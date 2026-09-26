"""Triage Line brain: wires legacy/core (dialogue -> deliberation -> commit
state machine) in as the "LLM" seam of the legacy DialogueEngine, so the
SAME shared LiveKit agent shell used for FDB-v3 (livekit_agent/) can host
the Triage Line roadside/incident-triage use case as a second, independent
call flow -- not a second LiveKit agent process.

Architecture (per the extension brief):

    LiveKit
        |
    shared agent/coordination layer   (triage_livekit_agent.py: AgentServer,
        |                              VAD/STT/TTS, same shell as cascaded_agent.py)
    Triage Line dialogue               (legacy core.dialogue.DialogueEngine)
        |
    deliberation                       (legacy core.deliberation.DeliberationEngine)
        |
    commit state machine               (legacy core.commit.CommitStateMachine)
        |
    dispatch/action                    (TriageBrainLLM._propose_action below --
                                         still a logged/mocked "dispatch", see
                                         EXTENSION_DEMO_TRANSCRIPT.md)

Nothing in legacy/core is modified to make this work except one bug fix in
core/deliberation/intents.py (location extraction now takes the LAST
matching mention, not the first -- required for corrections to actually
override a previously stated location; see that file's docstring). Every
class in legacy/core/dialogue, legacy/core/deliberation, and
legacy/core/commit is reused unmodified via its existing constructor
injection points (EventBus, ConversationTurn, the provider Protocols).
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Optional

# legacy/ uses bare `core.X` / `providers.X` imports (see legacy/tests/_run_all.py
# for the same convention) -- put legacy/ on sys.path rather than editing any
# legacy import statement.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LEGACY_ROOT = os.path.join(_REPO_ROOT, "legacy")
if _LEGACY_ROOT not in sys.path:
    sys.path.append(_LEGACY_ROOT)

from core.commit.state_machine import CommitState, CommitStateMachine  # noqa: E402
from core.deliberation.engine import (  # noqa: E402
    DeliberationBackedInterruptionStrategy,
    DeliberationEngine,
    FALLBACK_REQUEST_MORE_INFO,
)
from core.dialogue.engine import DialogueEngine  # noqa: E402
from core.event_bus import EventBus  # noqa: E402
from providers.interfaces import (  # noqa: E402
    ActionProposal,
    AudioIO,
    ConversationTurn,
    LLMProvider,
    LLMResponse,
    STTProvider,
    TranscriptEvent,
    TTSProvider,
)

# Confirmation parsing (audit E-01..E-03). Word-bounded matching only -- "yesterday" is
# not "yes" -- and precedence is: correction > negation > affirmation. A caller who says
# "yes, but actually I'm on Highway 12" is correcting, not confirming.
_AFFIRMATIVE_RE = re.compile(
    r"\b(yes|yeah|yep|yup|sure|confirm(?:ed)?|go ahead|do it|please do|correct|that'?s right|affirmative|"
    r"send it|dispatch it|ok(?:ay)?)\b", re.I)
_NEGATIVE_RE = re.compile(
    r"\b(no|nope|nah|don'?t|do not|never|cancel|stop|wait|hold on|hang on|not yet|negative|abort)\b", re.I)
_CORRECTION_RE = re.compile(
    r"\b(actually|but|instead|i mean|i meant|correction|wrong|not there|scratch that|sorry)\b", re.I)

CONFIRM, DECLINE, CORRECTION, OTHER = "confirm", "decline", "correction", "other"


def classify_confirmation(text: str) -> str:
    """Classify a caller reply to a pending confirmation prompt.

    - CORRECTION: carries a correction marker or new facts after an affirmation
      ("yes, but actually I'm on Highway 12") -> re-deliberate, never confirm.
    - DECLINE: any negation ("do not confirm", "no, don't send it", "not yet").
    - CONFIRM: a clean affirmation with no negation and no correction.
    """
    t = (text or "").strip()
    if not t:
        return OTHER
    neg = bool(_NEGATIVE_RE.search(t))
    aff = bool(_AFFIRMATIVE_RE.search(t))
    corr = bool(_CORRECTION_RE.search(t))
    if corr and (aff or not neg):
        return CORRECTION
    if neg:
        return DECLINE
    if aff:
        return CONFIRM
    return OTHER


def _is_affirmative(text: str) -> bool:  # kept for backwards compatibility
    return classify_confirmation(text) == CONFIRM


def _is_negative(text: str) -> bool:  # kept for backwards compatibility
    return classify_confirmation(text) == DECLINE


def _incident_key(action_type: str, payload: dict) -> str:
    """Idempotency key for a real-world side effect (audit E-04): one dispatch per
    (action type, normalised location) per call, no matter how often it is re-requested."""
    loc = re.sub(r"[^a-z0-9]+", " ", str(payload.get("location", "")).lower()).strip()
    return f"{action_type}|{loc}"


def _confirmation_prompt(record) -> str:
    facts = record.known_facts
    if record.chosen_option == "dispatch_tow":
        location = facts.get("location", "your location")
        return f"I'll dispatch a tow truck to {location}. Should I go ahead?"
    if record.chosen_option == "escalate_emergency":
        location = facts.get("location", "your location")
        reason = facts.get("reason", "an emergency")
        return (
            f"This sounds like an emergency ({reason}) at {location}. "
            "I'm escalating to emergency services now -- confirm?"
        )
    if record.chosen_option == "close_case":
        return "Sounds good -- should I close out this case?"
    return f"I'd like to proceed with {record.chosen_option}. Confirm?"


@dataclass
class DispatchLogEntry:
    """A "dispatch" is logged, never actually placed anywhere real.

    See EXTENSION_DEMO_TRANSCRIPT.md's "what is simplified" section --
    there is no real tow-dispatch, geocoding, or emergency-services API
    in this environment. This log is what a real dispatch integration
    would replace.
    """

    call_id: str
    action_id: str
    action_type: str
    payload: dict[str, Any]


class TriageBrainLLM(LLMProvider):
    """Implements legacy's `LLMProvider` seam using deliberation + commit,
    instead of an actual language model.

    This is deliberately injected into DialogueEngine as its `llm` --
    DialogueEngine already only depends on the LLMProvider *interface*
    (never a concrete provider), so nothing in core/dialogue/engine.py
    needed to change for the brain behind it to become "run a
    deliberation pass and manage a commit lifecycle" instead of "call an
    LLM API". This preserves the same seam DeliberationBackedInterruptionStrategy
    already uses for barge-in resolution (both plug into the pre-existing
    dialogue <-> deliberation seam described in core/deliberation/engine.py).
    """

    def __init__(
        self,
        call_id: str,
        deliberation: DeliberationEngine,
        commit: CommitStateMachine,
        dispatch_log: Optional[list[DispatchLogEntry]] = None,
    ) -> None:
        self._call_id = call_id
        self._deliberation = deliberation
        self._commit = commit
        self._dispatch_log = dispatch_log if dispatch_log is not None else []
        # The one action currently awaiting explicit caller confirmation,
        # if any. Confirmation safety (FINALIZED only via CommitStateMachine
        # .confirm()) is enforced by CommitStateMachine itself; this is only
        # bookkeeping for "what is the caller answering yes/no to right now".
        self.pending_action_id: Optional[str] = None
        self.pending_decision_id: Optional[str] = None
        # Every DeliberationRecord and CommitRecord ever produced this call,
        # in order -- used by the demo/tests to assert on the full trace
        # without reaching into engine internals.
        self.deliberation_trace: list[Any] = []
        self.commit_trace: list[Any] = []
        # incident idempotency key -> action_id that already FINALIZED (E-04)
        self.finalized_incidents: dict[str, str] = {}
        # set by TriageCallSession.teardown(); no confirmation is accepted after it (E-06)
        self.closed = False

    async def respond(self, context: list[ConversationTurn]) -> LLMResponse:
        caller_text = context[-1].text if context and context[-1].speaker == "caller" else ""

        if self.closed:
            return LLMResponse(text="This call has ended, so I can't take any further action on it.",
                               intent="closed")

        # --- confirmation / abort handling for a pending action ---
        if self.pending_action_id is not None:
            if self._commit.get(self.pending_action_id).is_terminal:
                # resolved elsewhere (teardown / supersession): never confirm a dead action
                self.pending_action_id = None
                self.pending_decision_id = None
                return LLMResponse(text="That earlier request is no longer active. What do you need now?",
                                   intent="stale")
            kind = classify_confirmation(caller_text)
            if kind == CONFIRM:
                record = await self._commit.confirm(self.pending_action_id, confirmed_by="caller")
                self.commit_trace.append(record)
                self._log_dispatch(record)
                self.pending_action_id = None
                self.pending_decision_id = None
                return LLMResponse(
                    text=f"Confirmed -- {record.action_type.replace('_', ' ')} is finalized.",
                    intent="confirm",
                )
            if kind == DECLINE:
                record = await self._commit.abort(self.pending_action_id, reason="caller_declined")
                self.commit_trace.append(record)
                self.pending_action_id = None
                self.pending_decision_id = None
                return LLMResponse(
                    text="Okay, I won't do that. What would you like instead?",
                    intent="abort",
                )
            # Anything else while a confirmation is pending is treated as
            # new information (e.g. a location correction), not an answer.
            # Fall through to re-deliberate on the full, updated context.

        # --- full deliberation pass (re-deliberation is automatic: this
        # always re-evaluates the whole context, which is exactly what
        # DeliberationEngine.deliberate / the intent handlers are built to
        # do -- see core/deliberation/intents.py's _caller_text docstring) ---
        record = await self._deliberation.deliberate(self._call_id, context)
        self.deliberation_trace.append(record)

        # If this pass superseded an earlier one in the same decision
        # group, any non-terminal commit action tied to the SUPERSEDED
        # decision is stale and must be cancelled -- this is what
        # prevents dispatching to a location the caller has since
        # corrected (section 6 of the extension brief).
        if record.supersedes:
            await self._cancel_stale_actions(record.supersedes)

        if not record.resolved:
            if record.fallback_action == FALLBACK_REQUEST_MORE_INFO:
                missing = ", ".join(record.uncertainties) or "a few more details"
                text = f"I need a bit more information -- can you tell me {missing}?"
            else:
                text = "I'm not confident enough to act on that automatically, so I'm escalating this to a human dispatcher."
            return LLMResponse(text=text, intent=record.intent, needs_more_info=True)

        key = _incident_key(record.chosen_option, dict(record.known_facts))
        if key in self.finalized_incidents:
            # the same incident was already dispatched on this call: never do it twice (E-04)
            where = record.known_facts.get("location", "that location")
            return LLMResponse(
                text=f"That's already been taken care of -- {record.chosen_option.replace('_', ' ')} "
                     f"to {where} is confirmed. I won't send a second one.",
                intent="duplicate_suppressed",
            )

        action_record = await self._commit.propose(
            call_id=self._call_id,
            decision_id=record.decision_id,
            action_type=record.chosen_option,
            action_payload=dict(record.known_facts),
        )
        self.commit_trace.append(action_record)
        self.pending_action_id = action_record.action_id
        self.pending_decision_id = record.decision_id

        return LLMResponse(
            text=_confirmation_prompt(record),
            intent=record.intent,
            action_proposal=ActionProposal(action_type=record.chosen_option, details=dict(record.known_facts)),
        )

    async def _cancel_stale_actions(self, superseded_decision_id: str) -> None:
        for action in self._commit.list_for_call(self._call_id):
            if action.decision_id != superseded_decision_id or action.is_terminal:
                continue
            if action.current_state == CommitState.PROPOSED:
                await self._commit.request_confirmation(action.action_id)
            record = await self._commit.abort(
                action.action_id, reason="superseded_by_redeliberation"
            )
            self.commit_trace.append(record)
            if action.action_id == self.pending_action_id:
                self.pending_action_id = None
                self.pending_decision_id = None

    def _log_dispatch(self, commit_record) -> None:
        if commit_record.current_state == CommitState.FINALIZED:
            self.finalized_incidents[_incident_key(commit_record.action_type,
                                                   dict(commit_record.action_payload))] = commit_record.action_id
            self._dispatch_log.append(
                DispatchLogEntry(
                    call_id=self._call_id,
                    action_id=commit_record.action_id,
                    action_type=commit_record.action_type,
                    payload=dict(commit_record.action_payload),
                )
            )


class TriageCallSession:
    """One Triage Line call: owns the EventBus + DialogueEngine +
    DeliberationEngine + CommitStateMachine for that call, wired together.

    Exists so both the real LiveKit entrypoint (triage_livekit_agent.py)
    and the offline demo/tests drive the exact same object graph -- there
    is only one code path that assembles the legacy stack, not one for
    "real" and a different one for tests.
    """

    def __init__(
        self,
        call_id: str,
        stt: STTProvider,
        tts: TTSProvider,
        audio_io: AudioIO,
        repository: Any = None,
    ) -> None:
        self.call_id = call_id
        self.bus = EventBus()
        self.deliberation = DeliberationEngine(self.bus, repository=repository)
        self.commit = CommitStateMachine(self.bus, repository=repository)
        self.dispatch_log: list[DispatchLogEntry] = []
        self.brain = TriageBrainLLM(call_id, self.deliberation, self.commit, self.dispatch_log)
        self.dialogue = DialogueEngine(
            call_id=call_id,
            stt=stt,
            llm=self.brain,
            tts=tts,
            audio_io=audio_io,
            bus=self.bus,
            interruption_strategy=DeliberationBackedInterruptionStrategy(self.deliberation),
        )

    async def on_final_transcript(self, text: str, speaker: str = "caller") -> None:
        await self.dialogue.handle_transcript(
            TranscriptEvent(speaker=speaker, text=text, is_final=True, timestamp_ms=self.dialogue._clock_ms())
        )

    async def on_partial_transcript(self, text: str, speaker: str = "caller") -> None:
        await self.dialogue.handle_transcript(
            TranscriptEvent(speaker=speaker, text=text, is_final=False, timestamp_ms=self.dialogue._clock_ms())
        )

    async def check_for_barge_in(self):
        return await self.dialogue.check_for_barge_in()

    async def teardown(self, reason: str = "session_teardown") -> list:
        """Fixes the disconnect issue named in the extension brief: call
        force_resolve_pending() so no action is left non-terminal after
        this call ends, whether or not a confirmation was ever answered.
        """
        self.brain.closed = True
        self.brain.pending_action_id = None
        self.brain.pending_decision_id = None
        return await self.commit.force_resolve_pending(self.call_id, reason=reason)
