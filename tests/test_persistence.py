"""Tests for the SQLite persistence layer (Phase 5): schema init, saving
and retrieving deliberation/action records, historical/supersession
preservation, restart/reload behavior, and traceability.
"""

import asyncio
import inspect
import json
import os
import tempfile

from core.commit.state_machine import CommitState, CommitStateMachine
from core.deliberation.engine import DeliberationEngine
from core.deliberation.record import DeliberationRecord
from core.event_bus import EventBus
from providers.interfaces import ConversationTurn
from persistence.db import Database
from persistence.repository import ActionRepository, CallRepository, DeliberationRepository


def run(coro):
    return asyncio.run(coro)


def caller_turn(text: str) -> ConversationTurn:
    return ConversationTurn(speaker="caller", text=text)


def make_temp_db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # sqlite3.connect will create it fresh
    return path


# --- provider independence / offline-only (section 19, 20) ---


def test_persistence_modules_have_no_concrete_provider_imports_or_network():
    import persistence.db as db_module
    import persistence.repository as repo_module

    forbidden = ("MockSTT", "MockLLM", "MockTTS", "MockAudioIO", "LiveKit", "Deepgram", "Groq", "Rime")
    network_hints = ("requests.", "urllib.request", "http.client", "socket.socket")
    for module in (db_module, repo_module):
        source = inspect.getsource(module)
        for name in forbidden:
            assert name not in source
        for hint in network_hints:
            assert hint not in source


# --- database + schema initialization (section 15, 18) ---


def test_database_defaults_to_in_memory_and_initializes_schema():
    db = Database()
    db.initialize_schema()
    tables = {
        row["name"]
        for row in db.connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"calls", "deliberation_records", "commit_actions"} <= tables
    db.close()


def test_database_supports_a_configurable_temp_file_path():
    path = make_temp_db_path()
    try:
        db = Database(path)
        db.initialize_schema()
        assert os.path.exists(path)
        db.close()
    finally:
        if os.path.exists(path):
            os.unlink(path)


# --- calls ---


def test_call_repository_ensure_call_is_idempotent():
    db = Database()
    db.initialize_schema()
    repo = CallRepository(db)

    repo.ensure_call("call-1", created_at_ms=100)
    repo.ensure_call("call-1", created_at_ms=999)  # must not raise or duplicate

    assert repo.list_calls() == ["call-1"]


# --- deliberation persistence (section 13, 18) ---


def test_save_and_retrieve_a_deliberation_record():
    db = Database()
    db.initialize_schema()
    repo = DeliberationRepository(db)

    bus = EventBus()
    engine = DeliberationEngine(bus=bus, repository=repo)
    record = run(engine.deliberate("call-1", [caller_turn("my car broke down on Highway 9")]))

    fetched = repo.get_deliberation(record.decision_id)
    assert fetched is not None
    assert fetched.decision_id == record.decision_id
    assert fetched.call_id == "call-1"
    assert fetched.intent == "breakdown"
    assert fetched.chosen_option == "dispatch_tow"
    assert fetched.known_facts["location"]
    assert fetched.resolved is True
    assert 0.0 <= fetched.confidence <= 1.0
    assert len(fetched.constraint_results) == 3  # required_fields, severity_threshold, service_radius
    assert len(fetched.options_considered) == 1
    assert fetched.options_considered[0].action_type == "dispatch_tow"


def test_deliberation_engine_persists_without_a_repository_by_default():
    """A repository is optional -- Phase 4 behavior is unaffected."""
    bus = EventBus()
    engine = DeliberationEngine(bus=bus)  # no repository
    record = run(engine.deliberate("call-1", [caller_turn("my car broke down on Highway 9")]))
    assert record.resolved is True  # runs fine with no persistence at all


def test_supersession_preserves_both_records_in_the_database():
    """Contradiction handling (section 14): both the superseded breakdown
    record and the superseding emergency record must survive persistence,
    and the old record's own content must be untouched -- only its
    superseded_by pointer changes."""
    db = Database()
    db.initialize_schema()
    repo = DeliberationRepository(db)
    bus = EventBus()
    engine = DeliberationEngine(bus=bus, repository=repo)

    context_v1 = [caller_turn("my car broke down on Highway 9")]
    first = run(engine.deliberate("call-1", context_v1))

    context_v2 = context_v1 + [caller_turn("actually there's a fire")]
    second = run(engine.deliberate("call-1", context_v2))

    persisted_first = repo.get_deliberation(first.decision_id)
    persisted_second = repo.get_deliberation(second.decision_id)

    assert persisted_first is not None
    assert persisted_second is not None
    assert persisted_first.intent == "breakdown"
    assert persisted_first.chosen_option == "dispatch_tow"
    assert persisted_first.superseded_by == second.decision_id
    assert persisted_second.intent == "emergency"
    assert persisted_second.chosen_option == "escalate_emergency"
    assert persisted_second.supersedes == first.decision_id

    all_for_call = repo.list_for_call("call-1")
    assert len(all_for_call) == 2


def test_resaving_a_decision_cannot_overwrite_its_reasoning_content():
    """Belt-and-suspenders at the SQL layer (section 14): even a direct,
    malformed re-save attempt can only ever change superseded_by."""
    db = Database()
    db.initialize_schema()
    repo = DeliberationRepository(db)

    original = DeliberationRecord(
        decision_id="d1", call_id="c1", intent="breakdown", confidence=0.5, rationale="original"
    )
    repo.save_deliberation(original)

    tampered = DeliberationRecord(
        decision_id="d1",
        call_id="c1",
        intent="breakdown",
        confidence=0.99,
        rationale="a different rationale entirely",
        superseded_by="d2",
    )
    repo.save_deliberation(tampered)

    fetched = repo.get_deliberation("d1")
    assert fetched.rationale == "original"  # untouched
    assert fetched.confidence == 0.5  # untouched
    assert fetched.superseded_by == "d2"  # the one field allowed to change


