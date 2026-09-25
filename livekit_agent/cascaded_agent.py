#!/usr/bin/env python3
"""
Cascaded Voice Agent: Silero VAD + OpenAI Whisper STT + OpenAI TTS, driven by
the EXISTING TriageLine interruption/epoch/cancellation/dedup engine.

Pipeline:
  User Audio -> Silero VAD -> OpenAI Whisper STT
      -> TriageAdapter (livekit_agent/adapter.py, UNMODIFIED)
         -> ParticipantAgent (agent/agent.py, UNMODIFIED): epoch/version
            handling, stale-work cancellation, revise/retract classification,
            duplicate-action dedup ledger
      -> FDB_TOOLS (livekit_agent/fdb_tools.py) executed via mock_apis.py
      -> OpenAI TTS -> Agent Audio

Fix note (docs/LIVEKIT_WIRING_FIX.md): a prior version of this file used
OpenAI gpt-4o for LLM tool-calling directly against mock_apis.py, completely
bypassing TriageAdapter/ParticipantAgent (Path A in
docs/LIVEKIT_AGENT_TECHNICAL_AUDIT.md). That LLM tool-calling path is REMOVED
here on purpose: keeping it running alongside the adapter would have created
a second, uncoordinated way to invoke state-changing tools with no epoch
tracking or dedup, defeating the point of wiring the adapter in at all. Voice
turn-taking is still real LiveKit (VAD + Whisper STT + TTS); language
understanding and tool selection are now entirely the existing
ParticipantAgent/nlu.py logic, the same as triage_livekit_agent.py already
does for the Triage Line brain.

Usage:
    python cascaded_agent.py dev
    python cascaded_agent.py console

Environment variables (in .env.local):
    LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
    OPENAI_API_KEY     - OpenAI API key (for STT + TTS)
"""

import os
import sys
import json
import logging
import time
import asyncio
from dotenv import load_dotenv

from livekit import agents
from livekit.agents import Agent, AgentSession, AgentServer

# Make both `agent.*` (ParticipantAgent) and `livekit_agent.*` (this package)
# importable regardless of cwd, the same way fdb_scenario_run.py does.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from livekit_agent.adapter import TriageAdapter, attach_livekit_session  # noqa: E402
from livekit_agent.fdb_tools import FDB_TOOLS  # noqa: E402

# Parse custom CLI args before LiveKit CLI
LATENCY_PROFILE = "instant"
if "--latency" in sys.argv:
    idx = sys.argv.index("--latency")
    if idx + 1 < len(sys.argv):
        LATENCY_PROFILE = sys.argv[idx + 1]
        sys.argv.pop(idx)
        sys.argv.pop(idx)

# Import mock APIs for tool execution -- this is the ONLY place tools are
# actually invoked now; it is called from tool_executor() below, which is
# itself only reachable through TriageAdapter/ParticipantAgent, not directly
# from LiveKit callbacks.
try:
    from mock_apis import MockAPIRegistry
    registry = MockAPIRegistry(latency_profile=LATENCY_PROFILE)
    print(f"API Backend running with '{LATENCY_PROFILE}' latency profile.")
except ImportError:
    logging.warning("mock_apis.py not found. Tools will be mocked or fail.")
    registry = None

env_path = os.path.join(os.path.dirname(__file__), ".env.local")
load_dotenv(env_path)

log = logging.getLogger("triageline.cascaded_agent")


# ---------------------------------------------------------------------------
# Latency Tracker (unchanged from the previous version of this file)
# ---------------------------------------------------------------------------
class LatencyTracker:
    def __init__(self):
        self.user_done_at = 0
        self.tool_start_at = 0
        self.tool_end_at = 0
        self.agent_start_at = 0
        self.query_received = False

    def reset(self):
        self.__init__()

    def log_breakdown(self, tool_name="", room_name="unknown"):
        if not self.user_done_at or not self.agent_start_at:
            return
        reasoning = (self.tool_start_at - self.user_done_at) if self.tool_start_at else 0
        execution = (self.tool_end_at - self.tool_start_at) if self.tool_start_at and self.tool_end_at else 0
        synthesis = (self.agent_start_at - (self.tool_end_at or self.user_done_at))
        total = self.agent_start_at - self.user_done_at

        report = f"\nLATENCY BREAKDOWN ({tool_name}) for room {room_name}:\n"
        report += f"  - Reasoning (ASR -> tool decision): {reasoning:.2f}s\n"
        if execution:
            report += f"  - Tool Execution (API):    {execution:.2f}s\n"
        report += f"  - Synthesis (Tool -> Spoken): {synthesis:.2f}s\n"
        report += f"  - TOTAL SEARCH LATENCY:      {total:.2f}s\n"

        metrics = {
            "room": room_name, "tool": tool_name,
            "reasoning": round(reasoning, 3), "execution": round(execution, 3),
            "synthesis": round(synthesis, 3), "total": round(total, 3),
            "agent_start_at": self.agent_start_at,
        }
        logging.info(report)
        logging.info(f"LATENCY_TRACK_JSON: {json.dumps(metrics)}")
        print(report)
        try:
            with open("/tmp/agent_heartbeat.log", "a") as f:
                f.write(report + "\n")
        except OSError:
            pass


