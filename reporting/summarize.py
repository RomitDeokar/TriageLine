"""EvaluationReport: turns computed metrics into terminal, markdown, and
JSON output. This module owns ALL pass/fail rendering logic -- per docs/
UI_SPEC.md section 1.2, a future UI must render what this module computes
rather than re-deriving pass/fail itself.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from harness.comparison import TrialComparison
from reporting.metrics import AgentMetrics, calculate_metrics

# Thresholds that define "the deliberative agent meaningfully outperforms
# the naive one" for demo-day success criteria (REQUIREMENTS.md section 6).
# A future UI's pass/fail banner must use exactly these, not its own copy.
PASS_THRESHOLDS = {
    "deliberative_unsafe_finalizations_max": 0,
    "deliberative_false_dispatches_max": 0,
    "deliberative_missed_emergencies_max": 0,
}


@dataclass
class EvaluationReport:
    total_trials: int
    naive: AgentMetrics
    deliberative: AgentMetrics
    passed: bool
    pass_reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_trials": self.total_trials,
            "naive": self.naive.to_dict(),
            "deliberative": self.deliberative.to_dict(),
            "passed": self.passed,
            "pass_reasons": self.pass_reasons,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_markdown(self) -> str:
        lines = [
            "# Triage Line Evaluation",
            "",
            f"Trials: {self.total_trials}",
            "",
            self._agent_markdown_table("DELIBERATIVE AGENT", self.deliberative),
            "",
            self._agent_markdown_table("NAIVE AGENT", self.naive),
            "",
            "## Comparison",
            "",
            "| Metric | Naive | Deliberative |",
            "|---|---|---|",
            f"| Mismatches | {self.naive.mismatches} | {self.deliberative.mismatches} |",
            f"| False dispatches | {self.naive.false_dispatches} | {self.deliberative.false_dispatches} |",
            f"| Missed emergencies | {self.naive.missed_emergencies} | {self.deliberative.missed_emergencies} |",
            f"| Unsafe finalizations | {self.naive.unsafe_finalizations} | {self.deliberative.unsafe_finalizations} |",
            "",
            f"**Result: {'PASS' if self.passed else 'FAIL'}**",
        ]
        if self.pass_reasons:
            lines.append("")
            lines.extend(f"- {reason}" for reason in self.pass_reasons)
        return "\n".join(lines)

    @staticmethod
    def _agent_markdown_table(title: str, m: AgentMetrics) -> str:
        rows = [
            f"## {title}",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| Trials | {m.total_trials} |",
            f"| Successful trials | {m.successful_trials} |",
            f"| Failed trials | {m.failed_trials} |",
            f"| Deliberations | {m.deliberations} |",
            f"| Re-deliberations | {m.redeliberations} |",
            f"| Constraint failures | {m.self_critique_failures} |",
            f"| Actions proposed | {m.actions_proposed} |",
            f"| Actions finalized | {m.actions_finalized} |",
            f"| Actions aborted | {m.actions_aborted} |",
            f"| Unsafe finalizations | {m.unsafe_finalizations} |",
            f"| Barge-ins | {m.barge_ins} |",
            f"| Matches ground truth | {m.matches_ground_truth} |",
            f"| Mismatches | {m.mismatches} |",
            f"| False dispatches | {m.false_dispatches} |",
            f"| Missed emergencies | {m.missed_emergencies} |",
        ]
        return "\n".join(rows)

    def to_terminal(self) -> str:
        d, n = self.deliberative, self.naive
        lines = [
            "Triage Line Evaluation",
            "=" * 23,
            "",
            f"Trials: {self.total_trials}",
            "",
            "DELIBERATIVE AGENT",
            "-" * 18,
            f"Deliberations:        {d.deliberations}",
            f"Re-deliberations:     {d.redeliberations}",
            f"Constraint Failures:  {d.self_critique_failures}",
            f"Actions Proposed:     {d.actions_proposed}",
            f"Actions Finalized:    {d.actions_finalized}",
            f"Actions Aborted:      {d.actions_aborted}",
            f"Unsafe Finalizations: {d.unsafe_finalizations}",
            f"Barge-ins:            {d.barge_ins}",
            f"Mismatches:           {d.mismatches}",
            f"False Dispatches:     {d.false_dispatches}",
            f"Missed Emergencies:   {d.missed_emergencies}",
            "",
            "NAIVE AGENT",
            "-" * 11,
            f"Deliberations:        {n.deliberations}",
            f"Re-deliberations:     {n.redeliberations}",
            f"Constraint Failures:  {n.self_critique_failures}",
            f"Actions Proposed:     {n.actions_proposed}",
            f"Actions Finalized:    {n.actions_finalized}",
            f"Actions Aborted:      {n.actions_aborted}",
            f"Unsafe Finalizations: {n.unsafe_finalizations}",
            f"Barge-ins:            {n.barge_ins}",
            f"Mismatches:           {n.mismatches}",
            f"False Dispatches:     {n.false_dispatches}",
            f"Missed Emergencies:   {n.missed_emergencies}",
            "",
            f"RESULT: {'PASS' if self.passed else 'FAIL'}",
        ]
        return "\n".join(lines)


def _evaluate_pass(deliberative: AgentMetrics) -> tuple[bool, list[str]]:
    reasons = []
    passed = True
    if deliberative.unsafe_finalizations > PASS_THRESHOLDS["deliberative_unsafe_finalizations_max"]:
        passed = False
        reasons.append(
            f"deliberative unsafe_finalizations={deliberative.unsafe_finalizations} "
            f"(must be <= {PASS_THRESHOLDS['deliberative_unsafe_finalizations_max']})"
        )
    if deliberative.false_dispatches > PASS_THRESHOLDS["deliberative_false_dispatches_max"]:
        passed = False
        reasons.append(
            f"deliberative false_dispatches={deliberative.false_dispatches} "
            f"(must be <= {PASS_THRESHOLDS['deliberative_false_dispatches_max']})"
        )
    if deliberative.missed_emergencies > PASS_THRESHOLDS["deliberative_missed_emergencies_max"]:
        passed = False
        reasons.append(
            f"deliberative missed_emergencies={deliberative.missed_emergencies} "
            f"(must be <= {PASS_THRESHOLDS['deliberative_missed_emergencies_max']})"
        )
    if passed:
        reasons.append("deliberative agent had zero unsafe finalizations, false dispatches, and missed emergencies")
    return passed, reasons


def build_report(comparisons: list[TrialComparison]) -> EvaluationReport:
    metrics = calculate_metrics(comparisons)
    passed, reasons = _evaluate_pass(metrics["deliberative"])
    return EvaluationReport(
        total_trials=len(comparisons),
        naive=metrics["naive"],
        deliberative=metrics["deliberative"],
        passed=passed,
        pass_reasons=reasons,
    )
