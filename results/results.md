# FDB-v3 results — TriageLine

Generated: 2026-09-30T18:24:25+00:00

Run directory: `results\20260930T182148Z`  
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

Files: .inference_started, agent_heartbeat.log, agent_tool_calls.log, pip_freeze.txt, run.log, run_config.json, triageline_text_evaluation_report.json, triageline_text_pass_rate_report.json


## Fast failure-replay harness + spelled-letter-id fix — 2026-10-01

`livekit_agent/replay_live_failures.py` replays a live run's OWN transcripts and fragment timing
through the same adapter offline, so a fix is measured in seconds instead of a 2-hour live run.
It resolves `$RESULT_n` references like the official evaluator and reports expected vs live-recorded
vs replayed calls.

Bug it found: a *spelled* letter-only id ("track order B O B", "Track order C A T") was never
joined (the joiner required a digit), so those tool calls silently never happened. The acceptance
is now gated on the joiner's UPPERCASING (ids uppercase, prose lowercase) — a broader
acceptance was tried and reverted because it cost the offline diagnostic 4 points (91 -> 87).

Measured on the same 29 live transcripts (diagnostic harness, not the official score):

| | before | after |
|---|---|---|
| pass | 12 (41%) | **20 (69%)** |
| missing_call | 7 | **1** |
| extra_call | 4 | **0** |
| wrong_args | 6 | 8 |

Offline text replay held at **91/100**; integrity audit PASS; 242 Python tests pass.
Residual work: the wrong_args bucket (chained `$RESULT` ids, plural/singular queries).
