#!/usr/bin/env python3
"""results/summarize_fdb_v3.py — turn one run directory into results/results.md.

    python3 results/summarize_fdb_v3.py results/<timestamp>/        (called by run_fdb_v3.sh stage 6)
    python3 results/summarize_fdb_v3.py                             (newest results/<timestamp>/)

Reads only files written by the OFFICIAL evaluators (<provider>_pass_rate_report.json,
<provider>_evaluation_report.json, optional <provider>_latency_report.json) plus run_config.json.
It never estimates a number: a metric that was not produced is printed as "not produced".
It also reports the dev / held-out split (livekit_agent/fdb_split.py) so tuning on the public
set can be checked for overfitting.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
sys.path.insert(0, str(ROOT / "livekit_agent"))
from fdb_split import side  # noqa: E402


def _load(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def _pct(x):
    return "not produced" if x is None else f"{100 * x:.1f}%"


def newest_run() -> Path | None:
    runs = sorted(p for p in RESULTS.iterdir() if p.is_dir() and p.name[:2] == "20")
    return runs[-1] if runs else None


def split_rates(pass_report: dict) -> dict:
    out = {}
    for s in ("dev", "heldout"):
        rows = [r for r in pass_report.get("scenario_results", []) if side(r["scenario_id"]) == s]
        if rows:
            out[s] = (sum(r["passed"] for r in rows), len(rows))
    return out


def main():
    run = Path(sys.argv[1]) if len(sys.argv) > 1 else newest_run()
    lines = ["# FDB-v3 results — TriageLine", "",
             f"Generated: {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}", ""]
    if run is None or not run.exists():
        lines += ["## Status: NOT RUN", "", "No run directory under `results/`. Run `./run_fdb_v3.sh` "
                  "(or `./run_fdb_v3.sh --offline-text` without keys).", ""]
        (RESULTS / "results.md").write_text("\n".join(lines))
        print("\n".join(lines))
        return
    cfg = _load(run / "run_config.json") or {}
    prov = cfg.get("provider", "triageline")
    pr = _load(run / f"{prov}_pass_rate_report.json")
    ev = _load(run / f"{prov}_evaluation_report.json")
    lat = _load(run / f"{prov}_latency_report.json")
    mode = cfg.get("mode", "?")
    lines += [
        f"Run directory: `{run.relative_to(ROOT)}`  ",
        f"Mode: **{mode}**" + ("  (official data + official evaluators; no LiveKit transport, no audio latency)"
                              if mode == "offline_text_replay" else ""),
        f"Provider name: `{prov}` · LLM judge: **{'on (gpt-4o)' if cfg.get('llm_judge') else 'off (exact match = lower bound)'}**"
        f" · examples: {pr.get('total_scenarios') if pr else '?'}{' (limited)' if cfg.get('limit') else ''}  ",
        f"FDB-v3 commit: `{cfg.get('fdb_commit', '?')}` · TriageLine commit: `{cfg.get('triageline_commit', '?')}` · {cfg.get('python', '')}  ",
        "Agent: custom LiveKit agent (Silero VAD → hosted STT → rule-based ParticipantAgent, no LLM → hosted TTS)"
        f" · STT={cfg.get('stt_provider', '?')} TTS={cfg.get('tts_provider', '?')}", "",
    ]
    if pr is None:
        lines += ["## Status: INCOMPLETE", "", "The pass-rate report was not produced; see `run.log` in the run directory.", ""]
    else:
        bm = (ev or {}).get("by_metric", {})
        lines += ["## Headline (official evaluators)", "",
                  "| strict pass rate | tool-selection acc | argument acc | response quality | turn-take rate |",
                  "|---|---|---|---|---|",
                  f"| **{_pct(pr.get('overall_pass_rate'))}** ({pr.get('passed')}/{pr.get('total_scenarios')}) "
                  f"| {_pct(bm.get('tool_selection_acc'))} | {_pct(bm.get('argument_acc'))} | {_pct(bm.get('response_qual'))} "
                  f"| {_pct((ev or {}).get('turn_taking', {}).get('turn_take_rate'))} |", ""]
        sp = split_rates(pr)
        if sp:
            lines += ["### Anti-overfitting: dev vs held-out (hash split, `livekit_agent/fdb_split.py`)", "",
                      "| split | passed | rate |", "|---|---|---|"]
            for s, (k, n) in sp.items():
                lines.append(f"| {s} | {k}/{n} | {100 * k / n:.1f}% |")
            lines.append("")
        for key, title in (("by_domain", "By domain"), ("by_disfluency_feature", "By disfluency"),
                           ("by_difficulty", "By difficulty"), ("by_num_tools", "By number of tools")):
            if pr.get(key):
                lines += [f"**{title}:** " + " · ".join(f"{k} {_pct(v)}" for k, v in pr[key].items()), ""]
        fb = pr.get("failure_breakdown")
        if fb:
            lines += ["**Failures:** " + ", ".join(f"{k}={v}" for k, v in fb.items()), ""]
    lat_ev = (ev or {}).get("latency", {})
    if lat_ev.get("avg_response_latency_s") is not None:
        lines += ["## Latency (official, audio-derived)", "",
                  f"avg first response {lat_ev['avg_response_latency_s']:.2f}s (std {lat_ev.get('std_response_latency_s') or 0:.2f}s, "
                  f"min {lat_ev.get('min_latency_s')}, max {lat_ev.get('max_latency_s')}); "
                  f"interruption rate {_pct(lat_ev.get('interruption_rate'))}", ""]
    elif mode == "offline_text_replay":
        lines += ["## Latency", "", "Not measured in offline mode (no audio). Run the full `./run_fdb_v3.sh` for latency.", ""]
    if lat:
        lines += ["Fine-grained latency report: `" + f"{prov}_latency_report.json`", ""]
    lines += ["Files: " + ", ".join(sorted(p.name for p in run.iterdir() if p.is_file())), ""]
    text = "\n".join(lines)
    (RESULTS / "results.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
