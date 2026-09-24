"""Tests for DeliberationRecord / ConstraintResult / Option (Phase 4)."""

from core.deliberation.record import ConstraintResult, DeliberationRecord, Option


def test_confidence_within_bounds_is_accepted():
    record = DeliberationRecord(decision_id="d1", call_id="c1", intent="breakdown", confidence=0.5)
    assert record.confidence == 0.5


def test_confidence_boundary_values_are_accepted():
    DeliberationRecord(decision_id="d1", call_id="c1", intent="breakdown", confidence=0.0)
    DeliberationRecord(decision_id="d2", call_id="c1", intent="breakdown", confidence=1.0)


def test_confidence_above_one_raises_value_error():
    raised = False
    try:
        DeliberationRecord(decision_id="d1", call_id="c1", intent="breakdown", confidence=1.5)
    except ValueError:
        raised = True
    assert raised


def test_confidence_below_zero_raises_value_error():
    raised = False
    try:
        DeliberationRecord(decision_id="d1", call_id="c1", intent="breakdown", confidence=-0.1)
    except ValueError:
        raised = True
    assert raised


def test_passed_all_constraints_true_when_no_constraints():
    record = DeliberationRecord(decision_id="d1", call_id="c1", intent="breakdown", confidence=0.5)
    assert record.passed_all_constraints is True
    assert record.failed_constraints == []


def test_failed_constraints_filters_correctly():
    results = [
        ConstraintResult(name="required_fields", passed=True, reason="ok"),
        ConstraintResult(name="service_radius", passed=False, reason="too far"),
    ]
    record = DeliberationRecord(
        decision_id="d1", call_id="c1", intent="breakdown", confidence=0.5, constraint_results=results
    )
    assert record.passed_all_constraints is False
    assert [r.name for r in record.failed_constraints] == ["service_radius"]


def test_mark_superseded_by_sets_pointer_without_touching_other_fields():
    record = DeliberationRecord(
        decision_id="d1",
        call_id="c1",
        intent="breakdown",
        confidence=0.5,
        rationale="original rationale",
        known_facts={"location": "Hwy 9"},
    )
    record.mark_superseded_by("d2")
    assert record.superseded_by == "d2"
    # nothing else about the record changed
    assert record.rationale == "original rationale"
    assert record.known_facts == {"location": "Hwy 9"}
    assert record.intent == "breakdown"


def test_to_dict_is_plain_json_friendly_structure():
    import json

    record = DeliberationRecord(
        decision_id="d1",
        call_id="c1",
        intent="breakdown",
        confidence=0.5,
        options_considered=[Option(action_type="dispatch_tow", description="tow it")],
    )
    data = record.to_dict()
    json.dumps(data)  # must not raise
    assert data["decision_id"] == "d1"
    assert data["options_considered"][0]["action_type"] == "dispatch_tow"
