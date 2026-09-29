#!/usr/bin/env python3
"""Score the agent on the independent held-out paraphrase set (scenarios_heldout/).

Drives the SAME LiveKit adapter + ParticipantAgent the worker uses, against the official FDB-v3 mock
tools, with the benchmark policy (no clarifying questions) and no LLM, so the number measures the rules
on items they were never tuned on. Strict scoring: ordered (tool, args-subset) list must match.

    python3 scripts/heldout_eval.py            # table + summary; exit 0 always (it is a report)
    python3 scripts/heldout_eval.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "livekit_agent")]
os.environ.setdefault("TRIAGELINE_BENCHMARK_POLICY", "1")
os.environ.setdefault("TRIAGELINE_LLM_PLANNER", "0")
os.environ.setdefault("TRIAGELINE_OFFLINE", "1")

from scripts.fdb_probe import run  # noqa: E402

STATE = {"book_flight", "update_identity_doc", "modify_autopay", "update_search_filter", "add_to_cart"}


def _eq(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


def score(item, calls) -> tuple[bool, str]:
    exp = item["expected"]
    got = [(api, args) for api, args in calls]
    if not exp:
        bad = [a for a, _ in got if a in STATE]
        return (not bad, "unexpected state change: " + ",".join(bad) if bad else "ok")
    names = [a for a, _ in got]
    if names != [e[0] for e in exp]:
        return False, f"tools {names} != {[e[0] for e in exp]}"
    for (api, args), (_, want) in zip(got, exp):
        for k, v in want.items():
            if k not in args or not _eq(args[k], v):
                return False, f"{api}.{k}={args.get(k)!r} != {v!r}"
    return True, "ok"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default=str(ROOT / "scenarios_heldout/heldout_v1.json"))
    ap.add_argument("--json")
    ap.add_argument("--settle", type=float, default=float(os.environ.get("TRIAGELINE_SETTLE_S", "1.0")))
    a = ap.parse_args()
    data = json.loads(Path(a.set).read_text())
    rows, by = [], defaultdict(lambda: [0, 0])
    for it in data["items"]:
        # the live worker commit gate (cascaded_agent SETTLE_S, benchmark default 1.0 s) merges paused fragments
        calls, spoken = run([(t, 0.3) for t in it["turns"]], tail=1.5, settle_s=a.settle)
        ok, why = score(it, calls)
        rows.append({"id": it["id"], "domain": it["domain"], "feature": it["feature"], "pass": ok, "why": why,
                     "calls": calls, "spoken": [t for _, t in spoken][-2:]})
        for k in (it["domain"], it["feature"]):
            by[k][0] += ok
            by[k][1] += 1
        print(f"{'PASS' if ok else 'FAIL'} {it['id']} [{it['domain']}/{it['feature']}] {'' if ok else why}")
    n, k = len(rows), sum(r["pass"] for r in rows)
    print(f"\nHeld-out strict pass rate: {k}/{n} = {100 * k / n:.1f}%  (rules only, benchmark policy, no LLM)")
    print("  " + " · ".join(f"{g} {v[0]}/{v[1]}" for g, v in sorted(by.items())))
    if a.json:
        Path(a.json).write_text(json.dumps({"set": data["name"], "passed": k, "total": n, "rows": rows}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
