"""Tests for the commit state machine (Phase 5): transitions, confirmation
safety, idempotency, terminal states, events, and the commit strategy seam.
"""

import asyncio
import inspect

from core.commit.state_machine import (
    ALLOWED_TRANSITIONS,
    CommitRecord,
    CommitState,
    CommitStateMachine,
    InvalidTransitionError,
    TERMINAL_STATES,
    UnknownActionError,
)
from core.commit.strategies import CommitStrategy, DeliberativeCommitStrategy
from core.event_bus import EventBus
from core.events import ActionAborted, ActionFinalized, ActionPending, ActionProposed


def run(coro):
    return asyncio.run(coro)


def make_machine(strategy=None):
    bus = EventBus()
    received = []

    async def record(event):
        received.append(event)

    run(bus.subscribe(record))
    machine = CommitStateMachine(bus=bus, strategy=strategy)
    return machine, received


# --- provider independence ---


def test_commit_modules_have_no_concrete_provider_imports():
    import core.commit.state_machine as sm_module
    import core.commit.strategies as strategies_module

    forbidden = ("MockSTT", "MockLLM", "MockTTS", "MockAudioIO", "LiveKit", "Deepgram", "Groq", "Rime")
    for module in (sm_module, strategies_module):
        source = inspect.getsource(module)
        for name in forbidden:
            assert name not in source


def test_commit_state_machine_never_imports_sqlite():
    import core.commit.state_machine as sm_module

    assert not hasattr(sm_module, "sqlite3")
    source = inspect.getsource(sm_module)
    assert "import sqlite3" not in source
    assert "import persistence" not in source


# --- valid transitions (section 18) ---


def test_propose_lands_in_pending_confirmation_with_default_strategy():
    machine, received = make_machine()
    record = run(machine.propose(call_id="c1", decision_id="d1", action_type="dispatch_tow"))

    assert record.current_state == CommitState.PENDING_CONFIRMATION
    proposed = [e for e in received if isinstance(e, ActionProposed)]
    pending = [e for e in received if isinstance(e, ActionPending)]
    assert len(proposed) == 1
    assert len(pending) == 1
    assert proposed[0].action_id == record.action_id
    assert pending[0].action_id == record.action_id


def test_confirm_finalizes_a_pending_action():
    machine, received = make_machine()
    record = run(machine.propose(call_id="c1", decision_id="d1", action_type="dispatch_tow"))

    finalized = run(machine.confirm(record.action_id, confirmed_by="caller"))

    assert finalized.current_state == CommitState.FINALIZED
    assert finalized.confirmed_by == "caller"
    assert finalized.confirmed_at_ms is not None
    events = [e for e in received if isinstance(e, ActionFinalized)]
    assert len(events) == 1
    assert events[0].action_id == record.action_id


def test_abort_aborts_a_pending_action():
    machine, received = make_machine()
    record = run(machine.propose(call_id="c1", decision_id="d1", action_type="dispatch_tow"))

    aborted = run(machine.abort(record.action_id, reason="caller hung up"))

    assert aborted.current_state == CommitState.ABORTED
    assert aborted.aborted_reason == "caller hung up"
    events = [e for e in received if isinstance(e, ActionAborted)]
    assert len(events) == 1
    assert events[0].reason == "caller hung up"


# --- invalid transitions (section 18) ---


class _NoAutoConfirmStrategy(CommitStrategy):
    name = "no_auto_confirm"

    def auto_request_confirmation(self) -> bool:
        return False


def test_proposed_to_finalized_is_rejected():
    machine, _ = make_machine(strategy=_NoAutoConfirmStrategy())
    record = run(machine.propose(call_id="c1", decision_id="d1", action_type="dispatch_tow"))
    assert record.current_state == CommitState.PROPOSED

    raised = False
    try:
        run(machine.confirm(record.action_id))
    except InvalidTransitionError as exc:
        raised = True
        assert exc.current == CommitState.PROPOSED
        assert exc.target == CommitState.FINALIZED
    assert raised


