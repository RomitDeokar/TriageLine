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


## FINAL LIVE RUN — pinned config, judge off (2026-10-01, run dir `results/live_20260930T200524Z/`)

100 examples streamed through LiveKit (Deepgram nova-3 STT / aura-2 TTS, official FDB-v3 runner and
evaluators, planner OFF, automatic dispatch), scored by the official `evaluate_tool_calls.py` and
`evaluate_pass_rate.py`. Coverage: **99/100 produced an agent response** (the single outlier,
`housing_14_61517db6`, joined its room but never spoke on any of 40 retry passes; its sibling
recording of the same scenario scores normally — reported as a miss, not hidden).

| metric | run #1 | run #2 | **final (fixed)** |
|---|---|---|---|
| strict pass (exact match, judge off) | 13/100 | 13/100 | **28/100** |
| tool-selection accuracy | 77.4% (N=63) | 75.1% | **80.6%** |
| argument accuracy | 43.7% (N=63) | 33.2% | **39.7%** |
| turn-take | 63/100 | 100/100 | **99/100** |
| avg response latency | 5.77 s | 5.34 s | 5.82 s |

Failure breakdown: wrong tools 43, wrong arguments 29. By domain: finance_billing 52.0%,
ecommerce_support 34.5%, housing_location (argument accuracy 26.3% remains the weakest), travel 0%.
The gains came from the live-audio fixes (dispatch/turn handling, spoken-id corrections incl.
letter-only ids, ordinal dates, decimal-safe numbers, continuation dedupe). The organisers re-run
with their pinned gpt-4o judge, which is more forgiving on wording than this exact-match diagnostic.


## Audio-path fix pass — 2026-10-01 (offline diagnostic; NOT a live run)

A root-cause pass over the STT / turn-commitment / latency configuration, validated offline against
the **official released data** (100 recordings + metadata, downloaded from the v3 README) using the
**official evaluators**. No API keys or LiveKit room were available here, so this is the shared
`audio → STT → adapter → ParticipantAgent → tools` path replayed with a local faster-whisper ASR. It
is a diagnostic proxy, not the scored Deepgram/live number.

### What was wrong (each reproduced before/after)

1. **STT biasing leaked the benchmark's own example answers.** `agent/nlu.py:tool_vocabulary` scraped
   the quoted examples from every tool/argument description (place names, currency codes, card tiers,
   and ids) and `speech_providers.whisper_prompt` hard-coded more (`QRT417`, `P52`, `UA318`). On the
   official audio this made the decoder hallucinate: whole utterances came back as a single filler
   word. Removing the vocabulary/example prompt (Whisper keeps only a generic spelling hint; Deepgram
   still gets sanitized domain `keyterm`s) fixed every degenerate transcript.
2. **`extract_id` could return the cue word itself** (`"The order ID is q r s"` → literal `"ID"`),
   because the undashed fallback matched the first uppercase token after the noun. Cue words are now
   blocked; dashed/spelled benchmark ids were never affected.
3. **An else-branch condition was evaluated against the wrong result** (`"if everything's over 50"` was
   tested against the running cart total from a later state change, so the branch fired wrongly).
   `condition_holds` now prefers the entity's own `price`/`cost`/`rate`/`amount`. `_COND` also
   recognises an else branch that opens with "but/or/otherwise".
4. **The commit gate could wait ~10 s** while text "looked unfinished" (`_settle_then_route` loop), the
   source of the 15-26 s reply-latency tail. It is now bounded by `max_settle_s`. Live defaults were
   retuned: `TRIAGELINE_SETTLE_S` 1.0 → 0.4, `MAX_SETTLE_S` 2.0 → 1.4, Silero
   `min_silence_duration` 0.55 → 0.4 / `min_speech_duration` 0.05 → 0.2, endpointing
   `min/max_delay` 0.35/1.2 → 0.25/0.9, and **Deepgram now requests `punctuate`/`smart_format`/`numerals`
   with `endpointing_ms=350`** (plugin defaults are `smart_format=False`, `numerals=False`,
   `endpointing_ms=25`, which fragments a request into many finals and returns spoken numbers as words).
   All kwargs verified present in the pinned `livekit-plugins-deepgram==1.8.3`.

### Measured (official data, official evaluators, judge off)

