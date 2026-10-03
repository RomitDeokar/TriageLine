# 03 — SCORE MAXIMIZATION (beyond bug-fixing)

This is the "what else wins points" file: how the official score is *actually* produced, what the
judge rewards, how to protect the 60% reproducibility chunk, and a runbook to apply everything in
this package safely. Read after `01` and `02`.

---

## 1. Where the 100 points actually come from (Round 1)

```
Round 1 = 0.60 × normalized benchmark score   (= your FDB-v3 strict pass / metrics, organisers re-run)
        + 0.20 × extension use case          (Triage Line: does it run end-to-end in the video?)
        + 0.20 × documentation/architecture/video
```
Round 2 (shortlisted) is a live jury demo: your agent interrupted live + design questions.

**Two implications most teams miss:**
- **The organisers re-run FDB-v3 with their own pinned gpt-4o judge.** Semantic argument matching is
  *forgiving* of wording; it is **not** forgiving of a *wrong tool*, a *missing call*, an *extra
  call*, or **no response**. So prioritise: (1) answer, (2) right tool, (3) right call count,
  (4) arguments, (5) latency.
- **20% is documentation/extension/video.** A README that a reviewer can follow for 60 seconds is
  worth real points and costs nothing.

---

## 2. The scoring-funnel tactic (answer the "what do we optimise?" question)

Order every improvement by the funnel:

| Priority | Property | Why it dominates | Cards |
|---|---|---|---|
| 1 | **Turn-take** (agent answers at all) | no-response zeroes the scenario *and* leaves the headline metric | FC-01, FC-02 |
| 2 | **Correct tool set** (no missing) | a missing call fails the scenario | FC-04, FC-15 |
| 3 | **No extra calls** (precision) | one spurious call fails the scenario | FC-04, FC-05 |
| 4 | **Argument accuracy** | the largest bucket, but the judge forgives wording | FC-03, FC-06…FC-10 |
| 5 | **Latency** | 15% internal, tie-breaker, and a reported FDB metric | FC-11, FC-12 |
| 6 | **Response quality** | ×0.90–×1.10 multiplier | FC-13, FC-17 |

**Rule of thumb:** a change that improves tool-selection or turn-take beats a change that only
improves arguments. Never trade a tool-selection gain for an argument gain.

---

## 3. Response-quality multiplier (×0.90–×1.10) — cheap points

The judge scores **relevance, truthfulness, naturalness, non-redundancy** on the transcript. The
current fillers/acks are correct but templated. Concrete, low-risk improvements:

1. **Content-aware acknowledgements.** `agent.py ack()` already repeats the key argument
   ("Checking flights to Boston."). Extend that pattern to every tool the manifest exposes, using the
   tool's own description words (schema-driven, not hardcoded).