# --- action / commit persistence (section 13, 18) ---


def test_save_and_retrieve_an_action_record():
    db = Database()
    db.initialize_schema()
    repo = ActionRepository(db)
    bus = EventBus()
    machine = CommitStateMachine(bus=bus, repository=repo)

    record = run(
        machine.propose(
            call_id="call-1",
            decision_id="decision-1",
            action_type="dispatch_tow",
            action_payload={"location": "Highway 9"},
        )
    )

    fetched = repo.get_action(record.action_id)
    assert fetched is not None
    assert fetched.call_id == "call-1"
    assert fetched.decision_id == "decision-1"
    assert fetched.action_type == "dispatch_tow"
    assert fetched.action_payload == {"location": "Highway 9"}
    assert fetched.current_state == CommitState.PENDING_CONFIRMATION


def test_action_state_transitions_are_persisted():
    db = Database()
    db.initialize_schema()
    repo = ActionRepository(db)
    bus = EventBus()
    machine = CommitStateMachine(bus=bus, repository=repo)

    record = run(machine.propose(call_id="call-1", decision_id="decision-1", action_type="dispatch_tow"))
    run(machine.confirm(record.action_id, confirmed_by="caller"))

    fetched = repo.get_action(record.action_id)
    assert fetched.current_state == CommitState.FINALIZED
    assert fetched.confirmed_by == "caller"
    assert fetched.confirmed_at_ms is not None


def test_commit_state_machine_works_without_a_repository_by_default():
    bus = EventBus()
    machine = CommitStateMachine(bus=bus)  # no repository
    record = run(machine.propose(call_id="c1", decision_id="d1", action_type="dispatch_tow"))
    assert record.current_state == CommitState.PENDING_CONFIRMATION  # runs fine, nothing persisted


# --- restart / reload behavior (section 18) ---


def test_reloading_from_a_fresh_repository_instance_preserves_deliberation_state():
    path = make_temp_db_path()
    try:
        db1 = Database(path)
        db1.initialize_schema()
        repo1 = DeliberationRepository(db1)
        bus = EventBus()
        engine = DeliberationEngine(bus=bus, repository=repo1)
        record = run(engine.deliberate("call-1", [caller_turn("my car broke down on Highway 9")]))
        db1.close()

        # brand-new process-like state: fresh Database + fresh Repository
        db2 = Database(path)
        repo2 = DeliberationRepository(db2)
        reloaded = repo2.get_deliberation(record.decision_id)

        assert reloaded is not None
        assert reloaded.intent == "breakdown"
        assert reloaded.chosen_option == "dispatch_tow"
        assert reloaded.known_facts == record.known_facts
        db2.close()
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_reloading_from_a_fresh_repository_instance_preserves_action_state():
    path = make_temp_db_path()
    try:
        db1 = Database(path)
        db1.initialize_schema()
        repo1 = ActionRepository(db1)
        bus = EventBus()
        machine = CommitStateMachine(bus=bus, repository=repo1)
        record = run(machine.propose(call_id="call-1", decision_id="decision-1", action_type="dispatch_tow"))
        run(machine.confirm(record.action_id))
        db1.close()

        db2 = Database(path)
        repo2 = ActionRepository(db2)
        reloaded = repo2.get_action(record.action_id)

        assert reloaded is not None
        assert reloaded.current_state == CommitState.FINALIZED
        db2.close()
    finally:
        if os.path.exists(path):
            os.unlink(path)


# --- traceability (section 7, 18) ---


def test_full_call_to_decision_to_action_trace_survives_persistence():
    path = make_temp_db_path()
    try:
        db = Database(path)
        db.initialize_schema()
        call_repo = CallRepository(db)
        deliberation_repo = DeliberationRepository(db)
        action_repo = ActionRepository(db)

        bus = EventBus()
        engine = DeliberationEngine(bus=bus, repository=deliberation_repo)
        machine = CommitStateMachine(bus=bus, repository=action_repo)

        call_repo.ensure_call("call-1", created_at_ms=0)
        decision = run(engine.deliberate("call-1", [caller_turn("my car broke down on Highway 9")]))
        action = run(
            machine.propose(
                call_id="call-1",
                decision_id=decision.decision_id,
                action_type=decision.chosen_option,
                action_payload=decision.known_facts,
            )
        )

        assert "call-1" in call_repo.list_calls()
        persisted_decision = deliberation_repo.get_deliberation(decision.decision_id)
        persisted_action = action_repo.get_action(action.action_id)

        assert persisted_action.call_id == "call-1"
        assert persisted_action.decision_id == persisted_decision.decision_id
        assert persisted_action.action_type == persisted_decision.chosen_option
        db.close()
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_events_and_json_round_trip_for_persisted_payloads():
    """Sanity check that everything persisted is plain-JSON-friendly, since
    the future UI/reporting layers will consume it that way."""
    db = Database()
    db.initialize_schema()
    repo = DeliberationRepository(db)
    bus = EventBus()
    engine = DeliberationEngine(bus=bus, repository=repo)
    record = run(engine.deliberate("call-1", [caller_turn("my car broke down")]))  # missing location

    fetched = repo.get_deliberation(record.decision_id)
    json.dumps(fetched.to_dict())  # must not raise
    assert fetched.resolved is False
    assert "location" in fetched.uncertainties