| metric | before | after |
|---|---|---|
| strict pass (local-ASR audio replay, diagnostic) | 54/100 | **65/100** |
| tool-selection accuracy | 76.0% | **93.3%** |
| argument accuracy | 60.7% | **72.8%** |
| degenerate (≤3-word) transcripts | 19/100 | **0/100** |

### Regression gates

- Offline **text** replay (official data + official evaluators, judge off): **92/100** (was 91;
  `ecommerce_20`'s spurious `track_order` is fixed). Tool-selection 98.8%, argument 93.7%.
- Tests: **243 passed** (was 236 — the previously-failing `test_spoken_ids_still_resolve` is fixed);
  the 8 remaining failures are environment-only (`livekit` not installed here, Starlette/httpx
  TestClient version). Six new regression tests in `tests/test_speech_and_settle.py` fail on the
  pre-fix tree and pass now.
- `scripts/integrity_audit.py --strict-comments` → PASS; held-out paraphrase set → 29/30.

**The live scored run was not re-run** (no LiveKit/speech keys in this environment). The reported
live number therefore remains the 28/100 exact-match run above; the organiser re-run with the pinned
gpt-4o judge is what counts, and the latency/turn/STT changes above are aimed squarely at its
failure buckets (fragmented finals → duplicate calls, missing chained calls, spoken ids/numbers).


## Parser fix pass — 2026-10-02 (official data + official evaluators)

The official live run's argument failures were mined for exact root causes (missing `max_price`,
`"the gym"`/`"five hundred Central Ave"`, `filter_name="Max Price Eighteen"`, swapped accounts/currency
amounts). Several were pure parser defects, fixed without touching benchmark answers:

| bug | before | after |
|---|---|---|
| thousands separator | `"1,500"` → 1 | → 1500 |
| k suffix | `"2k"` → 2 | → 2000 |
| decimal with separator | `"1,250.50"` → 1 | → 1250.5 |
| spoken street number | `"five hundred Central Ave"` kept as words | → `"500 Central Ave"` |
| filter compound value | `"eighteen hundred"` → 18 | → 1800 |
| filter key from a verb phrase | `filter_name="i_want"` | rejected |

`_compound_normalize` is now non-destructive (it no longer splits `"5th"` into `"5 th"` or a plain
count/date), and a purely numeric place (`"close to one"` → `"1"`) is rejected.

Measured: official-audio local-ASR replay **65 → 71/100** strict (tool-selection 93.3% → 94.2%,
argument 72.8% → 77.3%); offline text replay held at **92/100**; tests 243 → 249; integrity PASS;
held-out 29/30. Deliberately **not** changed (would be benchmark-specific overfitting): the remaining
exact-match misses are judge-forgivable wording/plurals (`mechanical keyboard(s)`) or unwinnable
(a city that is never spoken; a mis-formatted expected id).


## Live run — 2026-10-02 (current code, judge off)

Full official 100-recording run on a real LiveKit + Deepgram + GPU box (local recovery runner with a
worker watchdog; the dev machine's external process kills the worker, so 85/100 rooms answered and 15
were lost to worker deaths — those count as failures here, not omitted).

| metric | committed 2026-09-30 | this run (2026-10-02) |
|---|---|---|
| strict pass (all 100) | 28/100 | **39/100** |
| tool-selection (turn-taken) | 80.6% | **84.9%** |
| argument accuracy (turn-taken) | 39.7% | **56.9%** |
| tool-selection / argument (all 100) | — | 72.2% / 48.3% |
| turn-taken | 99/100 | 85/100 (worker deaths) |

Per-responding-sample strict pass rose from 28.3% to **45.9%** (39/85). By domain (turn-taken):
finance 90.4% tool / 76.7% arg, travel 82.1% / 36.7%, housing 66.5% / 37.2%, ecommerce 54.7% / 42.0%.
Failure breakdown: 43 wrong-tools, 18 wrong-arguments (argument failures nearly halved).

This is exact-match (judge off); the organisers' pinned gpt-4o judge is expected to be at or above it.
Remaining losses are dominated by (a) the 15 worker-death rooms and (b) long multi-pause utterances
where VAD genuinely ends mid-turn and the duplicate call is already logged — fixing that needs the
semantic turn detector, which requires an inference executor this worker does not have.

Run artifacts: `results/live_20261002T143553Z/` (run.log, worker logs, evaluation_report.json,
pass_rate_report.json).
