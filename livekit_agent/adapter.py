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
import os
import re
import time
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
        # call_ids whose executor has started running (i.e. the request may have reached the backend).
        # The executor's path from task start to the backend call is synchronous, so a call that is
        # NOT in this set has provably not been dispatched and can be cancelled with no side effect.
        self._started: set = set()
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
        self._last_route_at: float = 0.0
        self._last_route_text: str = ""      # the previous committed turn, for late-fragment merging
        self._closed = False
        self._settle_task: Optional[asyncio.Task] = None
        self._interrupt_speech = interrupt_speech
        # Speech floor (VAD onset -> VAD end). No commit may happen inside this window: an STT final
        # received while the caller is still speaking is a FRAGMENT, not a finished turn. Committing
        # it was the main cause of the official live run's duplicate / stale / partial tool calls
        # (e.g. a search fired on "...2 bedrooms" before "actually 3" arrived). A hard cap bounds the
        # wait so a lost VAD-end event cannot strand a finished request forever.
        self._user_speaking = False
        self._speech_hard_cap_s = max(2.0, float(os.environ.get("TRIAGELINE_SPEECH_CAP_S", "15")))
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
        # Await cancelled work, but BOUND the wait: a task that ignores cancellation (a blocked
        # asyncio.to_thread, a wedged executor) must never hang teardown. A hung teardown is what
        # makes the LiveKit job miss its shutdown ack and get the whole worker killed ("job did not
        # ack shutdown in time"), which is what dropped rooms in the full live run.
        if pending:
            try:
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=5.0)
            except asyncio.TimeoutError:
                log.warning("stop(): %d task(s) did not finish within 5s; continuing teardown", len(pending))
        for t in (self._pump_task, self._run_task):
            if t:
                t.cancel()
        for t in (self._pump_task, self._run_task):
            if t:
                try:
                    await asyncio.wait_for(t, timeout=5.0)
                except asyncio.CancelledError:
                    pass
                except asyncio.TimeoutError:
                    log.warning("stop(): pump/run task did not finish within 5s; continuing teardown")

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
        """VAD onset of user speech (audit B-08): take the floor. Stop the agent's queued/playing
        speech so the caller can barge in, and CANCEL any pending commit — a fragment must not be
        executed while the caller is still mid-turn. A bounded safety timer is armed only if text is
        already buffered, so a lost VAD-end can never strand a finished request."""
        if self._closed:
            return
        self._user_speaking = True
        self._cancel_settle()
        if self._pending_final:
            # provisional safety: a false onset (breath/click) with no further real final must not
            # strand the buffered turn. A real continuation final cancels this (see on_user_final).
            self._arm_settle(self.max_settle_s)
        if self._interrupt_speech is not None:
            try:
                await self._interrupt_speech()
            except Exception as e:  # noqa: BLE001 - a TTS hiccup must never kill the session
                log.warning("interrupt_speech failed: %s", e)

    async def on_user_partial(self, text: str):
        """Interim STT transcript. An explicit correction marker ("actually", "no wait", "instead",
        "scratch that") while the agent is speaking cuts the agent's speech right away (fast path).
        It must NOT set the speech floor: the floor is owned by real VAD onset/end, otherwise a
        partial would mark the user as speaking with no matching end event."""
        if text and self.busy() and _CORRECTION_CUE.search(text) and self._interrupt_speech is not None:
            try:
                await self._interrupt_speech()
            except Exception as e:  # noqa: BLE001
                log.warning("interrupt_speech failed: %s", e)

    async def on_user_speech_end(self):
        """VAD end of a user speech segment: release the floor and commit the assembled turn."""
        if self._closed:
            return
        self._user_speaking = False
        if self._pending_final:
            self._arm_settle()

    async def on_user_final(self, text: str):
        """Final STT transcript for a user utterance.

        With `settle_s > 0` finals are buffered briefly and merged (see B-09); otherwise they are
        routed immediately (unit tests / offline replay). While VAD says the caller is still speaking,
        the final is buffered but NOT scheduled for commit — it is a fragment of the turn in
        progress. An EMPTY final (STT heard only noise) restarts the timer for anything buffered."""
        text = (text or "").strip()
        if self._closed:
            return
        if not text:
            if self._pending_final and self.settle_s > 0:
                # a noise final inside a speech segment keeps the bounded safety timer alive
                self._arm_settle(self.max_settle_s if self._user_speaking else None)
            return
        if self.settle_s <= 0:
            return await self._route_final(text)
        self._pending_final.append(text)
        if self._user_speaking:
            # a real (non-empty) continuation: the caller is still building this turn, so wait for
            # the VAD-end event to commit the assembled turn. The long safety timer only fires if
            # that end event is lost, so the buffer can never be stranded forever.
            self._arm_settle(self._speech_hard_cap_s)
            return
        self._arm_settle()

    def _cancel_settle(self):
        if self._settle_task and not self._settle_task.done():
            self._settle_task.cancel()
        self._settle_task = None

    def _arm_settle(self, delay: Optional[float] = None):
        """(Re)start the single commit timer for the buffered transcript."""
        self._cancel_settle()
        self._settle_task = asyncio.create_task(self._settle_then_route(delay))

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
            for task in (self._run_task, self._pump_task):
                if task and task.done():
                    raise RuntimeError("agent event loop stopped before completion")
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
        long when it still looks unfinished (speculative plan, nothing emitted). A COMPLETE plan
        wins even when the raw text ends mid-spelling — normalization finishes the id for it."""
        try:
            from agent import nlu
            tools = self.agent.tools
            norm = nlu.normalize_asr(text)
            ranked = nlu.score_tools(norm, tools)
            if ranked and ranked[0][0] >= 1.5 and not self.agent.pending_clarify:
                _, missing = nlu.build_args(tools[ranked[0][1]], norm, dict(self.agent.state["slots"]))
                if not missing:
                    return self.settle_s
                return self.max_settle_s
        except Exception:  # noqa: BLE001 - the speculative plan is advisory only
            pass
        return self.max_settle_s if turn_looks_unfinished(text) else self.settle_s

    async def _settle_then_route(self, delay: Optional[float] = None):
        # A dangling fragment (mid-spelled id, trailing verb) must never become a tool call, but
        # the wait is bounded by max_settle_s: the old loop kept re-arming while the text "looked
        # unfinished" with a hard 10 s cap, which produced 15-26 s reply latency on spelled ids.
        # Bounding it here means the commit is at most ~max_settle_s after the last final, and the
        # live endpointing/turn-detector (not this gate) decides the true end of turn.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (delay if delay is not None else self.max_settle_s)
        while True:
            wait = delay if delay is not None else self.settle_for(" ".join(self._pending_final))
            await asyncio.sleep(max(0.0, wait))
            if delay is not None or not self._pending_final:
                break
            text = " ".join(self._pending_final)
            # a complete plan (settle_for <= settle_s) commits even if a raw regex still thinks the
            # text is unfinished; otherwise keep merging until the bounded deadline.
            complete = self.settle_for(text) <= self.settle_s + 1e-9
            if complete or not turn_looks_unfinished(text) or loop.time() >= deadline:
                break
        # Floor guard: an ordinary settle must never fire while the caller is still speaking — the
        # buffered text is a fragment and the VAD-end event will re-arm the commit. Only the bounded
        # safety timer (delay is not None) may force a commit, and it then releases the stuck floor.
        if delay is None and self._user_speaking:
            return
        if delay is not None and self._user_speaking:
            log.warning("speech-floor hard cap reached; committing buffered turn")
            self._user_speaking = False
        text, self._pending_final = " ".join(self._pending_final), []
        if text:
            log.debug("commit turn (%d chars): %r", len(text), text)
            await self._route_final(text)

    async def _route_final(self, text: str):
        """If the agent has live work (a pending response, an in-flight tool call,
        or an unanswered turn), the user spoke *while* the agent was mid-task — the
        final transcript IS the barge-in utterance and goes through
        on_interruption()'s epoch-bump path. Otherwise it is an ordinary new turn.
        The classification of the interruption (revise / retract / switch) is
        entirely ParticipantAgent.on_interruption."""
        if self._closed:
            return
        if self.busy():
            await self.on_barge_in(text)
            return
        # A late fragment that only adds arguments ("on May 12", "under 200") OR carries a correction
        # marker ("actually a lamp", "no wait, savings") amends the call just made instead of starting
        # a second request. The PREVIOUS turn text is merged in so the re-plan sees the whole request
        # and can build the complete/corrected arguments — otherwise a correction fragment alone is
        # parsed in isolation and the pre-correction value is kept (audit: duplicate + stale-value calls).
        if (self._last_route_at and (time.time() - self._last_route_at) < 8.0
                and (_is_arg_continuation(text) or _CORRECTION_CUE.search(text))):
            merged = ((self._last_route_text + " ") if self._last_route_text else "") + text
            log.info("late continuation/correction, merging instead of re-issuing: %r", merged)
            await self.on_barge_in(merged)
            return
        self._last_route_at = time.time()
        self._last_route_text = text
        await self.in_q.put({"event_type": "user_speech_chunk",
                              "payload": {"text": text, "end_of_turn": True}})

    async def on_barge_in(self, text: str):
        """Explicit barge-in: user spoke while the agent had the floor.
        -> ParticipantAgent.on_interruption(), which bumps the epoch via
        .invalidate() (switch) or .revise() (slot change) and cancels any
        in-flight call the change actually affects (agent/agent.py:595-680).
        """
        if self._closed:
            return
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
            self._started.add(cid)
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
            self._started.discard(cid)

    async def _pump_outputs(self):
        try:
            while True:
                msg = await self.out_q.get()
                action = msg.get("action")
                payload = msg.get("payload") or {}
                if self._closed:
                    continue
                if action in ("filler_speech", "final_response", "clarification_request"):
                    try:
                        await self._speak(action, payload.get("text", ""))
                    except Exception as exc:
                        log.warning("speech delivery failed (%s); keeping tool pump alive", type(exc).__name__)
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
                    state_mod = self.agent.tools.get(
                        self._reverse_alias(self._issued.get(cid, "")), {}).get("kind") == "state_modifying"
                    not_dispatched = task is not None and not task.done() and cid not in self._started
                    if task is not None and (not state_mod or not_dispatched):
                        # read-only work is cancelled for real. A state-modifying call is cancelled for
                        # real ONLY if it has not been dispatched yet (no side effect is possible, and it
                        # never reaches the tool log); once dispatched it is left to finish so its (late)
                        # outcome is reconciled by the operation ledger instead of being silently lost
                        # (R03 / B-10). See docs/ARCHITECTURE.md "Cancellation trade-off".
                        task.cancel()
                    await self._tool_canceller(cid)
                    if state_mod and not_dispatched:
                        self._tool_tasks.pop(cid, None)
                        await self.in_q.put({"event_type": "tool_cancelled",
                                             "payload": {"call_id": cid, "confirmed": True}})
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


_ARG_CONTINUATION = re.compile(
    r"(?i)^\s*(?:(?:and|also|then|plus|uh|um|okay|ok|so)\s+)*"
    r"(?:on\s+(?:the\s+)?(?:\d|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
    r"mon|tue|wed|thu|fri|sat|sun|today|tonight|tomorrow)|"
    r"(?:under|below|less than|at most|up to|no more than)\s|"
    r"for\s+(?:\w+\s+)?(?:nights?|days?|people|guests)|\d+\s+(?:nights?|days?|people|guests)|"
    r"keep it\s|make it\s|(?:with\s+(?:a\s+)?)?(?:budget|max(?:imum)?)\s|"
    r"(?:the\s+)?(?:name|passenger)\s+is\s|in\s+(?:economy|business|first)\b)")


def _is_arg_continuation(text: str) -> bool:
    """A late fragment that only supplies ARGUMENTS for the call just made ("on May 12",
    "under 200", "for two nights", "keep it under 50") — amending, not a new request."""
    return bool(_ARG_CONTINUATION.match((text or "").strip()))


def turn_looks_unfinished(text: str) -> bool:
    """Dangling connective / filler / trailing comma or ellipsis: the speaker has more to say.
    Also unfinished: a mid-spelled identifier ("order ID is a", "...is a b c") — STT delivers
    spelled ids as separate finals, so the commit must hold until a digit closes the sequence.
    Also unfinished: a trailing intent verb with no object yet ("...hoping you could find",
    "then I need to") — committing there invents junk arguments (e.g. filter_name="i_need")."""
    t = (text or "").strip()
    if _UNFINISHED.search(t):
        return True
    if re.search(r"(?i)\b(?:find|book|track|update|change|check|search|get|look|cancel|modify|switch)\s*[.?!,:]?\s*$", t):
        return True
    m = list(re.finditer(r"(?i)\b(?:id|number|code|no\.?|#)\b", t))
    if m:
        tail = t[m[-1].end():].strip(" .,?!:;")
        tail_words = [w for w in re.findall(r"[A-Za-z0-9]+", tail)
                      if w.lower() not in ("is", "was", "it's", "its", "the", "a", "an", "number", "id", "code")]
        if tail_words and not any(ch.isdigit() for ch in tail):
            from agent import nlu as _nlu
            # only spelled letters / number words so far and no digit yet: the id is incomplete
            if all(len(w) == 1 or w.lower() in _nlu.WORD_NUM or w.lower() in ("double", "triple") for w in tail_words):
                return True
    return False


_CORRECTION_CUE = re.compile(
    r"\b(actually|no[, ]+wait|wait[, ]+no|instead|scratch that|i mean|never ?mind|hold on|stop)\b", re.I)


# --------------------------------------------------------------------------- LiveKit wiring
def attach_livekit_session(session, adapter: TriageAdapter, *, room_name: str = "unknown",
                           on_transcript: Optional[Callable[[Any], None]] = None,
                           on_user_state: Optional[Callable[[Any], None]] = None):
    """Optional real-LiveKit wiring. Imports `livekit` lazily so this module
    (and the adapter tests) work without the package installed. Callers that
    already have a `livekit.agents.voice.AgentSession` instance can use this
    instead of hand-rolling the event handlers.

    ONE handler per session event. Telemetry hooks (`on_transcript`, `on_user_state`) run
    synchronously first, inside the same handler, so the timeline sees the event before routing.

    Used by cascaded_agent.py. Event names verified against livekit-agents 1.x.
    """
    @session.on("user_input_transcribed")
    def _on_transcript(msg):
        if on_transcript is not None:
            try:
                on_transcript(msg)
            except Exception as e:  # noqa: BLE001 - telemetry must never block routing
                log.warning("transcript hook failed: %s", e)
        text, is_final = msg.transcript, msg.is_final
        if is_final:
            asyncio.create_task(adapter.on_user_final(text))
        else:
            asyncio.create_task(adapter.on_user_partial(text))

    # Barge-in on VAD speech onset (audit B-08). livekit-agents 1.x emits
    # `user_state_changed` with new_state == "speaking" when the user starts talking.
    @session.on("user_state_changed")
    def _on_user_state(ev):
        if on_user_state is not None:
            try:
                on_user_state(ev)
            except Exception as e:  # noqa: BLE001
                log.warning("user-state hook failed: %s", e)
        new, old = getattr(ev, "new_state", None), getattr(ev, "old_state", None)
        if new == "speaking":
            asyncio.create_task(adapter.on_user_speech_start())
        elif old == "speaking":
            asyncio.create_task(adapter.on_user_speech_end())

    return session