def test_proposed_to_aborted_is_rejected():
    machine, _ = make_machine(strategy=_NoAutoConfirmStrategy())
    record = run(machine.propose(call_id="c1", decision_id="d1", action_type="dispatch_tow"))

    raised = False
    try:
        run(machine.abort(record.action_id, reason="too soon"))
    except InvalidTransitionError:
        raised = True
    assert raised


def test_finalized_to_aborted_is_rejected():
    machine, _ = make_machine()
    record = run(machine.propose(call_id="c1", decision_id="d1", action_type="dispatch_tow"))
    run(machine.confirm(record.action_id))

    raised = False
    try:
        run(machine.abort(record.action_id, reason="changed my mind"))
    except InvalidTransitionError as exc:
        raised = True
        assert exc.current == CommitState.FINALIZED
        assert exc.target == CommitState.ABORTED
    assert raised


def test_aborted_to_finalized_is_rejected():
    machine, _ = make_machine()
    record = run(machine.propose(call_id="c1", decision_id="d1", action_type="dispatch_tow"))
    run(machine.abort(record.action_id, reason="caller hung up"))

    raised = False
    try:
        run(machine.confirm(record.action_id))
    except InvalidTransitionError as exc:
        raised = True
        assert exc.current == CommitState.ABORTED
        assert exc.target == CommitState.FINALIZED
    assert raised


def test_finalized_to_proposed_is_rejected():
    machine, _ = make_machine()
    record = run(machine.propose(call_id="c1", decision_id="d1", action_type="dispatch_tow"))
    run(machine.confirm(record.action_id))

    raised = False
    try:
        machine._transition(record, CommitState.PROPOSED)
    except InvalidTransitionError:
        raised = True
    assert raised


def test_allowed_transitions_table_matches_the_spec_exactly():
    assert ALLOWED_TRANSITIONS[CommitState.PROPOSED] == {CommitState.PENDING_CONFIRMATION}
    assert ALLOWED_TRANSITIONS[CommitState.PENDING_CONFIRMATION] == {
        CommitState.FINALIZED,
        CommitState.ABORTED,
    }
    assert ALLOWED_TRANSITIONS[CommitState.FINALIZED] == set()
    assert ALLOWED_TRANSITIONS[CommitState.ABORTED] == set()


# --- confirmation safety (section 4, 8, 18) ---


def test_proposal_alone_never_finalizes():
    machine, received = make_machine()
    record = run(machine.propose(call_id="c1", decision_id="d1", action_type="dispatch_tow"))

    assert record.current_state != CommitState.FINALIZED
    assert not any(isinstance(e, ActionFinalized) for e in received)


def test_no_way_to_reach_finalized_without_calling_confirm():
    """Constraints passing / high confidence never appear anywhere in this
    module's API -- the only method that can move a record to FINALIZED is
    `confirm()`. This test asserts that structurally: FINALIZED only
    appears as a target inside `confirm`."""
    import core.commit.state_machine as sm_module

    source = inspect.getsource(sm_module.CommitStateMachine.confirm)
    assert "CommitState.FINALIZED" in source
    propose_source = inspect.getsource(sm_module.CommitStateMachine.propose)
    assert "CommitState.FINALIZED" not in propose_source
    request_source = inspect.getsource(sm_module.CommitStateMachine.request_confirmation)
    assert "CommitState.FINALIZED" not in request_source


# --- repeated confirmation / idempotency (section 9, 18) ---


def test_confirming_twice_does_not_duplicate_finalization():
    machine, received = make_machine()
    record = run(machine.propose(call_id="c1", decision_id="d1", action_type="dispatch_tow"))

    first = run(machine.confirm(record.action_id, confirmed_by="caller"))
    second = run(machine.confirm(record.action_id, confirmed_by="someone_else"))

    assert first.current_state == CommitState.FINALIZED
    assert second.current_state == CommitState.FINALIZED
    # the second call must not have overwritten who actually confirmed it
    assert second.confirmed_by == "caller"

    events = [e for e in received if isinstance(e, ActionFinalized)]
    assert len(events) == 1


def test_aborting_twice_does_not_duplicate_the_event():
    machine, received = make_machine()
    record = run(machine.propose(call_id="c1", decision_id="d1", action_type="dispatch_tow"))

    run(machine.abort(record.action_id, reason="first reason"))
    run(machine.abort(record.action_id, reason="second reason"))

    events = [e for e in received if isinstance(e, ActionAborted)]
    assert len(events) == 1
    assert events[0].reason == "first reason"


