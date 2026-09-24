"""Phase 6B — Scenario library + replay validation.

Scope is deliberately narrow: the required deterministic scenarios, their
event traces, ReplayResult structure, and determinism -- using only
harness/scenario.py, harness/scenarios/definitions.py, and
harness/replay.py's run_scenario() against the "deliberative" agent.

This module intentionally does NOT touch harness/comparison.py or
reporting/ (naive agent, comparison runner, metrics, evaluation reports)
-- those are out of scope for 6B and already covered separately in
tests/test_replay_harness.py from the 6A pass. Nothing in harness/replay.py
or harness/scenarios/definitions.py is modified by this phase; this file
only adds test coverage on top of the existing library.
"""

from __future__ import annotations

from harness.replay import ReplayResult, run_scenario
from harness.scenario import Scenario
from harness.scenarios.definitions import (
    required_scenarios,
    scenario_barge_in,
    scenario_constraint_failure,
    scenario_contradiction,
    scenario_disconnect_before_confirmation,
    scenario_emergency,
    scenario_missing_information,
    scenario_normal_tow,
    scenario_rejected_action,
)

AGENT = "deliberative"  # 6B validates the existing deliberative replay path only


def _event_types(result: ReplayResult) -> list[str]:
    return [e["event_type"] for e in result.event_trace]


def _assert_subsequence(haystack: list[str], needle: list[str]) -> None:
    """Assert `needle` appears in `haystack`, in order (not necessarily
    contiguous) -- e.g. other events (TurnStarted, MetricsTick) may
    legitimately interleave.
    """
    it = iter(haystack)
    for expected in needle:
        for actual in it:
            if actual == expected:
                break
        else:
            raise AssertionError(f"Expected event {expected!r} not found in order in {haystack}")


# --- 2. Required scenarios exist ----------------------------------------


def test_scenario_library_contains_all_seven_required_scenarios():
    ids = {s.scenario_id for s in required_scenarios()}
    required = {
        "normal_tow",
        "missing_information",
        "emergency",
        "contradiction",
        "rejected_action",
        "barge_in",
        "constraint_failure",
    }
    assert required.issubset(ids), f"missing: {required - ids}"


def test_every_required_scenario_is_a_scenario_instance_with_steps():
    for scenario in required_scenarios():
        assert isinstance(scenario, Scenario)
        assert len(scenario.steps) > 0
        assert scenario.scenario_id


# --- Scenario 1: Normal Tow ---


def test_scenario_1_normal_tow_event_trace():
    result = run_scenario(scenario_normal_tow(), AGENT)
    _assert_subsequence(
        _event_types(result),
        [
            "DeliberationStarted",
            "DeliberationResolved",
            "ActionProposed",
            "ActionPending",
            "ActionFinalized",
        ],
    )


def test_scenario_1_normal_tow_final_behavior():
    result = run_scenario(scenario_normal_tow(), AGENT)
    assert result.final_commit_state == "finalized"
    assert result.final_action_type == "dispatch_tow"
    assert result.actions_finalized == 1
    assert result.actions_aborted == 0


# --- Scenario 2: Missing Information ---


def test_scenario_2_missing_information_no_unsafe_finalization():
    result = run_scenario(scenario_missing_information(), AGENT)
    assert result.final_commit_state != "finalized"
    assert result.actions_finalized == 0
    assert result.unsafe_finalizations == 0


# --- Scenario 3: Emergency ---


def test_scenario_3_emergency_deliberation_behavior():
    result = run_scenario(scenario_emergency(), AGENT)
    types = _event_types(result)
    assert "DeliberationStarted" in types
    assert "DeliberationResolved" in types
    # The LAST resolved record is what matters: the first turn only
    # mentions the fire (no location yet), the second turn supplies the
    # location, which resolves the decision -- so the final resolved
    # record must reflect the emergency escalation, not a standard tow.
    resolved = [e for e in result.event_trace if e["event_type"] == "DeliberationResolved"]
    final_resolved = resolved[-1]
    assert final_resolved["chosen"] == "escalate_emergency"
    assert final_resolved["known"].get("severity") == "emergency"


# --- Scenario 4: Contradiction / re-deliberation ---


def test_scenario_4_contradiction_event_trace():
    result = run_scenario(scenario_contradiction(), AGENT)
    # The engine publishes DeliberationStarted for the new pass before it
    # knows whether it supersedes anything, and ReDeliberationTriggered
    # before the new DeliberationResolved (see
    # core/deliberation/engine.py's deliberate()) -- so the correct order
    # is Started/Resolved for the original pass, then Started for the new
    # pass, then ReDeliberationTriggered, then the new Resolved.
    _assert_subsequence(
        _event_types(result),
        [
            "DeliberationStarted",
            "DeliberationResolved",
            "DeliberationStarted",
            "ReDeliberationTriggered",
            "DeliberationResolved",
        ],
    )


def test_scenario_4_contradiction_supersedes_old_deliberation():
    result = run_scenario(scenario_contradiction(), AGENT)
    resolved = [e for e in result.event_trace if e["event_type"] == "DeliberationResolved"]
    redelib = [e for e in result.event_trace if e["event_type"] == "ReDeliberationTriggered"]

    assert len(resolved) == 2, "expected an original and a superseding deliberation"
    assert len(redelib) == 1

    original_decision_id = resolved[0]["decision_id"]
    new_decision_id = resolved[1]["decision_id"]

    # The re-deliberation event names exactly the original as superseded...
    assert redelib[0]["supersedes_id"] == original_decision_id
    assert redelib[0]["decision_id"] == new_decision_id

    # ...and the original's own resolved content is still present in the
    # trace untouched (retained, not overwritten -- FR-2.4).
    assert resolved[0]["chosen"] == "dispatch_tow"
    assert resolved[1]["chosen"] == "escalate_emergency"


