"""Tests for Phase 6: replay harness, required scenarios, naive vs
deliberative comparison, metrics, reporting, and 100+ trial determinism.

Mirrors the style of tests/test_deliberation_engine.py and
tests/test_commit_state_machine.py: plain assert-based tests, no pytest
fixtures (see tests/_run_all.py).
"""

from __future__ import annotations

import json

from harness.comparison import run_comparison
from harness.ground_truth import compute_ground_truth
from harness.replay import run_scenario
from harness.scenario import Scenario, barge_in, caller_says, confirm, disconnect, reject
from harness.scenarios.definitions import (
    all_trials,
    generate_trial_matrix,
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
from reporting.metrics import calculate_metrics
from reporting.summarize import build_report


# --- basic replay mechanics ---------------------------------------------


def test_run_scenario_returns_a_replay_result_that_succeeded():
    result = run_scenario(scenario_normal_tow(), "deliberative")
    assert result.success is True
    assert result.error is None
    assert result.scenario_id == "normal_tow"
    assert result.agent_type == "deliberative"


def test_replay_result_is_json_serializable():
    result = run_scenario(scenario_normal_tow(), "deliberative")
    payload = json.dumps(result.to_dict())
    reloaded = json.loads(payload)
    assert reloaded["scenario_id"] == "normal_tow"
    assert isinstance(reloaded["event_trace"], list)
    assert len(reloaded["event_trace"]) > 0


def test_event_trace_contains_expected_event_types_for_normal_tow():
    result = run_scenario(scenario_normal_tow(), "deliberative")
    types = {e["event_type"] for e in result.event_trace}
    assert "DeliberationStarted" in types
    assert "DeliberationResolved" in types
    assert "ActionProposed" in types
    assert "ActionPending" in types
    assert "ActionFinalized" in types


def test_replay_is_deterministic_across_repeated_runs():
    first = run_scenario(scenario_normal_tow(), "deliberative", trial_index=7)
    second = run_scenario(scenario_normal_tow(), "deliberative", trial_index=7)
    assert first.to_dict() == second.to_dict()


# --- required scenario 1: normal tow ---


def test_normal_tow_scenario_finalizes_dispatch_for_deliberative_agent():
    result = run_scenario(scenario_normal_tow(), "deliberative")
    assert result.final_commit_state == "finalized"
    assert result.final_action_type == "dispatch_tow"
    assert result.unsafe_finalizations == 0


# --- required scenario 2: missing information ---


def test_missing_information_scenario_never_finalizes():
    result = run_scenario(scenario_missing_information(), "deliberative")
    assert result.final_commit_state != "finalized"
    assert result.unsafe_finalizations == 0


# --- required scenario 3: emergency ---


def test_emergency_scenario_escalates_rather_than_standard_dispatch():
    result = run_scenario(scenario_emergency(), "deliberative")
    assert result.final_commit_state == "finalized"
    assert result.final_action_type == "escalate_emergency"


# --- required scenario 4: contradiction / re-deliberation ---


def test_contradiction_scenario_triggers_redeliberation_and_preserves_old_record():
    result = run_scenario(scenario_contradiction(), "deliberative")
    types = [e["event_type"] for e in result.event_trace]
    assert "ReDeliberationTriggered" in types
    assert result.redeliberation_count >= 1
    # Final outcome should reflect the corrected (emergency) information,
    # not the original breakdown report.
    assert result.final_action_type == "escalate_emergency"

    redelib_events = [e for e in result.event_trace if e["event_type"] == "ReDeliberationTriggered"]
    superseded_id = redelib_events[0]["supersedes_id"]
    resolved_events = [e for e in result.event_trace if e["event_type"] == "DeliberationResolved"]
    superseded_records = [e for e in resolved_events if e["decision_id"] == superseded_id]
    assert len(superseded_records) == 1  # the old record's own content is still in the trace, untouched


# --- required scenario 5: rejected action ---


def test_rejected_action_scenario_aborts_not_finalizes():
    result = run_scenario(scenario_rejected_action(), "deliberative")
    assert result.final_commit_state == "aborted"
    assert result.actions_finalized == 0


# --- required scenario 6: barge-in ---


def test_barge_in_scenario_records_a_barge_in_event():
    result = run_scenario(scenario_barge_in(), "deliberative")
    assert result.barge_ins == 1
    types = [e["event_type"] for e in result.event_trace]
    assert "BargeIn" in types


# --- required scenario 7: constraint failure ---


def test_constraint_failure_scenario_triggers_self_critique_failure():
    result = run_scenario(scenario_constraint_failure(), "deliberative")
    assert result.self_critique_failures >= 1
    types = [e["event_type"] for e in result.event_trace]
    assert "SelfCritiqueFailed" in types
    # Out-of-radius dispatch must never be finalized regardless of "confirm".
    assert result.final_commit_state != "finalized"
    assert result.unsafe_finalizations == 0


# --- disconnect / session teardown (FR-3.3/FR-3.4) ---


def test_disconnect_before_confirmation_force_resolves_pending_action():
    result = run_scenario(scenario_disconnect_before_confirmation(), "deliberative")
    assert result.final_commit_state == "aborted"
    assert result.unsafe_finalizations == 0


# --- naive agent: legitimate baseline, not deliberately crippled ---


def test_naive_agent_can_execute_every_required_scenario():
    for scenario in required_scenarios():
        result = run_scenario(scenario, "naive")
        assert result.success is True, f"{scenario.scenario_id}: {result.error}"


def test_naive_agent_finalizes_immediately_without_checking_confirmation():
    result = run_scenario(scenario_normal_tow(), "naive")
    assert result.final_commit_state == "finalized"
    assert result.unsafe_finalizations == 1


def test_naive_agent_does_not_run_self_critique():
    result = run_scenario(scenario_constraint_failure(), "naive")
    assert result.self_critique_failures == 0
    # The naive agent's whole weakness: it finalizes the out-of-radius
    # dispatch that the deliberative agent correctly refuses.
    assert result.final_commit_state == "finalized"
    assert result.unsafe_finalizations == 1


def test_naive_agent_shares_the_same_commit_state_machine_semantics():
    """Naive still goes through PROPOSED -> PENDING_CONFIRMATION -> FINALIZED
    -- it just does it immediately, rather than skipping states outright.
    """
    result = run_scenario(scenario_normal_tow(), "naive")
    types = [e["event_type"] for e in result.event_trace]
    assert types.index("ActionProposed") < types.index("ActionPending") < types.index("ActionFinalized")


# --- ground truth oracle ---


def test_ground_truth_is_independent_of_scenario_module_and_matches_expectation():
    truth = compute_ground_truth(scenario_normal_tow())
    assert truth.expected_action_type == "dispatch_tow"
    assert truth.expected_finalization is True

    truth = compute_ground_truth(scenario_missing_information())
    assert truth.expected_action_type is None
    assert truth.expected_finalization is False

    truth = compute_ground_truth(scenario_emergency())
    assert truth.expected_action_type == "escalate_emergency"

    truth = compute_ground_truth(scenario_rejected_action())
    assert truth.expected_finalization is False


# --- naive vs deliberative comparison ---


def test_comparison_runs_both_agents_on_the_same_scenarios():
    comparisons = run_comparison(required_scenarios())
    assert len(comparisons) == len(required_scenarios())
    for c in comparisons:
        assert c.naive.scenario_id == c.deliberative.scenario_id == c.scenario_id


def test_deliberative_matches_ground_truth_on_every_required_scenario():
    comparisons = run_comparison(required_scenarios())
    for c in comparisons:
        assert c.deliberative_matches_ground_truth(), c.scenario_id


def test_naive_underperforms_deliberative_on_required_scenarios():
    comparisons = run_comparison(required_scenarios())
    naive_unsafe = sum(c.naive.unsafe_finalizations for c in comparisons)
    deliberative_unsafe = sum(c.deliberative.unsafe_finalizations for c in comparisons)
    assert deliberative_unsafe == 0
    assert naive_unsafe > deliberative_unsafe


# --- metrics ---


def test_metrics_are_calculated_from_actual_result_data_not_hardcoded():
    comparisons = run_comparison(required_scenarios())
    metrics = calculate_metrics(comparisons)
    assert set(metrics.keys()) == {"naive", "deliberative"}
    assert metrics["deliberative"].total_trials == len(required_scenarios())
    assert metrics["naive"].total_trials == len(required_scenarios())
    # unsafe_finalizations must come from the actual replay data, and the
    # deliberative agent's confirm() calls are always gated on a real
    # CONFIRM step -- see harness/replay.py's docstring.
    assert metrics["deliberative"].unsafe_finalizations == 0


def test_missed_emergencies_metric_reflects_real_emergency_scenarios():
    comparisons = run_comparison([scenario_emergency()])
    metrics = calculate_metrics(comparisons)
    assert metrics["deliberative"].missed_emergencies == 0


# --- reporting ---


def test_report_json_round_trips():
    comparisons = run_comparison(required_scenarios())
    report = build_report(comparisons)
    payload = report.to_json()
    reloaded = json.loads(payload)
    assert reloaded["total_trials"] == len(required_scenarios())
    assert "naive" in reloaded and "deliberative" in reloaded
    assert reloaded["deliberative"]["unsafe_finalizations"] == 0


def test_report_terminal_and_markdown_render_without_error():
    comparisons = run_comparison(required_scenarios())
    report = build_report(comparisons)
    terminal_text = report.to_terminal()
    markdown_text = report.to_markdown()
    assert "DELIBERATIVE AGENT" in terminal_text
    assert "NAIVE AGENT" in terminal_text
    assert "# Triage Line Evaluation" in markdown_text


def test_report_pass_fail_reflects_deliberative_safety_on_required_scenarios():
    comparisons = run_comparison(required_scenarios())
    report = build_report(comparisons)
    assert report.passed is True


# --- 100+ trials, determinism ---


def test_trial_matrix_generates_the_requested_count_deterministically():
    first = generate_trial_matrix(count=120, seed=42)
    second = generate_trial_matrix(count=120, seed=42)
    assert len(first) == 120
    assert [s.scenario_id for s in first] == [s.scenario_id for s in second]
    assert [s.full_caller_text() for s in first] == [s.full_caller_text() for s in second]


def test_all_trials_meets_the_100_trial_minimum():
    trials = all_trials(matrix_count=120)
    assert len(trials) >= 100


def test_100_plus_trials_execute_successfully_for_both_agents():
    trials = all_trials(matrix_count=120)
    comparisons = run_comparison(trials)
    assert len(comparisons) >= 100
    for c in comparisons:
        assert c.naive.success is True, f"{c.scenario_id} (naive): {c.naive.error}"
        assert c.deliberative.success is True, f"{c.scenario_id} (deliberative): {c.deliberative.error}"


def test_deliberative_has_zero_unsafe_finalizations_across_the_full_matrix():
    trials = all_trials(matrix_count=120)
    comparisons = run_comparison(trials)
    metrics = calculate_metrics(comparisons)
    assert metrics["deliberative"].unsafe_finalizations == 0


def test_full_matrix_report_is_deterministic_across_repeated_runs():
    trials_a = all_trials(matrix_count=120)
    trials_b = all_trials(matrix_count=120)
    report_a = build_report(run_comparison(trials_a))
    report_b = build_report(run_comparison(trials_b))
    assert report_a.to_dict() == report_b.to_dict()


# --- provider/architecture independence ---


def test_harness_modules_do_not_import_concrete_providers():
    import ast
    from pathlib import Path

    forbidden = {"MockSTT", "MockLLM", "MockTTS", "MockAudioIO", "livekit", "deepgram", "groq", "rime"}
    harness_dir = Path(__file__).resolve().parent.parent / "harness"
    for path in harness_dir.rglob("*.py"):
        source = path.read_text()
        lowered = source.lower()
        for term in forbidden:
            assert term.lower() not in lowered, f"{path} references forbidden provider term {term!r}"
