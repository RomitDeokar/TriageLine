"""Replay harness: feeds a scripted Scenario into the existing core.

Drives the real DeliberationEngine (core/deliberation/engine.py) and the
real CommitStateMachine (core/commit/state_machine.py) directly, bypassing
the transport/dialogue layer entirely (no STT/TTS/AudioIO, no real time).
This is deliberately the same pattern docs/ARCHITECTURE.md describes for
the replay harness: a first-class consumer of core, not a hack, so 100+
trials run in milliseconds and exercise the exact decision code the live
agent would use.

Two agent "strategies" run through this one harness, sharing everything
except the decision/commit logic itself (per docs/ARCHITECTURE.md section
3 and REQUIREMENTS.md FR-4):

- "deliberative": every caller turn goes through the real DeliberationEngine
  (intent matching + self-critique constraints). An action is proposed only
  once a decision resolves, and it is only ever finalized in direct
  response to an explicit CONFIRM step in the scenario -- never earlier.
- "naive": every caller turn is matched against the same IntentRegistry but
  WITHOUT running any self-critique constraints, and the first action it
  decides on is proposed and immediately confirmed -- it never waits for,
  or checks, an actual caller confirmation. This is what FR-4.1 calls
  "commits immediately after the LLM decides, no confirmation-safety."
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from core.commit.state_machine import CommitStateMachine, CommitState
from core.commit.strategies import DeliberativeCommitStrategy
from core.deliberation.constraints import default_constraint_registry
from core.deliberation.engine import DeliberationEngine
from core.deliberation.intents import default_intent_registry
from core.event_bus import EventBus
from core.events import Event, BargeIn
from harness.scenario import Scenario, StepType
from providers.interfaces import ConversationTurn

AgentType = str  # "deliberative" | "naive"


@dataclass
class ReplayResult:
    """Structured, serializable outcome of running one scenario through one agent."""

    scenario_id: str
    agent_type: AgentType
    call_id: str
    success: bool = True
    error: Optional[str] = None

    event_trace: list[dict[str, Any]] = field(default_factory=list)

    deliberation_count: int = 0
    redeliberation_count: int = 0
    self_critique_failures: int = 0

    actions_proposed: int = 0
    actions_pending: int = 0
    actions_finalized: int = 0
    actions_aborted: int = 0

    barge_ins: int = 0
    backchannels: int = 0

    final_commit_state: Optional[str] = None
    final_action_type: Optional[str] = None
    unsafe_finalizations: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "agent_type": self.agent_type,
            "call_id": self.call_id,
            "success": self.success,
            "error": self.error,
            "deliberation_count": self.deliberation_count,
            "redeliberation_count": self.redeliberation_count,
            "self_critique_failures": self.self_critique_failures,
            "actions_proposed": self.actions_proposed,
            "actions_pending": self.actions_pending,
            "actions_finalized": self.actions_finalized,
            "actions_aborted": self.actions_aborted,
            "barge_ins": self.barge_ins,
            "backchannels": self.backchannels,
            "final_commit_state": self.final_commit_state,
            "final_action_type": self.final_action_type,
            "unsafe_finalizations": self.unsafe_finalizations,
            "event_trace": self.event_trace,
        }


class _EventCollector:
    """Subscribes to the real EventBus and tallies what the UI event
    contract (docs/UI_SPEC.md section 3) would show, without needing a UI.

    `on_event`, when given, is called synchronously with each event's
    `to_dict()` as it arrives -- this is the one hook Phase 7A's WebSocket
    bridge uses to relay a live/demo run to connected clients. It is
    plain-callback (not bus-subscriber) shaped on purpose: the bridge lives
    outside core/harness and must not need to know about EventBus at all.
    """

    def __init__(self, on_event: Optional[Callable[[dict[str, Any]], None]] = None) -> None:
        self._on_event = on_event
        self.trace: list[dict[str, Any]] = []
        self.deliberation_count = 0
        self.redeliberation_count = 0
        self.self_critique_failures = 0
        self.actions_proposed = 0
        self.actions_pending = 0
        self.actions_finalized = 0
        self.actions_aborted = 0
        self.barge_ins = 0
        self.backchannels = 0
        self.last_finalized_action_type: dict[str, str] = {}

    async def handle(self, event: Event) -> None:
        payload = event.to_dict()
        self.trace.append(payload)
        if self._on_event is not None:
            self._on_event(payload)
        name = event.event_type
        if name == "DeliberationStarted":
            self.deliberation_count += 1
        elif name == "ReDeliberationTriggered":
            self.redeliberation_count += 1
        elif name == "SelfCritiqueFailed":
            self.self_critique_failures += 1
        elif name == "ActionProposed":
            self.actions_proposed += 1
            self.last_finalized_action_type[event.action_id] = event.action_type  # type: ignore[attr-defined]
        elif name == "ActionPending":
            self.actions_pending += 1
        elif name == "ActionFinalized":
            self.actions_finalized += 1
        elif name == "ActionAborted":
            self.actions_aborted += 1
        elif name == "BargeIn":
            self.barge_ins += 1
        elif name == "BackchannelSent":
            self.backchannels += 1


def _clock_factory():
    counter = {"t": 0}

    def clock_ms() -> int:
        counter["t"] += 1
        return counter["t"]

    return clock_ms


def _id_factory_for(prefix: str):
    counter = {"n": 0}

    def make() -> str:
        counter["n"] += 1
        return f"{prefix}-{counter['n']:04d}"

    return make


async def _run_deliberative(
    scenario: Scenario,
    call_id: str,
    result: ReplayResult,
    on_event: Optional[Callable[[dict[str, Any]], None]] = None,
) -> None:
    bus = EventBus()
    collector = _EventCollector(on_event=on_event)
    await bus.subscribe(collector.handle)

    engine = DeliberationEngine(
        bus=bus,
        intent_registry=default_intent_registry(),
        constraint_registry=default_constraint_registry(),
        clock_ms=_clock_factory(),
        id_factory=_id_factory_for("dec"),
    )
    commit = CommitStateMachine(
        bus=bus,
        strategy=DeliberativeCommitStrategy(),
        clock_ms=_clock_factory(),
        id_factory=_id_factory_for("act"),
    )

    context: list[ConversationTurn] = []
    pending_action_id: Optional[str] = None
    active_decision_id: Optional[str] = None
    real_confirmation_seen = False

    for step in scenario.steps:
        if step.type == StepType.CALLER_SAYS:
            context.append(ConversationTurn(speaker="caller", text=step.text))
            record = await engine.deliberate(call_id, context)

            if record.resolved and record.chosen_option is not None:
                # A prior pending action for the same call that hasn't been
                # resolved yet is superseded by this new decision -- abort
                # it explicitly rather than leaving it dangling (this is
                # what a real re-deliberation must do to the commit layer,
                # even though core/commit itself has no notion of
                # "decision groups").
                if pending_action_id is not None:
                    existing = commit.get(pending_action_id)
                    if existing is not None and not existing.is_terminal:
                        await commit.abort(pending_action_id, reason="superseded_by_redeliberation")
                        real_confirmation_seen = False

                proposed = await commit.propose(
                    call_id=call_id,
                    decision_id=record.decision_id,
                    action_type=record.chosen_option,
                    action_payload=dict(record.known_facts),
                )
                pending_action_id = proposed.action_id
                active_decision_id = record.decision_id

        elif step.type == StepType.CONFIRM:
            real_confirmation_seen = True
            if pending_action_id is not None:
                existing = commit.get(pending_action_id)
                if existing is not None and existing.current_state == CommitState.PENDING_CONFIRMATION:
                    # The only place confirm() is ever called for this
                    # agent: in direct response to a genuine CONFIRM step.
                    # unsafe_finalizations therefore stays 0 by construction
                    # -- there is no code path to FINALIZED here that
                    # wasn't gated on this branch.
                    await commit.confirm(pending_action_id, confirmed_by="caller")

        elif step.type == StepType.REJECT:
            if pending_action_id is not None:
                existing = commit.get(pending_action_id)
                if existing is not None and existing.current_state == CommitState.PENDING_CONFIRMATION:
                    await commit.abort(pending_action_id, reason="caller_rejected")

        elif step.type == StepType.BARGE_IN:
            await bus.publish(
                BargeIn(call_id=call_id, timestamp_ms=0, position_ms=0, latency_ms=90)
            )

        elif step.type == StepType.DISCONNECT:
            await commit.force_resolve_pending(call_id, reason="disconnect")

    _finalize_result(result, collector, commit, call_id, pending_action_id)


async def _run_naive(
    scenario: Scenario,
    call_id: str,
    result: ReplayResult,
    on_event: Optional[Callable[[dict[str, Any]], None]] = None,
) -> None:
    bus = EventBus()
    collector = _EventCollector(on_event=on_event)
    await bus.subscribe(collector.handle)

    intents = default_intent_registry()
    # Deliberately no ConstraintRegistry: FR-4.1's "no confirmation-safety
    # or deliberation gating" -- the naive agent skips self-critique
    # entirely rather than merely skipping the wait for confirmation.
    commit = CommitStateMachine(
        bus=bus,
        strategy=DeliberativeCommitStrategy(),
        clock_ms=_clock_factory(),
        id_factory=_id_factory_for("act"),
    )

    context: list[ConversationTurn] = []
    decided = False  # naive commits to its first decision and never revisits it
    action_id: Optional[str] = None
    decision_counter = 0

    for step in scenario.steps:
        if step.type == StepType.CALLER_SAYS:
            context.append(ConversationTurn(speaker="caller", text=step.text))
            if decided:
                continue
            handler = intents.best_match(context)
            if handler is None:
                continue
            candidate = handler.deliberate(context)
            if candidate.chosen_option is None:
                continue

            decided = True
            decision_counter += 1
            decision_id = f"naive-dec-{decision_counter:04d}"
            proposed = await commit.propose(
                call_id=call_id,
                decision_id=decision_id,
                action_type=candidate.chosen_option,
                action_payload=dict(candidate.known_facts),
            )
            action_id = proposed.action_id
            # "Commits immediately after the LLM decides" (FR-4.1): confirm
            # right away, with no real caller confirmation behind it.
            await commit.confirm(action_id, confirmed_by="naive_auto")
            result.unsafe_finalizations += 1

        elif step.type == StepType.BARGE_IN:
            await bus.publish(
                BargeIn(call_id=call_id, timestamp_ms=0, position_ms=0, latency_ms=90)
            )

        elif step.type == StepType.DISCONNECT:
            await commit.force_resolve_pending(call_id, reason="disconnect")

        # CONFIRM/REJECT steps are meaningless to the naive agent: it has
        # already committed by the time they would arrive, which is
        # exactly the safety gap this comparison exists to surface.

    _finalize_result(result, collector, commit, call_id, action_id)


def _finalize_result(
    result: ReplayResult,
    collector: _EventCollector,
    commit: CommitStateMachine,
    call_id: str,
    tracked_action_id: Optional[str],
) -> None:
    result.event_trace = collector.trace
    result.deliberation_count = collector.deliberation_count
    result.redeliberation_count = collector.redeliberation_count
    result.self_critique_failures = collector.self_critique_failures
    result.actions_proposed = collector.actions_proposed
    result.actions_pending = collector.actions_pending
    result.actions_finalized = collector.actions_finalized
    result.actions_aborted = collector.actions_aborted
    result.barge_ins = collector.barge_ins
    result.backchannels = collector.backchannels

    if tracked_action_id is not None:
        record = commit.get(tracked_action_id)
        if record is not None:
            result.final_commit_state = record.current_state.value
            result.final_action_type = record.action_type
    else:
        actions = commit.list_for_call(call_id)
        if actions:
            last = actions[-1]
            result.final_commit_state = last.current_state.value
            result.final_action_type = last.action_type


def run_scenario(
    scenario: Scenario,
    agent_type: AgentType,
    trial_index: int = 0,
    on_event: Optional[Callable[[dict[str, Any]], None]] = None,
) -> ReplayResult:
    """Run one scenario through one agent, synchronously (wraps asyncio.run).

    `on_event` is optional and does not change any decision/commit
    behavior -- it is a pass-through tap for live callers (Phase 7A's
    WebSocket bridge) that want to observe the exact same event trace this
    function already records into `ReplayResult.event_trace`, as it is
    produced rather than only after the run completes. The evaluation/
    comparison path (harness/comparison.py, run_evaluation.py) is
    unaffected since it never passes this argument.
    """

    call_id = f"{scenario.scenario_id}-{agent_type}-{trial_index}"
    result = ReplayResult(scenario_id=scenario.scenario_id, agent_type=agent_type, call_id=call_id)

    async def _run() -> None:
        if agent_type == "deliberative":
            await _run_deliberative(scenario, call_id, result, on_event=on_event)
        elif agent_type == "naive":
            await _run_naive(scenario, call_id, result, on_event=on_event)
        else:
            raise ValueError(f"Unknown agent_type: {agent_type!r}")

    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 - a failed trial is data, not a crash
        result.success = False
        result.error = f"{type(exc).__name__}: {exc}"

    return result
