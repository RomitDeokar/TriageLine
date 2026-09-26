"""livekit_agent/adapter.py — thin translation layer between LiveKit Agents and
TriageLine's existing `agent.agent.ParticipantAgent`.

This file contains NO interruption *classification* or dedup logic of its own.
It does own the transport-level concurrency: tool calls run as independent
tasks (never blocking speech/cancellation), finals are optionally settled
before routing, and VAD/partial-transcript barge-in cuts agent speech early. Every
behavior listed below already lives in `agent/agent.py` + `agent/nlu.py` and is
covered by `tests/test_regressions.py` and adapter_tests/test_1..9; this module only translates LiveKit's
callback shapes into the event-queue shapes ParticipantAgent already consumes,
and translates ParticipantAgent's out_q actions into LiveKit calls. That keeps
one implementation instead of two forks that can drift.

Where each existing mechanism lives (read, not reimplemented):
  - epoch-guarded interruption classifier : ParticipantAgent.version (bumped in
    .invalidate() and at the end of .revise()); completions carry the version
    they started under and are dropped if stale (on_internal / still_valid).
  - revise / retract / switch classification : ParticipantAgent.on_interruption,
    .revise(), .retract() (agent/agent.py:588-680).
  - async tool orchestration : ParticipantAgent.call() / .cancel_where() —
    every call is tagged with self.version and a call_id; cancel_where() emits
    an out_q "cancel_tool" action for any in-flight call matching a predicate.
  - duplicate-action dedup ledger : ParticipantAgent.ops, keyed by
    op_key(api, args) = api + canonical-JSON(args) (agent/agent.py:194-230).

LiveKit callback points this adapter wires up (see cascaded_agent.py / livekit
Agent Session for the real names — this module talks to them through small
protocol shims so it can be unit-tested without the `livekit` package installed):

  - user speech PARTIAL transcript  -> on_user_partial()   (fast path: a
      correction cue while the agent is busy interrupts its speech at once)
  - VAD user-speech onset           -> on_user_speech_start() (stops agent speech)
  - user speech FINAL transcript    -> on_user_final()     -> "user_speech_chunk"
      (end_of_turn=True) if no turn is in flight, else -> on_interruption()
  - barge-in / interruption event   -> on_barge_in()       -> "interruption"
  - tool-call issuing               -> out_q "tool_call" action, forwarded to
      the caller-supplied `tool_executor` (in LiveKit: the AssistantFnc method)
  - session/task cancellation       -> out_q "cancel_tool" action, forwarded to
      `tool_canceller` (in LiveKit: cancel the asyncio.Task running the
      function-tool call, e.g. via FunctionCall.cancel() / task.cancel())

Usage (framework-agnostic — see attach_livekit_session() below for the real
LiveKit wiring):

    adapter = TriageAdapter(tool_executor=my_exec, tool_canceller=my_cancel,
                             speak=my_speak)
    await adapter.start(tools_manifest)
    await adapter.on_user_final("book a flight to Boston")
    ...
    await adapter.on_tool_completed(call_id, result, status="ok")
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable, Dict, Optional

from agent.agent import ParticipantAgent

log = logging.getLogger("triageline.livekit_adapter")

ToolExecutor = Callable[[str, str, Dict[str, Any]], Awaitable[None]]
# (call_id, api_name, args) -> None; executor is responsible for eventually
# calling adapter.on_tool_completed(call_id, ...) — exactly mirroring how the
# harness driver feeds "tool_result" back into ParticipantAgent today.

ToolCanceller = Callable[[str], Awaitable[None]]
# (call_id) -> None; best-effort — cancelling a call whose result already
# arrived is a no-op both in the harness and here.

Speak = Callable[[str, str], Awaitable[None]]
# (kind, text) -> None; kind is "filler_speech" or "final_response".


class TriageAdapter:
    """Wraps one ParticipantAgent per LiveKit session/room. Owns no state of
    its own beyond the plumbing queues and the caller's callbacks — the epoch,
    the in-flight ledger and the op ledger all live on `self.agent`."""

    def __init__(self, *, tool_executor: ToolExecutor, tool_canceller: ToolCanceller,
                 speak: Speak, live: bool = True, settle_s: float = 0.0,
                 interrupt_speech: Optional[Callable[[], Awaitable[None]]] = None,
                 load_models: bool = False, max_settle_s: Optional[float] = None):
        self.in_q: asyncio.Queue = asyncio.Queue()
        self.out_q: asyncio.Queue = asyncio.Queue()
        self.agent = ParticipantAgent(self.in_q, self.out_q, live=live)
        self._tool_executor = tool_executor
        self._tool_canceller = tool_canceller
        self._speak = speak
        self._pump_task: Optional[asyncio.Task] = None
        self._run_task: Optional[asyncio.Task] = None
        # call_id -> api_name, so on_tool_completed can log without the caller
        # having to remember what it dispatched.
        self._issued: Dict[str, str] = {}
        # call_id -> asyncio.Task running the executor. Tool calls run as their
        # own tasks so a slow tool NEVER blocks filler speech, cancel_tool or new
        # tool calls (audit B-05: previously the pump awaited the executor inline).
        self._tool_tasks: Dict[str, asyncio.Task] = {}
        # Utterance settling (audit B-09): LiveKit endpointing can split one spoken
        # request with a long hesitation into several finals. Finals arriving within
        # `settle_s` of each other are merged before they are routed, so the second
        # half of a sentence is never mistaken for a barge-in on the first half.
        self.settle_s = max(0.0, float(settle_s))
        # speculate-then-commit (C2): the running transcript of the whole turn is re-planned at every
        # fragment; a turn that still looks unfinished (dangling connective / filler, or a ranked tool
        # whose required args are not all present yet) waits up to `max_settle_s` before committing.
        self.max_settle_s = max(self.settle_s, float(max_settle_s if max_settle_s is not None else self.settle_s * 2))
        self._pending_final: list = []
        self._closed = False
        self._settle_task: Optional[asyncio.Task] = None
        self._interrupt_speech = interrupt_speech
        # FDB/LiveKit path never uses local ASR/CLIP (LiveKit does STT), so the
        # 700 MB model load is skipped unless explicitly requested (audit B-11).
        self._load_models = load_models

    # ------------------------------------------------------------- lifecycle
    async def start(self, tools_manifest: Dict[str, Any]):
        if self._load_models:
            await self.agent.setup()
        await self.in_q.put({"event_type": "tool_manifest", "payload": {"tools": tools_manifest}})
        self._run_task = asyncio.create_task(self.agent.run())
        self._pump_task = asyncio.create_task(self._pump_outputs())

    async def stop(self):
        self.close()
        if self._settle_task:
            self._settle_task.cancel()
        pending = list(self._tool_tasks.values()) + list(self.agent.tasks)
        if self._settle_task:
            pending.append(self._settle_task)
        for t in pending:
            t.cancel()
        self._tool_tasks.clear()
        if pending:   # await them so no "Task was destroyed but it is pending" (B14)
            await asyncio.gather(*pending, return_exceptions=True)
        for t in (self._pump_task, self._run_task):
            if t:
                t.cancel()
        for t in (self._pump_task, self._run_task):
            if t:
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    @property
    def epoch(self) -> int:
        """The current task-version / epoch. Bumped by ParticipantAgent itself
        on interruption (.invalidate() / .revise()) — never set here."""
        return self.agent.version

    # ------------------------------------------------------ inbound (LiveKit -> agent)
    def busy(self) -> bool:
        """True while the agent owns live work for the current request: a tool in
        flight, or a turn it has not answered yet."""
        return bool(self.agent.planner_pending) or ((bool(self.agent.inflight) or not self.agent.answered)
                                                   and self.agent.last_api is not None)

    async def on_user_speech_start(self):
        """VAD onset of user speech (audit B-08). If the agent is talking or working,
        stop its queued/playing speech immediately so the user can barge in; the
        actual interruption semantics (revise / retract / switch) are decided when
        the final transcript arrives."""
        if self._interrupt_speech is not None:
            try:
                await self._interrupt_speech()
            except Exception as e:  # noqa: BLE001 - a TTS hiccup must never kill the session
                log.warning("interrupt_speech failed: %s", e)

    async def on_user_partial(self, text: str):
        """Interim STT transcript. An explicit correction marker ("actually",
        "no wait", "instead", "scratch that") while the agent is speaking cuts the
        agent's speech right away (fast path) — the correction itself is applied
        once the final transcript arrives, so nothing is decided on a partial."""
        if text and self.busy() and _CORRECTION_CUE.search(text):
            await self.on_user_speech_start()

    async def on_user_final(self, text: str):
        """Final STT transcript for a user turn.

        With `settle_s > 0` finals are buffered briefly and merged (see B-09);
        otherwise they are routed immediately (unit tests / offline replay)."""
        text = (text or "").strip()
        if not text or self._closed:
            return
        if self.settle_s <= 0:
            return await self._route_final(text)
        self._pending_final.append(text)
        if self._settle_task and not self._settle_task.done():
            self._settle_task.cancel()
        self._settle_task = asyncio.create_task(self._settle_then_route())

    async def flush(self):
        """Route any buffered final immediately (end of stream). After close() nothing is routed (B8)."""
        if self._settle_task and not self._settle_task.done():
            self._settle_task.cancel()
        if self._pending_final and not self._closed:
            text, self._pending_final = " ".join(self._pending_final), []
            await self._route_final(text)

    async def wait_idle(self, timeout: float = 30.0):
        """Drain queued events, planning and chained tools; do not truncate slow offline work."""
        await self.flush()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        quiet = 0
        while quiet < 2:
            active = (not self.in_q.empty() or not self.out_q.empty() or self._tool_tasks
                      or self.agent.tasks or self.agent.inflight or self.agent.planner_pending
                      or self._pending_final)
            quiet = 0 if active else quiet + 1
            if loop.time() >= deadline:
                raise TimeoutError("agent did not finish planning/tool execution before replay timeout")
            await asyncio.sleep(0.01)

    def close(self):
        """Room gone: drop buffered speech, never issue a late tool call (B8)."""
        self._closed = True
        self._pending_final = []
        if self._settle_task and not self._settle_task.done():
            self._settle_task.cancel()

    def settle_for(self, text: str) -> float:
        """Commit delay for the running transcript: short when the turn plans to a complete call,
        long when it still looks unfinished (speculative plan, nothing emitted)."""
        if turn_looks_unfinished(text):
            return self.max_settle_s
        try:
            from agent import nlu
            tools = self.agent.tools
            norm = nlu.normalize_asr(text)
            ranked = nlu.score_tools(norm, tools)
            if ranked and ranked[0][0] >= 1.5 and not self.agent.pending_clarify:
                _, missing = nlu.build_args(tools[ranked[0][1]], norm, dict(self.agent.state["slots"]))
                if missing:
                    return self.max_settle_s
        except Exception:  # noqa: BLE001 - the speculative plan is advisory only
            pass
        return self.settle_s

    async def _settle_then_route(self):
        try:
            await asyncio.sleep(self.settle_for(" ".join(self._pending_final)))
        except asyncio.CancelledError:
            return
        text, self._pending_final = " ".join(self._pending_final), []
        if text:
            await self._route_final(text)

    async def _route_final(self, text: str):
        """If the agent has live work (a pending response, an in-flight tool call,
        or an unanswered turn), the user spoke *while* the agent was mid-task — the
        final transcript IS the barge-in utterance and goes through
        on_interruption()'s epoch-bump path. Otherwise it is an ordinary new turn.
        The classification of the interruption (revise / retract / switch) is
        entirely ParticipantAgent.on_interruption."""
        if self.busy():
            await self.on_barge_in(text)
            return
        await self.in_q.put({"event_type": "user_speech_chunk",
                              "payload": {"text": text, "end_of_turn": True}})

    async def on_barge_in(self, text: str):
        """Explicit barge-in: user spoke while the agent had the floor.
        -> ParticipantAgent.on_interruption(), which bumps the epoch via
        .invalidate() (switch) or .revise() (slot change) and cancels any
        in-flight call the change actually affects (agent/agent.py:595-680).
        """
        await self.in_q.put({"event_type": "interruption", "payload": {"text": text}})

    async def on_tool_completed(self, call_id: str, result: Dict[str, Any], status: str = "ok"):
        """Feed a finished (or failed) tool call back in. ParticipantAgent
        drops this silently if call_id was already popped by cancel_where()
        (agent/agent.py:692-696) — that's the epoch-guard in action: a stale
        cancelled call's result is never grounded on."""
        self._issued.pop(call_id, None)
        await self.in_q.put({"event_type": "tool_result",
                              "payload": {"call_id": call_id, "result": result, "status": status}})

    # ------------------------------------------------------ outbound (agent -> LiveKit)
    async def _run_tool(self, cid: str, api: str, args: Dict[str, Any]):
        try:
            await self._tool_executor(cid, api, args)
        except asyncio.CancelledError:
            log.info("tool task cancelled: %s (%s)", cid, api)
            raise
        except Exception as e:  # noqa: BLE001 - executor bug -> structured error, never a stranded call
            log.exception("tool executor failed for %s", api)
            await self.on_tool_completed(cid, {"status": "error", "error": "error", "message": str(e)},
                                         status="error")
        finally:
            self._tool_tasks.pop(cid, None)

    async def _pump_outputs(self):
        try:
            while True:
                msg = await self.out_q.get()
                action = msg.get("action")
                payload = msg.get("payload") or {}
                if action in ("filler_speech", "final_response", "clarification_request"):
                    await self._speak(action, payload.get("text", ""))
                elif action == "tool_call":
                    cid, api, args = payload["call_id"], payload["api_name"], payload["args"]
                    self._issued[cid] = api
                    # Non-blocking (B-05): each call is its own task, so fillers,
                    # cancellations and further calls keep flowing while it runs.
                    # ParticipantAgent.call() already tagged it with the epoch.
                    self._tool_tasks[cid] = asyncio.create_task(self._run_tool(cid, api, args))
                elif action == "cancel_tool":
                    cid = payload["call_id"]
                    task = self._tool_tasks.get(cid)
                    if task is not None and self.agent.tools.get(
                            self._reverse_alias(self._issued.get(cid, "")), {}).get("kind") != "state_modifying":
                        # read-only work is cancelled for real; a state-modifying call
                        # is left to finish so its (late) outcome is reconciled by the
                        # operation ledger instead of being silently lost (R03 / B-10)
                        task.cancel()
                    await self._tool_canceller(cid)
                else:
                    log.debug("unhandled out_q action: %s", action)
        except asyncio.CancelledError:
            return

    def _reverse_alias(self, ext: str) -> str:
        for internal, external in (self.agent.tool_alias or {}).items():
            if external == ext:
                return internal
        return ext