# --- Scenario 5: Rejected Action ---


def test_scenario_5_rejected_action_event_trace():
    result = run_scenario(scenario_rejected_action(), AGENT)
    _assert_subsequence(
        _event_types(result),
        ["ActionProposed", "ActionPending", "ActionAborted"],
    )


def test_scenario_5_rejected_action_never_finalizes():
    result = run_scenario(scenario_rejected_action(), AGENT)
    assert result.final_commit_state == "aborted"
    assert result.actions_finalized == 0


# --- Scenario 6: Barge-In ---


def test_scenario_6_barge_in_produces_bargein_event():
    result = run_scenario(scenario_barge_in(), AGENT)
    assert "BargeIn" in _event_types(result)
    assert result.barge_ins == 1


def test_scenario_6_barge_in_does_not_prevent_correct_completion():
    result = run_scenario(scenario_barge_in(), AGENT)
    # Interruption happens before the location is fully given, but the
    # caller's next turn supplies it, so the call should still resolve.
    assert result.final_commit_state == "finalized"
    assert result.final_action_type == "dispatch_tow"


# --- Scenario 7: Constraint Failure ---


def test_scenario_7_constraint_failure_event_trace():
    result = run_scenario(scenario_constraint_failure(), AGENT)
    assert "SelfCritiqueFailed" in _event_types(result)
    assert result.self_critique_failures >= 1


def test_scenario_7_constraint_failure_prevents_unsafe_finalization():
    result = run_scenario(scenario_constraint_failure(), AGENT)
    assert result.final_commit_state != "finalized"
    assert result.unsafe_finalizations == 0


# --- Bonus: disconnect / session teardown (already in the library) ---


def test_disconnect_scenario_force_resolves_pending_action():
    result = run_scenario(scenario_disconnect_before_confirmation(), AGENT)
    assert result.final_commit_state == "aborted"
    assert "ActionAborted" in _event_types(result)


# --- 4. ReplayResult validation ------------------------------------------


def test_replay_result_has_scenario_id_success_and_ordered_trace_for_every_scenario():
    for scenario in required_scenarios():
        result = run_scenario(scenario, AGENT)
        assert result.scenario_id == scenario.scenario_id
        assert result.success is True, f"{scenario.scenario_id}: {result.error}"
        assert result.error is None
        assert isinstance(result.event_trace, list)
        assert len(result.event_trace) > 0
        # Each event carries a timestamp field, but the deliberation engine
        # and commit state machine use independent clocks (see
        # harness/replay.py's _clock_factory() per component), so only
        # per-event well-formedness is checked here -- ordering is
        # validated per-scenario via the explicit event-type sequence
        # tests above, not via raw timestamp comparison.
        for event in result.event_trace:
            assert "timestamp_ms" in event
            assert "event_type" in event


def test_replay_result_reflects_expected_final_behavior_per_scenario():
    expectations = {
        "normal_tow": ("finalized", "dispatch_tow"),
        "missing_information": (None, None),
        "emergency": ("finalized", "escalate_emergency"),
        "contradiction": ("finalized", "escalate_emergency"),
        "rejected_action": ("aborted", "dispatch_tow"),
        "barge_in": ("finalized", "dispatch_tow"),
        "constraint_failure": (None, None),
    }
    for scenario in required_scenarios():
        if scenario.scenario_id not in expectations:
            continue
        expected_state, expected_action = expectations[scenario.scenario_id]
        result = run_scenario(scenario, AGENT)
        if expected_state is None:
            assert result.final_commit_state != "finalized", scenario.scenario_id
        else:
            assert result.final_commit_state == expected_state, scenario.scenario_id
            assert result.final_action_type == expected_action, scenario.scenario_id


# --- 5. Determinism -------------------------------------------------------


def _event_signature(event: dict) -> str:
    """Everything about an event except its timestamp -- determinism must
    hold for event types, ordering, and payload content, not wall-clock
    values (section 5/18 of the phase spec). JSON-encoded (sorted keys)
    rather than a raw tuple since event payloads contain nested dicts/
    lists (e.g. DeliberationResolved.known), which aren't hashable.
    """
    import json

    trimmed = {k: v for k, v in event.items() if k != "timestamp_ms"}
    return json.dumps(trimmed, sort_keys=True, default=str)


def test_each_required_scenario_is_deterministic_across_repeated_runs():
    for scenario in required_scenarios():
        first = run_scenario(scenario, AGENT, trial_index=0)
        second = run_scenario(scenario, AGENT, trial_index=0)

        first_sig = [_event_signature(e) for e in first.event_trace]
        second_sig = [_event_signature(e) for e in second.event_trace]
        assert first_sig == second_sig, scenario.scenario_id

        assert first.final_commit_state == second.final_commit_state
        assert first.final_action_type == second.final_action_type
        assert first.deliberation_count == second.deliberation_count
        assert first.redeliberation_count == second.redeliberation_count
        assert first.self_critique_failures == second.self_critique_failures
        assert first.actions_finalized == second.actions_finalized
        assert first.actions_aborted == second.actions_aborted


def test_determinism_holds_across_many_repeated_runs_not_just_two():
    scenario = scenario_contradiction()
    signatures = set()
    for _ in range(10):
        result = run_scenario(scenario, AGENT, trial_index=0)
        signatures.add(tuple(_event_signature(e) for e in result.event_trace))
    assert len(signatures) == 1, "event trace varied across repeated runs of the same scenario"
