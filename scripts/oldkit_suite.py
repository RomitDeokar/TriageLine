#!/usr/bin/env python3
"""Old-kit regression suite: run every scenario in scenarios/ and scenarios_extra/ at time scale 1.0.

These are OLD-KIT scores from the queue-based harness. They are not FDB-v3 results. We use them only to
catch regressions in the coordinator.

    python scripts/oldkit_suite.py                 # table
    python scripts/oldkit_suite.py --json out.json # also write per-scenario scores
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _one(path: str):
    from harness.runner import run_scenario
    from harness.scorer import score_scenario
    from run_local import load_agent_factory
    sc = json.load(open(path))
    trace = run_scenario(sc, load_agent_factory("agent.agent:ParticipantAgent"), time_scale=1.0,
                         verbose=False, tail_ms=6000.0)
    r = score_scenario(sc, trace)
    bad = [e.get("kind") for e in trace if e.get("kind") in ("agent_crash", "protocol_error", "tool_abandoned")]
    return path, round(r["total"], 1), bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    paths = sorted(glob.glob("scenarios/*.json")) + sorted(glob.glob("scenarios_extra/*.json"))
    with ProcessPoolExecutor(a.jobs) as ex:
        rows = list(ex.map(_one, paths))
    pub = [s for p, s, _ in rows if p.startswith("scenarios/")]
    for p, s, bad in rows:
        print(f"{s:6.1f}  {os.path.basename(p)}  {'!! ' + ','.join(bad) if bad else ''}")
    print(f"old-kit public mean: {sum(pub) / len(pub):.1f} over {len(pub)} scenarios")
    if a.json:
        json.dump([{"scenario": p, "score": s, "issues": b} for p, s, b in rows], open(a.json, "w"), indent=2)


if __name__ == "__main__":
    main()
