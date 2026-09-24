"""Comparison runner: runs the same scenario set through both agents.

Does not fabricate or duplicate results -- every ReplayResult here comes
from an actual harness.replay.run_scenario() call. See reporting/ for how
these are turned into metrics and a report.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from harness.ground_truth import GroundTruth, compute_ground_truth
from harness.replay import ReplayResult, run_scenario
from harness.scenario import Scenario


@dataclass
class TrialComparison:
    scenario_id: str
    ground_truth: GroundTruth
    naive: ReplayResult
    deliberative: ReplayResult

    def naive_matches_ground_truth(self) -> bool:
        return _matches(self.naive, self.ground_truth)

    def deliberative_matches_ground_truth(self) -> bool:
        return _matches(self.deliberative, self.ground_truth)


def _matches(result: ReplayResult, truth: GroundTruth) -> bool:
    if not result.success:
        return False
    if truth.expected_finalization:
        return (
            result.final_commit_state == "finalized"
            and result.final_action_type == truth.expected_action_type
        )
    return result.final_commit_state != "finalized"


def run_comparison(scenarios: list[Scenario]) -> list[TrialComparison]:
    comparisons: list[TrialComparison] = []
    for index, scenario in enumerate(scenarios):
        truth = compute_ground_truth(scenario)
        naive_result = run_scenario(scenario, "naive", trial_index=index)
        deliberative_result = run_scenario(scenario, "deliberative", trial_index=index)
        comparisons.append(
            TrialComparison(
                scenario_id=scenario.scenario_id,
                ground_truth=truth,
                naive=naive_result,
                deliberative=deliberative_result,
            )
        )
    return comparisons
