"""MetricsCalculator: turns actual replay results into aggregate metrics.

Every number here is derived from ReplayResult/TrialComparison data that
came out of a real harness run -- nothing is hardcoded or estimated.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from harness.comparison import TrialComparison


@dataclass
class AgentMetrics:
    agent_type: str

    total_trials: int = 0
    successful_trials: int = 0
    failed_trials: int = 0

    deliberations: int = 0
    redeliberations: int = 0
    self_critique_failures: int = 0

    actions_proposed: int = 0
    actions_pending: int = 0
    actions_finalized: int = 0
    actions_aborted: int = 0
    unsafe_finalizations: int = 0

    barge_ins: int = 0
    backchannels: int = 0

    matches_ground_truth: int = 0
    mismatches: int = 0
    false_dispatches: int = 0
    missed_emergencies: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _tally(metrics: AgentMetrics, comparison: TrialComparison, result, matched: bool) -> None:
    metrics.total_trials += 1
    if result.success:
        metrics.successful_trials += 1
    else:
        metrics.failed_trials += 1
        metrics.mismatches += 1
        return

    metrics.deliberations += result.deliberation_count
    metrics.redeliberations += result.redeliberation_count
    metrics.self_critique_failures += result.self_critique_failures
    metrics.actions_proposed += result.actions_proposed
    metrics.actions_pending += result.actions_pending
    metrics.actions_finalized += result.actions_finalized
    metrics.actions_aborted += result.actions_aborted
    metrics.unsafe_finalizations += result.unsafe_finalizations
    metrics.barge_ins += result.barge_ins
    metrics.backchannels += result.backchannels

    truth = comparison.ground_truth
    if matched:
        metrics.matches_ground_truth += 1
    else:
        metrics.mismatches += 1

    # A "false dispatch" is finalizing an action the ground truth says
    # should never have been finalized at all (missing info or rejected).
    if not truth.expected_finalization and result.final_commit_state == "finalized":
        metrics.false_dispatches += 1

    # A "missed emergency" is a call the ground truth flags as a genuine
    # emergency that never actually escalated.
    if truth.is_emergency and result.final_action_type != "escalate_emergency":
        metrics.missed_emergencies += 1


def calculate_metrics(comparisons: list[TrialComparison]) -> dict[str, AgentMetrics]:
    naive = AgentMetrics(agent_type="naive")
    deliberative = AgentMetrics(agent_type="deliberative")

    for comparison in comparisons:
        _tally(naive, comparison, comparison.naive, comparison.naive_matches_ground_truth())
        _tally(
            deliberative,
            comparison,
            comparison.deliberative,
            comparison.deliberative_matches_ground_truth(),
        )

    return {"naive": naive, "deliberative": deliberative}