def test_requesting_confirmation_twice_is_a_harmless_no_op():
    machine, received = make_machine(strategy=_NoAutoConfirmStrategy())
    record = run(machine.propose(call_id="c1", decision_id="d1", action_type="dispatch_tow"))

    run(machine.request_confirmation(record.action_id))
    run(machine.request_confirmation(record.action_id))

    events = [e for e in received if isinstance(e, ActionPending)]
    assert len(events) == 1


# --- terminal states (section 6, 18) ---


def test_finalized_and_aborted_records_report_is_terminal():
    machine, _ = make_machine()
    finalized = run(machine.propose(call_id="c1", decision_id="d1", action_type="dispatch_tow"))
    run(machine.confirm(finalized.action_id))
    assert finalized.is_terminal is True

    aborted = run(machine.propose(call_id="c1", decision_id="d2", action_type="dispatch_tow"))
    run(machine.abort(aborted.action_id, reason="x"))
    assert aborted.is_terminal is True

    assert CommitState.FINALIZED in TERMINAL_STATES
    assert CommitState.ABORTED in TERMINAL_STATES
    assert CommitState.PROPOSED not in TERMINAL_STATES
    assert CommitState.PENDING_CONFIRMATION not in TERMINAL_STATES


def test_unknown_action_id_raises():
    machine, _ = make_machine()
    raised = False
    try:
        run(machine.confirm("does-not-exist"))
    except UnknownActionError:
        raised = True
    assert raised


# --- traceability (section 7) ---


def test_action_traces_back_to_call_and_decision():
    machine, _ = make_machine()
    record = run(
        machine.propose(
            call_id="call-42",
            decision_id="decision-7",
            action_type="dispatch_tow",
            action_payload={"location": "Highway 9"},
        )
    )
    assert record.call_id == "call-42"
    assert record.decision_id == "decision-7"
    assert record.action_payload == {"location": "Highway 9"}

    assert machine.get(record.action_id) is record
    assert record in machine.list_for_call("call-42")


# --- strategy seam (section 12) ---


def test_deliberative_strategy_auto_requests_confirmation():
    strategy = DeliberativeCommitStrategy()
    assert strategy.auto_request_confirmation() is True


def test_custom_strategy_can_defer_confirmation_request_without_touching_the_state_machine():
    machine, received = make_machine(strategy=_NoAutoConfirmStrategy())
    record = run(machine.propose(call_id="c1", decision_id="d1", action_type="dispatch_tow"))

    assert record.current_state == CommitState.PROPOSED
    assert not any(isinstance(e, ActionPending) for e in received)

    run(machine.request_confirmation(record.action_id))
    assert record.current_state == CommitState.PENDING_CONFIRMATION


# --- session teardown (FR-3.4) ---


def test_force_resolve_pending_aborts_every_non_terminal_action_for_a_call():
    machine, received = make_machine(strategy=_NoAutoConfirmStrategy())
    still_proposed = run(machine.propose(call_id="c1", decision_id="d1", action_type="dispatch_tow"))
    pending = run(machine.propose(call_id="c1", decision_id="d2", action_type="escalate_emergency"))
    run(machine.request_confirmation(pending.action_id))
    already_done = run(machine.propose(call_id="c1", decision_id="d3", action_type="close_case"))
    run(machine.request_confirmation(already_done.action_id))
    run(machine.confirm(already_done.action_id))

    resolved = run(machine.force_resolve_pending("c1", reason="call ended"))

    assert still_proposed.current_state == CommitState.ABORTED
    assert pending.current_state == CommitState.ABORTED
    assert already_done.current_state == CommitState.FINALIZED  # untouched, already terminal
    assert already_done not in resolved
    assert {r.action_id for r in resolved} == {still_proposed.action_id, pending.action_id}


def test_events_are_json_serializable():
    import json

    machine, received = make_machine()
    record = run(machine.propose(call_id="c1", decision_id="d1", action_type="dispatch_tow"))
    run(machine.confirm(record.action_id))
    for event in received:
        json.dumps(event.to_dict())