_UNFINISHED = re.compile(r"(?i)(?:\b(?:and|or|but|then|so|to|for|of|the|a|an|my|with|is|was|um+|uh+|like|"
                         r"actually|wait|i mean|let me (?:see|find|check)|hold on)\s*[,.…]*|\.{2,}|…|,)\s*$")


def turn_looks_unfinished(text: str) -> bool:
    """Dangling connective / filler / trailing comma or ellipsis: the speaker has more to say."""
    return bool(_UNFINISHED.search((text or "").strip()))


_CORRECTION_CUE = re.compile(
    r"\b(actually|no[, ]+wait|wait[, ]+no|instead|scratch that|i mean|never ?mind|hold on|stop)\b", re.I)


# --------------------------------------------------------------------------- LiveKit wiring
def attach_livekit_session(session, adapter: TriageAdapter, *, room_name: str = "unknown"):
    """Optional real-LiveKit wiring. Imports `livekit` lazily so this module
    (and the adapter tests) work without the package installed. Callers that
    already have a `livekit.agents.voice.AgentSession` instance can use this
    instead of hand-rolling the event handlers.

    Used by cascaded_agent.py. Event names verified against livekit-agents 1.x.
    """
    @session.on("user_input_transcribed")
    def _on_transcript(msg):
        text, is_final = msg.transcript, msg.is_final
        if is_final:
            asyncio.create_task(adapter.on_user_final(text))
        else:
            asyncio.create_task(adapter.on_user_partial(text))

    # Barge-in on VAD speech onset (audit B-08). livekit-agents 1.x emits
    # `user_state_changed` with new_state == "speaking" when the user starts talking.
    @session.on("user_state_changed")
    def _on_user_state(ev):
        if getattr(ev, "new_state", None) == "speaking":
            asyncio.create_task(adapter.on_user_speech_start())

    return session
