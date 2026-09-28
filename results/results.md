# FDB-v3 results — TriageLine

Generated: 2026-09-28T18:55:18+00:00

Run directory: `results/20260926T031545Z`  
Mode: **offline_text_replay**  (official data + official evaluators; no LiveKit transport, no audio latency)
Provider name: `triageline_text` · LLM judge: **off (exact match = lower bound)** · examples: 100  
FDB-v3 commit: `3e799c45a045256f47d5f1c9cda90157e2d2ec9e` · TriageLine commit: `e019ea97b94c17d9be320bf6f46a86635b8b6044` · Python 3.13.14  
Agent: custom LiveKit agent (Silero VAD → STT → ParticipantAgent: rules only (LLM planner off) → TTS) · STT=openai TTS=openai

> **Stale evidence:** this run was produced at `e019ea97b9`, not the current checkout `b2f0821162`. Re-run `./run_fdb_v3.sh` on the submission commit.

> **Diagnostic only.** Offline text replay feeds the official transcripts to the agent without LiveKit, STT or TTS. It is **not** the scored live FDB-v3 run and must not be reported as one.

## Headline (official evaluators)

| strict pass rate | tool-selection acc | argument acc | response quality | turn-take rate |
|---|---|---|---|---|
| **85.0%** (85/100) | 95.5% | 89.5% | not produced | 100.0% |

### Dev vs hash split of the public set (`livekit_agent/fdb_split.py`)

Both halves come from the same public benchmark that rules were developed against, so this is **not** an independent held-out set. See `scenarios_heldout/` for the independent paraphrase set.

| split | passed | rate |
|---|---|---|
| dev | 44/53 | 83.0% |
| heldout | 41/47 | 87.2% |

**By domain:** ecommerce_support 79.3% · housing_location 69.2% · finance_billing 100.0% · travel_identity 95.0%

**By disfluency:** SELF_CORRECTION 82.4% · FILLER 82.8% · PAUSE 72.2% · HESITATION 90.0% · FALSE_START 100.0%

**By difficulty:** medium 88.2% · hard 73.3% · easy 91.7%

**By number of tools:** 1 89.4% · 2 83.3% · 3 68.8%

**Failures:** wrong_tools=9, wrong_arguments=6

## Latency

Not measured in offline mode (no audio). Run the full `./run_fdb_v3.sh` for latency.

Files: pip_freeze.txt, run_config.json, triageline_text_evaluation_report.json, triageline_text_pass_rate_report.json
