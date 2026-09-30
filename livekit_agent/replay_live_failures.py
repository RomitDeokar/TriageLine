#!/usr/bin/env python3
"""Fast per-example failure replay for the LIVE FDB-v3 run (diagnostic tool).

Why this exists: iterating on a 2-hour live LiveKit run is far too slow. This script takes the
transcripts the LIVE run actually recorded (Deepgram output + its fragment timing) and replays them
through the SAME adapter/agent offline, then diffs expected vs live-recorded vs replayed tool calls.
A fix is measured in seconds instead of hours.

It is a DIAGNOSTIC only: it does not touch the agent, the gateway, the workers or the official
runner, and nothing imports it. The canonical benchmark path stays `./run_fdb_v3.sh`.

    python livekit_agent/replay_live_failures.py                 # summarise all recorded examples
    python livekit_agent/replay_live_failures.py --only ecommerce # filter by example id prefix
    python livekit_agent/replay_live_failures.py --json out.json  # machine-readable detail

Definition of a failure (matching the official evaluator's spirit):
    wrong_tools  - the ordered tool-name list differs from expected
    wrong_args   - tool names match but an argument differs
    extra_call   - more calls than expected (e.g. the same call twice)
    missing_call - fewer calls than expected
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FDB_REPO = ROOT / "livekit_agent" / ".fdb_v3_repo" / "v3"
DATA = ROOT / "livekit_agent" / ".fdb_v3_repo" / "v3" / "fdb_v3_data_released"

from livekit_agent.adapter import TriageAdapter  # noqa: E402
from livekit_agent.fdb_tools import FDB_TOOLS  # noqa: E402


def load_registry():
    p = str(FDB_REPO if FDB_REPO.is_dir() else ROOT / "livekit_agent")
    if p not in sys.path:
        sys.path.append(p)
    import mock_apis  # official, unmodified
    return mock_apis.MockAPIRegistry(latency_profile="instant", enable_logging=False)


def expected_from_benchmark() -> dict:
    """scenario id -> expected tool calls (the runner's per-result copy can be empty)."""
    bench = json.loads((FDB_REPO / "benchmark_data_v2.json").read_text(encoding="utf-8"))
    out = {}
    for s in bench.get("scenarios", []):
        out[s["id"]] = s.get("expected_tool_calls") or []
    return out


def replay_actual_calls(fragments: list[str], gaps: list[float], busy_wait: float = 0.05) -> list[dict]:
    """Drive the same adapter/agent offline with the live fragments and return the emitted calls."""
    registry = load_registry()
    calls: list[dict] = []

    async def main():
        async def execute(cid, api, args):
            try:
                res = await asyncio.to_thread(registry.call, api, **args)
            except Exception as e:  # official mocks raise on bad kwargs
                res = {"status": "error", "error": "invalid_args", "message": f"{type(e).__name__}: {e}"}
            calls.append({"function": api, "args": args})
            st = "error" if (isinstance(res, dict) and res.get("status") == "error") else "success"
            await adapter.on_tool_completed(cid, res, status=st)

        async def speak(kind, text):
            pass

        async def cancel(cid):
            pass

        adapter = TriageAdapter(tool_executor=execute, tool_canceller=cancel, speak=speak,
                                settle_s=1.0, max_settle_s=2.0)
        await adapter.start(FDB_TOOLS)
        try:
            for i, txt in enumerate(fragments):
                await adapter.on_user_final(txt)
                gap = gaps[i] if i < len(gaps) else 0.4
                # the live adapter runs on a real clock; compress proportionally but keep ordering
                await asyncio.sleep(max(0.05, min(gap, 2.0) / 4))
            await adapter.wait_idle(timeout=20.0)
        except TimeoutError:
            pass
        finally:
            await adapter.stop()

    asyncio.run(main())
    return calls


def fragments_from_result(res: dict) -> tuple[list[str], list[float]]:
    """Prefer the real caller fragments (input_asr_chunks) so the live fragmentation is reproduced."""
    chunks = res.get("input_asr_chunks") or []
    frags, gaps, prev_end = [], [], None
    for c in chunks:
        txt = (c.get("text") or "").strip()
        if not txt:
            continue
        ts = c.get("timestamp") or [None, None]
        end = ts[1] if len(ts) > 1 else None
        gaps.append((end - prev_end) if (end is not None and prev_end is not None) else 0.4)
        frags.append(txt)
        prev_end = end if end is not None else prev_end
    if frags:
        return frags, gaps
    # fall back to the whole transcript as one turn
    whole = (res.get("input_transcript") or "").strip()
    return ([whole] if whole else []), [0.4]


def _args_match(e_args: dict, a_args: dict) -> bool:
    """The official evaluator resolves $RESULT_n.* references against the previous call's result;
    here such an arg is treated as satisfied when the actual value is non-empty."""
    if set(e_args) != set(a_args):
        return False
    for k, ev in e_args.items():
        av = a_args.get(k)
        if isinstance(ev, str) and ev.startswith("$RESULT"):
            if av in (None, ""):
                return False
        elif ev != av:
            return False
    return True


def classify(expected: list[dict], actual: list[dict]) -> tuple[bool, str]:
    e_names = [c.get("function") for c in expected]
    a_names = [c.get("function") for c in actual]
    if e_names == a_names:
        e_args = [c.get("args") or c.get("arguments") or {} for c in expected]
        a_args = [c.get("args") or {} for c in actual]
        if all(_args_match(e, a) for e, a in zip(e_args, a_args)):
            return True, "pass"
        return False, "wrong_args"
    if sorted(e_names) == sorted(a_names) and len(a_names) > len(set(a_names)):
        return False, "extra_call"
    if len(a_names) > len(e_names):
        return False, "extra_call"
    if len(a_names) < len(e_names):
        return False, "missing_call"
    return False, "wrong_tools"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json", default="")
    ap.add_argument("--no-replay", action="store_true", help="only diff expected vs the live recording")
    a = ap.parse_args()

    exp_by_id = expected_from_benchmark()
    rows, counts = [], {}
    dirs = sorted(p for p in Path(a.data).iterdir() if p.name and p.is_dir())
    if a.only:
        dirs = [d for d in dirs if d.name.startswith(a.only)]
    if a.limit:
        dirs = dirs[: a.limit]

    for d in dirs:
        f = d / "result_triageline.json"
        if not f.exists():
            continue
        res = json.loads(f.read_text(encoding="utf-8"))
        if not (res.get("transcript") or "").strip():
            continue  # silent room: nothing to diagnose
        sid = res.get("example_id") or d.name
        scenario = next((k for k in exp_by_id if sid.startswith(k)), sid)
        expected = exp_by_id.get(scenario, [])
        live_calls = res.get("actual_tool_calls") or []
        frags, gaps = fragments_from_result(res)
        replay = [] if a.no_replay else replay_actual_calls(frags, gaps)
        ok_live, why_live = classify(expected, live_calls)
        ok_replay, why_replay = classify(expected, replay) if not a.no_replay else (ok_live, why_live)
        counts[why_replay] = counts.get(why_replay, 0) + 1
        rows.append({
            "example": d.name, "scenario": scenario,
            "expected": [c.get("function") for c in expected],
            "live": [c.get("function") for c in live_calls],
            "replay": [c.get("function") for c in replay],
            "verdict_replay": why_replay, "verdict_live": why_live,
            "fragments": len(frags),
        })
        flag = "OK " if (ok_live and ok_replay) else "MIS"
        print(f"[{flag}] {d.name[:38]:38s} exp={rows[-1]['expected']} live={rows[-1]['live']} "
              f"replay={rows[-1]['replay']} ({why_replay})", flush=True)

    total = len(rows)
    print(f"\n=== {total} examples with a response ===")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {k:12s} {v:3d}  ({v / max(1, total) * 100:.0f}%)")
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        print("wrote", a.json)


if __name__ == "__main__":
    main()
