# FDB-v3 results — TriageLine

Generated: 2026-09-29T08:45:23+00:00

Run directory: `results/20260929T084428Z`  
Mode: **offline_text_replay**  (official data + official evaluators; no LiveKit transport, no audio latency)
Provider name: `triageline_text` · LLM judge: **off (exact match = lower bound)** · examples: 100  
FDB-v3 commit: `3e799c45a045256f47d5f1c9cda90157e2d2ec9e` · TriageLine commit: `b30ac4dc90f2b0da484fe589be70076af380a66c` · 3.13.14  
Agent: custom LiveKit agent (Silero VAD → STT → ParticipantAgent: rules only (LLM planner off) → TTS) · STT=openai:whisper-1 TTS=openai:tts-1

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

**By domain:** ecommerce_support 86.2% · housing_location 84.6% · finance_billing 100.0% · travel_identity 95.0%

**By disfluency:** SELF_CORRECTION 82.4% · FILLER 100.0% · PAUSE 83.3% · HESITATION 90.0% · FALSE_START 100.0%

**By difficulty:** medium 94.1% · hard 86.7% · easy 91.7%

**By number of tools:** 1 92.4% · 2 83.3% · 3 93.8%

**Failures:** wrong_tools=3, wrong_arguments=6

## Latency

Not measured in offline mode (no audio). Run the full `./run_fdb_v3.sh` for latency.

Files: .inference_started, pip_freeze.txt, run.log, run_config.json, triageline_text_evaluation_report.json, triageline_text_pass_rate_report.json
