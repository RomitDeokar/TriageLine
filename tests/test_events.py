"""Tests for the typed event models (Phase 3)."""

import json

from core.events import (
    ActionAborted,
    ActionFinalized,
    ActionPending,
    ActionProposed,
    BackchannelSent,
    BargeIn,
    DeliberationResolved,
    DeliberationStarted,
    FinalTranscript,
    MetricsTick,
    PartialTranscript,
    ReDeliberationTriggered,
    SelfCritiqueFailed,
    TurnStarted,
)


def test_partial_transcript_fields():
    event = PartialTranscript(call_id="c1", timestamp_ms=10, speaker="caller", text="my car")
    assert event.call_id == "c1"
    assert event.timestamp_ms == 10
    assert event.speaker == "caller"
    assert event.text == "my car"


def test_final_transcript_fields():
    event = FinalTranscript(call_id="c1", timestamp_ms=20, speaker="caller", text="my car broke down")
    assert event.text == "my car broke down"


def test_barge_in_fields():
    event = BargeIn(call_id="c1", timestamp_ms=30, position_ms=450, latency_ms=12)
    assert event.position_ms == 450
    assert event.latency_ms == 12


def test_backchannel_sent_fields():
    event = BackchannelSent(call_id="c1", timestamp_ms=40, text="mm-hm")
    assert event.text == "mm-hm"


def test_metrics_tick_optional_fields_default_none():
    event = MetricsTick(call_id="c1", timestamp_ms=50)
    assert event.barge_in_latency_ms is None
    assert event.time_to_decision_ms is None


def test_deliberation_started_fields():
    event = DeliberationStarted(call_id="c1", timestamp_ms=60, decision_id="d1", intent="breakdown")
    assert event.decision_id == "d1"
    assert event.intent == "breakdown"


def test_deliberation_resolved_defaults():
    event = DeliberationResolved(call_id="c1", timestamp_ms=70, decision_id="d1")
    assert event.known == {}
    assert event.uncertain == []
    assert event.options == []
    assert event.chosen is None
    assert event.confidence is None


def test_self_critique_failed_fields():
    event = SelfCritiqueFailed(
        call_id="c1",
        timestamp_ms=80,
        decision_id="d1",
        failed_constraint="service_radius",
        fallback_action="escalate_to_human",
    )
    assert event.failed_constraint == "service_radius"
    assert event.fallback_action == "escalate_to_human"


def test_re_deliberation_triggered_fields():
    event = ReDeliberationTriggered(
        call_id="c1", timestamp_ms=90, decision_id="d2", supersedes_id="d1", reason="new info"
    )
    assert event.supersedes_id == "d1"
    assert event.reason == "new info"


def test_action_lifecycle_events_fields():
    proposed = ActionProposed(call_id="c1", timestamp_ms=100, action_id="a1", action_type="dispatch_tow")
    pending = ActionPending(call_id="c1", timestamp_ms=110, action_id="a1")
    finalized = ActionFinalized(call_id="c1", timestamp_ms=120, action_id="a1")
    aborted = ActionAborted(call_id="c1", timestamp_ms=130, action_id="a1", reason="disconnect")

    assert proposed.action_type == "dispatch_tow"
    assert pending.action_id == finalized.action_id == aborted.action_id == "a1"
    assert aborted.reason == "disconnect"


def test_turn_started_fields():
    event = TurnStarted(call_id="c1", timestamp_ms=5, speaker="caller")
    assert event.speaker == "caller"


def test_event_serializes_to_dict_with_event_type():
    event = PartialTranscript(call_id="c1", timestamp_ms=10, speaker="caller", text="hi")
    data = event.to_dict()
    assert data["call_id"] == "c1"
    assert data["timestamp_ms"] == 10
    assert data["speaker"] == "caller"
    assert data["text"] == "hi"
    assert data["event_type"] == "PartialTranscript"


def test_event_dict_is_json_serializable():
    event = DeliberationResolved(
        call_id="c1",
        timestamp_ms=10,
        decision_id="d1",
        known={"location": "Hwy 9"},
        uncertain=["severity"],
        options=["a", "b"],
        chosen="a",
        confidence=0.7,
    )
    json.dumps(event.to_dict())  # must not raise
