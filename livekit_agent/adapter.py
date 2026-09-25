"""livekit_agent/adapter.py — thin translation layer between LiveKit Agents and
TriageLine's existing `agent.agent.ParticipantAgent`.

This file contains NO interruption/classification/dedup logic of its own. Every
behavior listed below already lives in `agent/agent.py` + `agent/nlu.py` and is
covered by `tests/test_regressions.py`; this module only translates LiveKit's
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

  - user speech PARTIAL transcript  -> on_user_partial()   (fast-path only; no
      agent event today — ParticipantAgent has no partial-transcript hook, so
      partials are currently a no-op pass-through. Kept as an explicit seam.)
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
                 speak: Speak, live: bool = True):
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

    # ------------------------------------------------------------- lifecycle
    async def start(self, tools_manifest: Dict[str, Any]):
        await self.agent.setup()
        await self.in_q.put({"event_type": "tool_manifest", "payload": {"tools": tools_manifest}})
        self._run_task = asyncio.create_task(self.agent.run())
        self._pump_task = asyncio.create_task(self._pump_outputs())

    async def stop(self):
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
    async def on_user_partial(self, text: str):
        """Interim STT transcript. ParticipantAgent's fast path has no partial
        hook (it buffers/turns on end_of_turn, and treats barge-in during an
        active response as an explicit "interruption" event instead) so this
        is intentionally a no-op today. Kept as a named seam so a future
        partial-driven early-cancel policy has one obvious place to land
        instead of being invented ad hoc in cascaded_agent.py."""
        return

    async def on_user_final(self, text: str):
        """Final STT transcript for a user turn.

        If the agent has nothing in flight and hasn't already answered this
        turn, this is an ordinary new turn. If it does have live work (a
        pending response, an in-flight tool call, or an unanswered turn),
        LiveKit's own VAD/endpointing already means the user spoke *while*
        the agent was mid-task — i.e. this final transcript IS the barge-in
        utterance, so it must go through on_interruption()'s epoch-bump path,
        not a fresh turn. This routing decision is the only thing this
        adapter decides; the classification of what KIND of interruption it
        is (revise/retract/switch) is entirely ParticipantAgent.on_interruption.
        """
        text = text or ""
        busy = bool(self.agent.inflight) or not self.agent.answered
        if busy and self.agent.last_api is not None:
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
    async def _pump_outputs(self):
        try:
            while True:
                msg = await self.out_q.get()
                action = msg.get("action")
                payload = msg.get("payload") or {}
                if action in ("filler_speech", "final_response", "clarification_request"):
                    kind = "final_response" if action != "filler_speech" else "filler_speech"
                    await self._speak(action, payload.get("text", ""))
                elif action == "tool_call":
                    cid, api, args = payload["call_id"], payload["api_name"], payload["args"]
                    self._issued[cid] = api
                    # new tool call always carries the CURRENT epoch because
                    # ParticipantAgent.call() reads self.version at call time
                    # (agent/agent.py:224) — nothing to tag here.
                    await self._tool_executor(cid, api, args)
                elif action == "cancel_tool":
                    cid = payload["call_id"]
                    await self._tool_canceller(cid)
                else:
                    log.debug("unhandled out_q action: %s", action)
        except asyncio.CancelledError:
            return


# --------------------------------------------------------------------------- LiveKit wiring
def attach_livekit_session(session, adapter: TriageAdapter, *, room_name: str = "unknown"):
    """Optional real-LiveKit wiring. Imports `livekit` lazily so this module
    (and the adapter tests) work without the package installed. Callers that
    already have a `livekit.agents.voice.AgentSession` instance can use this
    instead of hand-rolling the event handlers.

    NOT exercised by the manual test scripts in this phase (no livekit-agents
    in this sandbox — see SETUP.md); wiring documented and left for the smoke
    test in TASK 4/5's follow-up once credentials are available.
    """
    @session.on("user_input_transcribed")
    def _on_transcript(msg):
        text, is_final = msg.transcript, msg.is_final
        if is_final:
            asyncio.create_task(adapter.on_user_final(text))
        else:
            asyncio.create_task(adapter.on_user_partial(text))

    # Barge-in: LiveKit AgentSession fires speech_created/user_state_changed
    # around VAD-detected speech while the agent is talking; the exact event
    # name has moved across livekit-agents versions (SETUP.md notes 1.3 vs
    # 1.8.3 drift), so this hooks the documented 1.x event and falls back to
    # treating the next final transcript as a barge-in via on_user_final's own
    # `busy` check above — that fallback is what on_user_final already does,
    # so barge-in detection degrades gracefully even if this handler name is
    # wrong for a given SDK point release.
    if hasattr(session, "on"):
        try:
            @session.on("user_state_changed")
            def _on_user_state(ev):
                if getattr(ev, "new_state", None) == "speaking":
                    pass  # on_user_final's busy-check covers this; see above
        except Exception:
            log.warning("could not attach barge-in hook for this livekit-agents version")

    return session
