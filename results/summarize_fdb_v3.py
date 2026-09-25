#!/usr/bin/env python3
"""results/summarize_fdb_v3.py — turns results/raw/* into results/results.md.

Called as the last stage of run_fdb_v3.sh. If the official FDB-v3 eval never
produced raw output (because an earlier stage stopped the pipeline), this
writes an honest NOT_RUN summary instead of fabricating numbers.
"""
import datetime
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.path.join(ROOT, "results")
RAW_DIR = os.path.join(RESULTS_DIR, "raw")
CONFIG_PATH = os.path.join(RESULTS_DIR, "config.json")
OUT_PATH = os.path.join(RESULTS_DIR, "results.md")

# Where an actual FDB-v3 run's official metrics JSON would land, once Stage 5
# of run_fdb_v3.sh is wired to the real eval command from the cloned repo.
METRICS_CANDIDATE = os.path.join(RAW_DIR, "fdb_v3_metrics.json")


def main():
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)

    metrics = None
    if os.path.exists(METRICS_CANDIDATE):
        with open(METRICS_CANDIDATE) as f:
            metrics = json.load(f)

    lines = []
    lines.append("# FDB-v3 v3 evaluation — results")
    lines.append("")
    lines.append(f"Generated: {datetime.datetime.now(datetime.timezone.utc).isoformat()}")
    lines.append(f"Benchmark: {cfg['benchmark']} ({cfg['benchmark_repo']})")
    lines.append(f"Agent: {cfg['agent_under_test']['entrypoint']} "
                 f"({cfg['agent_under_test']['pipeline']})")
    lines.append(f"Model/provider: {cfg['model']} / {cfg['provider']}")
    lines.append("")

    if metrics is None:
        lines.append("## Status: NOT RUN")
        lines.append("")
        lines.append(f"Stopped at stage: **{cfg['run']['stopped_at_stage']}**")
        lines.append("")
        lines.append("No official FDB-v3 metrics were produced. The table below is "
                      "intentionally empty rather than estimated or fabricated.")
        lines.append("")
        lines.append("| tool-selection F1 | argument accuracy | strict pass rate | latency |")
        lines.append("|---|---|---|---|")
        lines.append("| N/A | N/A | N/A | N/A |")
        lines.append("")
        lines.append("See `results/raw/environment_check.log` for exactly what blocked the run, "
                      "and `results/config.json` for the full attempted configuration.")
    else:
        lines.append("## Status: COMPLETE")
        lines.append("")
        lines.append("| tool-selection F1 | argument accuracy | strict pass rate | latency (p50/p95) |")
        lines.append("|---|---|---|---|")
        lines.append(f"| {metrics.get('tool_selection_f1', 'N/A')} "
                     f"| {metrics.get('argument_accuracy', 'N/A')} "
                     f"| {metrics.get('strict_pass_rate', 'N/A')} "
                     f"| {metrics.get('latency_p50', 'N/A')}/{metrics.get('latency_p95', 'N/A')} |")

    with open(OUT_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