def _log_tool_call(room_name: str, func_name: str, args: dict, t_start: float, t_end: float):
    try:
        with open("/tmp/agent_tool_calls.log", "a") as f:
            f.write(json.dumps({
                "room": room_name,
                "call": {"function": func_name, "args": args,
                         "timestamp_start": t_start, "timestamp_end": t_end},
            }) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Agent definition -- no LLM, no function-tool-calling. Tool selection comes
# entirely from ParticipantAgent (agent/agent.py + agent/nlu.py) via the
# adapter, same as triage_livekit_agent.py's TriageVoiceAgent.
# ---------------------------------------------------------------------------
class CascadedVoiceAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions="")


def build_cascaded_pipeline():
    """VAD + STT + TTS only. No LLM here on purpose -- see module docstring
    ("Fix note") for why the previous gpt-4o tool-calling step was removed."""
    from livekit.plugins import openai
    from livekit.plugins import silero

    vad = silero.VAD.load(min_speech_duration=0.05, min_silence_duration=0.55)
    stt = openai.STT(model="whisper-1", language="en")
    tts = openai.TTS(model="tts-1", voice="nova")
    return vad, stt, tts


# ---------------------------------------------------------------------------
# Agent server & session
# ---------------------------------------------------------------------------
server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: agents.JobContext):
    room_name = ctx.room.name
    print(f"!!! CASCADED AGENT JOINING ROOM: {room_name} !!!")

    vad, stt, tts = build_cascaded_pipeline()
    tracker = LatencyTracker()

    session = AgentSession(
        vad=vad,
        stt=stt,
        tts=tts,
        min_endpointing_delay=0.5,
        max_endpointing_delay=5.0,
    )

    # call_id -> cancelled. Best-effort: mock_apis.py's calls are short/sync
    # (run via asyncio.to_thread below), so we can't preempt one mid-flight;
    # this set prevents a *result* that arrives after cancellation from ever
    # reaching ParticipantAgent, which is the actual stale-result-emission
    # risk. (ParticipantAgent/adapter also drop stale call_ids independently
    # -- this is belt-and-suspenders at the execution boundary, not a
    # reimplementation of that logic.)
    _cancelled_calls: set[str] = set()

    async def tool_executor(call_id: str, api_name: str, args: dict) -> None:
        """The ONLY place FDB tools are invoked. Reachable exclusively through
        TriageAdapter's out_q -> ParticipantAgent decided to call `api_name`.
        cascaded_agent.py never calls registry.call() on its own."""
        tracker.tool_start_at = time.time()
        if registry is None:
            result = {"status": "error", "message": "mock_apis registry unavailable"}
        else:
            try:
                result = await asyncio.to_thread(registry.call, api_name, **args)
            except Exception as e:  # never let one bad tool call kill the session
                result = {"status": "error", "message": str(e)}
        tracker.tool_end_at = time.time()
        _log_tool_call(room_name, api_name, args, tracker.tool_start_at, tracker.tool_end_at)

        if call_id in _cancelled_calls:
            _cancelled_calls.discard(call_id)
            log.info("dropping result for cancelled call_id=%s (%s)", call_id, api_name)
            return
        await adapter.on_tool_completed(call_id, result, status=result.get("status", "ok"))

    async def tool_canceller(call_id: str) -> None:
        _cancelled_calls.add(call_id)

    async def speak(kind: str, text: str) -> None:
        if not text:
            return
        if kind == "final_response" and tracker.query_received and not tracker.agent_start_at:
            tracker.agent_start_at = time.time()
            tracker.log_breakdown(tool_name="fdb_tool", room_name=room_name)
            tracker.reset()
        session.say(text)

    adapter = TriageAdapter(tool_executor=tool_executor, tool_canceller=tool_canceller, speak=speak)
    await adapter.start(FDB_TOOLS)

    # This is the actual P0 fix: attach_livekit_session() was previously
    # defined in adapter.py but never called anywhere in the repo. Calling it
    # here routes real LiveKit STT transcript/barge-in events into
    # TriageAdapter -> ParticipantAgent instead of a hand-rolled/LLM path.
    attach_livekit_session(session, adapter, room_name=room_name)

    @session.on("user_input_transcribed")
    def on_user_input(msg: agents.voice.UserInputTranscribedEvent):
        logging.info(f"STT TRANSCRIPT: '{msg.transcript}' (is_final={msg.is_final})")
        print(f"  STT: '{msg.transcript}' (final={msg.is_final})")
        if msg.is_final and not tracker.query_received:
            tracker.user_done_at = time.time()
            tracker.query_received = True

    async def _teardown(*_args, **_kwargs) -> None:
        await adapter.stop()

    ctx.room.on("disconnected", lambda *a, **k: asyncio.create_task(_teardown()))
    ctx.add_shutdown_callback(_teardown)

    await session.start(room=ctx.room, agent=CascadedVoiceAgent())
    print("!!! CASCADED AGENT STARTED (Silero VAD + OpenAI Whisper STT + TriageAdapter/ParticipantAgent + OpenAI TTS) !!!")


if __name__ == "__main__":
    agents.cli.run_app(server)
