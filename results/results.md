# FDB-v3 results — TriageLine

Generated: 2026-09-29T15:14:52+00:00

Run directory: `results\20260929T151342Z`  
Mode: **offline_text_replay**  (official data + official evaluators; no LiveKit transport, no audio latency)
Provider name: `triageline_text` · LLM judge: **off (exact match = lower bound)** · examples: 100  
FDB-v3 commit: `3e799c45a045256f47d5f1c9cda90157e2d2ec9e` · TriageLine commit: `` · 3.11.15  
Agent: custom LiveKit agent (Silero VAD → STT → ParticipantAgent: rules only (LLM planner off) → TTS) · STT=deepgram:nova-3 TTS=deepgram:aura-2-andromeda-en

> **Diagnostic only.** Offline text replay feeds the official transcripts to the agent without LiveKit, STT or TTS. It is **not** the scored live FDB-v3 run and must not be reported as one.

## Headline (official evaluators)

| strict pass rate | tool-selection acc | argument acc | response quality | turn-take rate |
|---|---|---|---|---|
| **91.0%** (91/100) | 98.6% | 93.7% | not produced | 100.0% |

### Dev vs hash split of the public set (`livekit_agent/fdb_split.py`)

Both halves come from the same public benchmark that rules were developed against, so this is **not** an independent held-out set. See `scenarios_heldout/` for the independent paraphrase set.

| split | passed | rate |
|---|---|---|
| dev | 48/53 | 90.6% |
| heldout | 43/47 | 91.5% |

**By domain:** ecommerce_support 86.2% · finance_billing 100.0% · housing_location 84.6% · travel_identity 95.0%

**By disfluency:** PAUSE 83.3% · FILLER 100.0% · HESITATION 90.0% · FALSE_START 100.0% · SELF_CORRECTION 82.4%

**By difficulty:** easy 91.7% · medium 94.1% · hard 86.7%

**By number of tools:** 1 92.4% · 2 83.3% · 3 93.8%

**Failures:** wrong_tools=3, wrong_arguments=6

## Latency

Not measured in offline mode (no audio). Run the full `./run_fdb_v3.sh` for latency.

Files: .inference_started, pip_freeze.txt, run.log, run_config.json, triageline_text_evaluation_report.json, triageline_text_pass_rate_report.json

## Live FDB-v3 run #2 — 2026-09-30 (all 100 examples turn-taken)

Second full live run after the live-audio robustness fixes (undashed spoken ids, NATO/dash
normalization, compound numbers, mid-id turn hold, dangling-verb commit block, endpointing
max-delay 1.2s, progress narration during slow injected tool calls). Run via
`resilient_fdb_run.sh` (self-healing passes; workers on Windows dev machine were being
killed by HP SystemOptimizer — passes recover missing examples automatically).

| metric | run #1 (63 turn-take) | **run #2 (100 turn-take)** |
|---|---|---|
| Turn-take success | 63/100 | **100/100** |
| Tool-selection acc (N=100) | 77.4% (N=63) | 75.1% |
| Argument accuracy (N=100) | 43.7% (N=63) | 33.2% |
| Strict pass (exact match, judge off) | 13/100 | 13/100 |
| Avg response latency | 5.77s | 5.34s (runner perceived: 3.92s) |

Takeaways: the silent-room problem is fully eliminated (every example produced agent speech).
The remaining strict-pass ceiling is argument extraction under real STT surface forms
(travel_identity argument accuracy 5.8% — spelled ids/dates remain the hard case); the
offline text replay (91/100) shows the tool logic itself is sound. Official scored numbers
come from the organisers' common re-run with their pinned gpt-4o judge.
Artifacts: `results/20260930_live_run/`.
