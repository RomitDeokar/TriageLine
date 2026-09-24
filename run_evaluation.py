"""Triage Line — Phase 6 evaluation entry point.

Runs the required scenarios plus a deterministic trial matrix (>= 100
trials, fixed seed) through both the naive and deliberative agents,
computes metrics from the actual results, and writes the report to
results/ as both JSON and Markdown, in addition to printing it.

Usage:
    python3 run_evaluation.py [trial_count]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from harness.comparison import run_comparison
from harness.scenarios.definitions import all_trials
from reporting.summarize import build_report

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def main() -> None:
    matrix_count = int(sys.argv[1]) if len(sys.argv) > 1 else 120

    scenarios = all_trials(matrix_count=matrix_count)
    print(f"Running {len(scenarios)} trials per agent (naive + deliberative)...")

    start = time.perf_counter()
    comparisons = run_comparison(scenarios)
    elapsed = time.perf_counter() - start

    report = build_report(comparisons)

    print()
    print(report.to_terminal())
    print()
    print(f"Elapsed: {elapsed:.3f}s for {len(scenarios)} scenarios x 2 agents.")

    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "evaluation_report.json").write_text(report.to_json())
    (RESULTS_DIR / "evaluation_report.md").write_text(report.to_markdown())
    print(f"\nWrote {RESULTS_DIR / 'evaluation_report.json'}")
    print(f"Wrote {RESULTS_DIR / 'evaluation_report.md'}")

    raise SystemExit(0 if report.passed else 1)


if __name__ == "__main__":
    main()