2. **No repeated strings.** FC-13 rotates backchannel/narrator lines.
3. **Truthfulness.** Never say "booked"/"done" before the tool completes — the code obeys this
   (`FUTURE_GUARDS`), but audit any new line you add: it must be a **promise** ("I'll get that
   booked now") or a **result** ("Done — you're booked"), never a premature claim.
4. **Non-redundancy.** `say()` already dedups fillers; ensure the *final_response* is not a paraphrase
   of the filler just spoken.
5. **Brevity.** The guide penalises keyword-stuffing and spam; keep every line ≤ ~20 words.

Self-test (the repo's own instruction): dump transcripts with `run_local.py --json` and grade them
with any LLM using the prompt in `docs/SCORING.md`. **Do not** put grader-directed text in outputs.

---

## 4. Protecting the 60%: reproducibility is binary

The guide: *"If your script does not reproduce, we contact you once; if it still does not run, this
portion scores zero."* This is the single biggest risk. Do all of the following:

1. **Test on a clean machine/container** that is not yours. Use the repo's `Dockerfile` /
   `docker-compose.yml` to simulate the organisers' box.
2. **Run the full path once end-to-end** before submitting: `PYTHON=python3.12 ./run_fdb_v3.sh --require-judge`
   on a fresh clone, with only `livekit_agent/.env.local` filled in.
3. **Make the failure modes survivable** (FC-20): NeMo optional, data-download fallback, provider
   chain verified without a GPU, pinned model names.
4. **Declare the provider/model** exactly (the guide asks for it). The start-up banner already does;
   put the same declaration in the README and in `submission.yaml`.
5. **Pin the FDB-v3 commit** (already `3e799c45…`) and record it in `run_config.json`.
6. **Never call your own servers at eval time.** Already true; keep it.
7. **Never cache across scenarios.** `entrypoint()` resets provider state per room; keep the
   per-room `MockAPIRegistry` construction.

---

## 5. Latency: the honest levers

- **FC-11**: shorter commit gate (0.7 s / 1.4 s).
- **FC-12**: Deepgram formatting + endpointing → fewer fragments → fewer settle waits.
- **Speak a substantive ack immediately** on a recognised tool (the fast path already does
  `say("filler_speech", ack)` before the call). Under the official audio→audio clock this starts the
  timer early and legitimately.
- **Do NOT** add filler audio just to trip the clock — gibberish/spam is explicitly penalised and
  "gibberish does not stop the latency clock".

---

## 6. Safety (10% internal, tie-breaker in the official rules)

Audit these before submitting:

| Rule | Current status | Action |
|---|---|---|
| No duplicate state-modifying completion with identical args | protected by op-ledger; `add_to_cart` quantity path at risk | FC-19 |
| Filler budget (default 4) | `MAX_FILLERS=3` in agent, live path uses per-turn budget | verify live path counts fillers |
| No `final_response` without `state_snapshot` | `say()` always attaches one | keep |
| No premature completion claim | guarded by `FUTURE_GUARDS` | audit new lines |
| No verbatim-repeated filler | dedup in `say()`; FC-13 for narrator | FC-13 |
| Protocol errors | none found | keep `validate_action` clean |

**Ties break on the safety subscore, then mean latency fraction.** A clean safety record with
average latency beats a slightly better pass rate with deductions.

---

## 7. Extension use case (20%) — make it undeniable

The guide scores *relevance, end-to-end execution, and whether the video shows it working*.

- **One use case done well beats three half-built.** Triage Line is the right scope.
- **Show it running end to end in the video** — a slide is explicitly worth nothing.
- The demo video must contain: (a) a real interruption handled **on the benchmark**, then (b) the
  extension in action. 3–5 min, single takes preferred.
- Rehearse the exact interruptions you will show: mid-utterance destination change, correction after
  a tool started, and a re-ask. These are the benchmark's own disfluency types — the agent already
  handles them (see `docs/ARCHITECTURE.md`).
- Keep the extension's honesty ("all actions are simulated") — it is a strength, not a weakness.

---

## 8. Documentation (part of the 20%) — the 60-second test

A reviewer should be able to answer these from the first screen (FC-22):

1. What is it? → one-line pitch.
2. How do I run it? → the one-command reproduction.
3. What is the extension, and does it work? → command + video timestamp.
4. What score did it get, and how was it produced? → current number **with its mode**.
5. What's the architecture? → one diagram.

Then, below the fold: diagnostics, known simplifications, AI disclosure, integrity audit.

---

## 9. Integrity (disqualification risk)

The guide: *"Don't hardcode, memorize, or fine-tune on benchmark test items."* FDB-v3 is public, so
its answers are visible; any submission that pattern-matches test items is **disqualified**.

Keep running:
```bash
.venv/bin/python scripts/integrity_audit.py --strict-comments   # must be PASS
.venv/bin/python scripts/heldout_eval.py                        # must be 29/30
```
Every fix card in this package is written to be **schema-driven** (argument names/types/enums,
generic English lexicons). If any AI proposes an `if city == "..."` branch, **reject it**.

---

## 10. Recommended application order + runbook

Apply cards in this order, committing after each, gating on the offline replay.

| Step | Cards | Why now |
|---|---|---|
| 1 | FC-21, FC-22 | free points, zero risk, changes no logic |
| 2 | FC-01, FC-02 | protect turn-take (biggest lever) |
| 3 | FC-04 | remove spurious calls (precision) |
| 4 | FC-03 | id extraction (clear, reproduced wins) |
| 5 | FC-06, FC-07, FC-08, FC-09 | argument accuracy |
| 6 | FC-05, FC-15, FC-10 | duplicates / chained steps |
| 7 | FC-12, FC-11 | STT + latency |
| 8 | FC-13, FC-17, FC-19 | quality / safety |
| 9 | FC-16, FC-14, FC-20 | generalization + reproducibility hardening |
| 10 | FC-18 | (no-op reminder) |

**Gate after every step:**
```bash
.venv/bin/python -m pytest tests livekit_agent/adapter_tests -q
python3 livekit_agent/fdb_v3_offline_replay.py --data <dir> --text
.venv/bin/python scripts/heldout_eval.py
.venv/bin/python scripts/integrity_audit.py --strict-comments
python livekit_agent/replay_live_failures.py --json /tmp/replay.json
```
If `strict pass` (offline) rises and the held-out set stays 29/30 and integrity is PASS, keep it.
Otherwise revert.

**Only run live once**, at the end:
```bash
PYTHON=python3.12 ./run_fdb_v3.sh --limit 5 --require-judge   # smoke
PYTHON=python3.12 ./run_fdb_v3.sh --require-judge             # full
```
Then regenerate the evidence (FC-21) with the new number.

---

## 11. Expected impact (honest estimate, judge-off exact match)

| Change set | Expected strict-pass gain | Confidence |
|---|---|---|
| FC-01/FC-02 (turn-take) | recovers the ~15 lost rooms → +8 to +15 | high (mechanical) |
| FC-04 (spurious calls) | +5 to +12 | high (reproduced) |
| FC-03 (ids) | +3 to +6 | high (reproduced) |
| FC-06/07/08/09 (args) | +5 to +10 | medium |
| FC-05/10/15 (duplicates/chains) | +3 to +8 | medium |
| FC-11/12 (latency/STT) | +2 to +6 (also fewer fragments → fewer dupes) | medium |
| **Total** | **≈ +25 to +55 strict points** from the 39–42 baseline | — |

Under the organisers' **semantic judge** the argument gains are larger in practice (wording is
forgiven), so the delivered Round-1 benchmark score should rise by more than the exact-match delta.

---

## 12. What NOT to do (the disqualification / regression list)

1. **Never** hardcode a scenario id, city, expected string, or benchmark wording.
2. **Never** call an external server at eval time.
3. **Never** cache across scenarios.
4. **Never** go silent to avoid a wrong call — a silent scenario is a guaranteed 0.
5. **Never** batch many fix cards in one commit — you will not know which broke the gate.
6. **Never** trust a live run to validate a fix (2 h, and worker deaths muddy the signal).
7. **Never** claim success before a tool completes.
8. **Never** let a weak AI "clean up" files it was not asked to touch.
