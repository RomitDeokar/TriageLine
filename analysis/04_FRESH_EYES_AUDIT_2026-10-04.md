# 04 — FRESH-EYES AUDIT 2026-10-04 (independent re-review of the whole submission)

> **What this file is.** A brand-new audit of the repo *as it stands at HEAD `8855334`* against the
> two official guide documents (`Theme05_Participant_Guide_UPDATED_FBD.docx` — the current FBD
> participant guide — and `Theme 5_Guide.pdf` — the older protocol/queue-based guide). It is
> written so that a weak AI can apply each fix without guessing: every finding has the exact file,
> the exact line region, the root cause, and drop-in code.
>
> **How it relates to `analysis/01–03`.** Those files (committed 2026-10-03) are still valid and
> their fix cards (FC-01…FC-22) are still the right *content* fixes. This audit found they missed a
> **class of problems that decide the score before a single scenario runs**: the tuned configuration
> your docs claim is **not actually wired into the reproduction script or the code defaults**, so
> the organisers' clean re-run will execute a *different, slower* agent than your best recorded run.
> Start here, then do the FC cards.
>
> **Rule for every change:** no scenario ids, no benchmark strings, keep the four gates green
> (pytest, offline text replay ≥ 92, heldout 29/30, integrity_audit PASS). Nothing in this file has
> been applied — these are instructions for you/your AI.

---

## 0. Executive summary — why the live score is low, in one paragraph

Your agent logic is genuinely good (offline text replay: **92/100** strict; tool-selection
**98.8%**). The low live score (**41–42/100** exact-match, latency **5.8 s**) is caused by a small
number of *mechanical* defects, not intelligence:

1. **The re-run will not use your tuned config.** The docs/README/submission.yaml all claim
   `settle 0.4/1.4 s`, `VAD 0.4/0.2`, `endpointing 0.25/0.9`, and Deepgram
   `punctuate/smart_format/numerals/endpointing_ms=350`. **None of that is true at HEAD.** The code
   defaults are `settle 1.0/2.0`, `VAD 0.05/0.55`, `endpointing 0.35/1.2`, and Deepgram runs with
   `smart_format`/`numerals` **off** and `endpointing_ms=25`. `run_fdb_v3.sh` exports **none** of
   the `TRIAGELINE_*` overrides. So the organisers' re-run gets the *old* behaviour — fragmented
   finals → duplicate/missing calls, spoken numbers returned as words, and ~1.5–4 s of dead air per
   turn. **This single finding is worth more than every parser fix combined.**
2. **The semantic turn detector is silently optional.** If `download-files` fails on the clean
   machine (or the plugin errors), the code *logs a line and continues* with VAD-only endpointing —
   the exact condition your own results identify as the source of ~14 duplicate-call failures. The
   scored run can degrade without failing.
3. **Your own failure data tells you the fix order.** Live run failure split: **~15 rooms with no
   agent response** (worker deaths / lost VAD-end), **~14 duplicate calls from VAD mid-turn
   splits**, then **39 wrong-tools / 20 wrong-arguments**. Fix turn-take first, duplicates second,
   arguments third. (FC-01/02/05 + this file's F-A…F-D cover exactly that.)
4. **Response latency is the tie-breaker and a scored metric, and it is dominated by the commit
   gate and the STT endpointing**, both of which are mis-configured at HEAD (see #1).

A realistic path: wire the tuned config (F-A), hard-fail on the turn detector (F-B), apply
FC-01/FC-02/FC-05 (turn-take + duplicates), and the exact-match live pass should move from ~41 to
the mid-50s–60s; under the organisers' gpt-4o semantic judge the *reported* pass will be higher.

---

## 1. What the score actually rewards (recap, guide-aligned)

| Source | Weight | What it is measured from |
|---|---|---|
| FDB-v3 re-run by organisers | **60%** | strict pass rate (tool selection F1 + argument accuracy + no missing/extra calls) + latency, judged by their pinned gpt-4o. **Only their re-run counts.** |
| Extension use case | **20%** | Triage Line runs end-to-end and is shown working in the video |
| Docs / architecture / video | **20%** | README, honest architecture, real behaviour on camera |

The judge is forgiving of *wording* but never of: a **missing call**, an **extra call**, a **wrong
tool**, or **no response**. So the priority order is: **turn-take → correct tool set → call count →
arguments → latency → phrasing.** Ties break on strict pass-rate.

---

## 2. NEW findings (not in 01–03) — ranked by points

Severity: **S0 = decides the score before scenarios run · S1 = caps the whole score ·
S2 = per-scenario content loss · S3 = latency/safety/polish.**

---

### F-A (S0) — The tuned configuration is documented but NOT wired into the code or the script

**Evidence (HEAD `8855334`).**

| Claimed in `submission.yaml` / `results.md` | Actual code default | Where |
|---|---|---|
| `TRIAGELINE_SETTLE_S=0.4` | `"1.0"` | `livekit_agent/cascaded_agent.py:98-99` |
| `TRIAGELINE_MAX_SETTLE_S=1.4` | `"2.0"` | `livekit_agent/cascaded_agent.py:100` |
| `VAD min_silence 0.4 / min_speech 0.2` | `0.55 / 0.05` | `livekit_agent/speech_providers.py:125-126` |
| endpointing `0.25/0.9` | `0.35 / 1.2` | `livekit_agent/cascaded_agent.py:226-227` |
| Deepgram `punctuate, smart_format, numerals, endpointing_ms=350` | **none passed** (plugin defaults: `smart_format=False, numerals=False, endpointing_ms=25`) | `livekit_agent/speech_providers.py:159-166` |

`run_fdb_v3.sh` (stage 2, lines 138-141) pins `TRIAGELINE_MODE/BENCHMARK_POLICY/LLM_*` only — it
never exports `TRIAGELINE_SETTLE_S`, `TRIAGELINE_VAD_*`, `TRIAGELINE_EOT_MAX_S`, or any
`TRIAGELINE_DEEPGRAM_*`. There is **no `TRIAGELINE_DEEPGRAM_*` knob anywhere in the codebase** (the
comment in `speech_providers.py:162-165` says to add one — it was never added).

**Why this is the #1 bug.** The comment at `speech_providers.py:162-164` even admits the recorded
live baseline "used the defaults, so they are left here" — meaning your best 41/100 was achieved
*with the slow, fragmenting config*. The organisers re-run from `run_fdb_v3.sh` on a clean machine
will therefore reproduce the *slow* agent: `endpointing_ms=25` splits every hesitation into a final
(a prime cause of the ~14 duplicate calls), `smart_format/numerals` off returns "one thousand five
hundred" as words (the exact failures fixed in your parser pass), and the 1.0 s settle gate +
0.35/1.2 endpointing stacks onto the 5.8 s latency.

**Fix (two parts — do both).**

*Part 1 — code defaults.* Make the tuned values the defaults so *any* runner (theirs included)
gets them without env vars:

```python
# livekit_agent/cascaded_agent.py  (~line 98)
_default_settle = "0.4" if MODE == "benchmark" else "0.9"          # was "1.0"
SETTLE_S = float(os.environ.get("TRIAGELINE_SETTLE_S", _default_settle))
MAX_SETTLE_S = float(os.environ.get("TRIAGELINE_MAX_SETTLE_S", "1.4"))  # was "2.0"
```

```python
# livekit_agent/cascaded_agent.py  build_turn_handling() (~line 226)
th: dict = {"endpointing": {"min_delay": 0.25,                                  # was 0.35
                            "max_delay": float(os.environ.get("TRIAGELINE_EOT_MAX_S", "0.9"))}}  # was 1.2
```

```python
# livekit_agent/speech_providers.py  load_vad() (~line 124)
return silero.VAD.load(
    min_speech_duration=float(os.environ.get("TRIAGELINE_VAD_MIN_SPEECH_S", "0.2")),   # was 0.05
    min_silence_duration=float(os.environ.get("TRIAGELINE_VAD_MIN_SILENCE_S", "0.4")), # was 0.55
)
```

*Part 2 — Deepgram kwargs.* Add the knobs the comment promises and turn them on by default for the
benchmark worker:

```python
# livekit_agent/speech_providers.py  build_stt(), deepgram branch (~line 159)
if p == "deepgram":
    from livekit.plugins import deepgram
    kw = {"keyterm": terms[:50]} if terms and model.startswith("nova-3") else {}
    # Spoken numbers/ids must arrive as digits, and finals must not fragment on every 25 ms pause.
    # All four kwargs exist in livekit-plugins-deepgram==1.8.3 (verified in requirements-fdb.txt pin).
    kw.setdefault("punctuate",        os.environ.get("TRIAGELINE_DG_PUNCTUATE", "1") == "1")
    kw.setdefault("smart_format",     os.environ.get("TRIAGELINE_DG_SMART_FORMAT", "1") == "1")
    kw.setdefault("numerals",         os.environ.get("TRIAGELINE_DG_NUMERALS", "1") == "1")
    kw.setdefault("endpointing_ms",   int(os.environ.get("TRIAGELINE_DG_ENDPOINTING_MS", "350")))
    return deepgram.STT(model=model, language="en-US", interim_results=True, filler_words=True, **kw)
```

*Part 3 — pin in the script* so the scored run is explicit and shows up in `run_config.json`:

```bash
# run_fdb_v3.sh, stage_2_configure, right after the existing `export TRIAGELINE_MODE=...` block
export TRIAGELINE_SETTLE_S=0.4 TRIAGELINE_MAX_SETTLE_S=1.4
export TRIAGELINE_VAD_MIN_SPEECH_S=0.2 TRIAGELINE_VAD_MIN_SILENCE_S=0.4
export TRIAGELINE_EOT_MAX_S=0.9
export TRIAGELINE_DG_PUNCTUATE=1 TRIAGELINE_DG_SMART_FORMAT=1 TRIAGELINE_DG_NUMERALS=1 TRIAGELINE_DG_ENDPOINTING_MS=350
```

**Verify.** `.venv/bin/python -m pytest tests/test_speech_and_settle.py -q` (update the expected
defaults in the test), then `./run_fdb_v3.sh --offline-text` must stay ≥ 92, and `resilient_fdb_run.sh`
must export the same block (it currently pins only MODE/LLM/STT/TTS — add the same 3 lines there too,
or better: source one shared `scripts/scored_env.sh`).

---

### F-B (S1) — Semantic turn detector is "best effort"; a clean machine can silently degrade to VAD-only

**Evidence.** `run_fdb_v3.sh:120-125`: if `download-files` fails, it only logs a WARNING and exports
`TRIAGELINE_TURN_DETECTOR=0`. `cascaded_agent.py:228-236`: if importing `MultilingualModel` throws, it
*logs and continues* with VAD endpointing. Your own `results.md` (live run 2026-10-02) states the
~14 duplicate calls "need the semantic turn detector, which requires an inference executor this
worker does not have."

**Why it matters.** VAD-only endpointing on `min_silence 0.55 s` cuts a turn at every mid-sentence
hesitation — the benchmark's 5 disfluency classes are *designed* to trigger exactly that. With the
detector off, the commit gate (`_settle_then_route`) is your only defence, and it is precisely the
path that produced the duplicate `search_flights`/`search_products`/`update_search_filter` calls.

**Fix.** For the *scored* run, treat the detector as required, not optional:

```python
# livekit_agent/cascaded_agent.py  build_turn_handling()
def build_turn_handling() -> dict:
    th: dict = {"endpointing": {"min_delay": 0.25,
                                "max_delay": float(os.environ.get("TRIAGELINE_EOT_MAX_S", "0.9"))}}
    if os.environ.get("TRIAGELINE_TURN_DETECTOR", "1") == "1":
        try:
            from livekit_agent.speech_providers import _allow_plugin_registration_in_subprocess
            _allow_plugin_registration_in_subprocess()
            from livekit.plugins.turn_detector.multilingual import MultilingualModel
            th["turn_detection"] = MultilingualModel()
        except Exception as e:
            # Under the SCORED benchmark worker a missing turn detector is fatal, not a warning:
            # VAD-only endpointing is the known cause of duplicate tool calls on disfluent audio.
            if os.environ.get("TRIAGELINE_MODE", "benchmark") == "benchmark":
                raise RuntimeError(
                    "semantic turn detector unavailable; refusing to start the benchmark worker "
                    f"with VAD-only endpointing ({e}). Fix `python -m livekit.agents download-files` "
                    "or explicitly export TRIAGELINE_TURN_DETECTOR=0."
                ) from e
            log.info("turn detector unavailable (%s); assistant mode falls back to VAD endpointing", e)
    return th
```

And in `run_fdb_v3.sh`, change the WARNING to a hard failure:

```bash
( cd "$LK_DIR" && "$PY" -m livekit.agents download-files ) >>"$LOG" 2>&1 \
    || fail "turn-detector/silero weights download failed; the scored run must not run VAD-only"
```

(Keep `TRIAGELINE_TURN_DETECTOR=0` as an explicit escape hatch for local debugging only.)

---

### F-C (S1) — The 15 no-response rooms are the single largest score sink; make worker death survivable

**Evidence.** Live runs 2026-10-02/03: 15–16 rooms produced no agent response ("the dev machine's
process-killer had all workers down; restart takes ~15 s"). `run_fdb_v3.sh` starts exactly **one**
worker (`stage_4_agent`, line 224) and the whole run dies with it (`stage_5`: `kill -0 $AGENT_PID ||
fail "the TriageLine agent died"`). `resilient_fdb_run.sh` exists but is a local recovery script, not
the submission path.

**Why it matters more than arguments.** A no-response room scores 0 on *every* metric and, per your
own note in 00_START_HERE, costs ~3× a wrong argument. 15 rooms = up to 15 strict-pass points lost
before content is even graded.

**Fixes (all cheap, independent).**

1. **Run 2 workers in the scored script** (LiveKit automatic dispatch load-balances rooms across
   registered workers; you already validated 2 workers on 2026-10-03):
```bash
# stage_4_agent: start a second worker on the same box; harmless if the first never dies
( cd "$LK_DIR" && exec "$PY" cascaded_agent.py start --latency "$LATENCY" ) >"$AGENT_LOG.2" 2>&1 &
AGENT_PID2=$!
# cleanup(): kill both. stage_5 health check: fail only if BOTH are dead.
```
2. **Bounded teardown is already there** (`adapter.stop()` 5 s caps) — keep it; it is why "job did
   not ack shutdown" stopped happening. Do not remove it while touching anything else.
3. **Apply FC-01 and FC-02** (turn-commit watchdog + "must answer something" backstop). These convert
   a lost VAD-end from a silent 0 into at least a taken turn. They are in `analysis/02_FIX_SPECS.md`
   and are the two highest-value cards there.

---

### F-D (S2) — `_route_final`'s 8-second "arg continuation" heuristic can misclassify a real new request

**Evidence.** `livekit_agent/adapter.py:355-359`: within 8 s of the last routed turn, *any* final
matching `_ARG_CONTINUATION` ("on May 12", "under 200", "make it …") is force-fed to `on_barge_in`
(i.e. treated as an interruption/amendment of the previous task), even when the previous task has
**already finished** (`busy()` is False) and the user has moved on.

**Why it matters.** After a completed search, a follow-up like "under 200" or "on May 12" as a *new*
search refinement is correct; but the same regex also matches the start of a genuinely new request
("make it two tickets to…"), and routing that through the interruption path bumps the epoch and
cancels nothing relevant, producing stale-slot reuse — a source of the wrong-args bucket
(`finance_billing` swapped amounts, wrong city after correction).

**Fix.** Gate the heuristic on the previous task being incomplete *or* the new fragment referencing
the same tool family:

```python
# adapter.py _route_final, replace the arg-continuation branch:
recent = self._last_route_at and (time.time() - self._last_route_at) < 8.0
if recent and _is_arg_continuation(text):
    # Only amend when there is a task whose slots this fragment can plausibly refine:
    # something in flight, an open clarification, or a just-completed read whose slots
    # are still the live intent. Otherwise treat it as a fresh turn.
    agent = self.agent
    refinable = (agent.inflight or agent.pending_clarify is not None
                 or (agent.last_done is not None and not agent.answered))
    if refinable:
        log.info("late arg continuation, amending instead of re-issuing: %r", text)
        await self.on_barge_in(text)
        return
```

(The `_is_arg_continuation` regex itself is fine; the bug is applying it unconditionally.)

---

### F-E (S2) — `speech_started` handler is dead code in the live path; the fast barge-in you designed never fires

**Evidence.** `agent/agent.py:266-271` implements the ideal "stop obsolete work at voice onset,
before STT returns text" handler for `speech_started`. But the LiveKit adapter **never emits it**:
`grep` shows `speech_started` is produced nowhere in `livekit_agent/` — `on_user_speech_start()`
(adapter.py:184) only stops TTS and cancels the settle timer; it never tells the agent to cancel
in-flight tool calls. The only producer is `user_audio_chunk`→`interrupted` (agent.py:272-274),
which the LiveKit path never sends either.

**Why it matters.** The guide's core promise is "cancel superseded in-flight calls within a few ms
grace period." Today the cancel happens only when the *final transcript* arrives (seconds later),
not at speech onset. In the mock harness the call is usually already done so it is harmless, but in
principle the epoch/invalidation is delayed by the full STT latency — exactly what the interruption
metric measures.

**Fix.** Emit the event at VAD onset:

```python
# livekit_agent/adapter.py  on_user_speech_start()  (after `self._cancel_settle()`)
await self.in_q.put({"event_type": "speech_started", "payload": {}})
```

Caveat to handle in the same card: `ParticipantAgent.dispatch("speech_started")` calls
`cancel_where(lambda c: True)` + `invalidate()`, i.e. it cancels *all* in-flight work at mere voice
onset. That is the right semantics for barge-in, but a **false VAD onset** (a breath, a click) would
then kill a legitimate in-flight call. Guard it: only forward when speech lasts beyond a minimum
(e.g. forward on the first *partial transcript* or after ~150 ms of sustained VAD), or make
`speech_started` cancel only calls whose deps are likely to change. The minimal safe version is to
forward it and accept that a cancelled read-only call is re-issued (they are idempotent in your
design); state-modifying calls are protected already by the not-dispatched rule in
`_pump_outputs` (adapter.py:419-436).

---

### F-F (S2) — `results.md` is append-only and self-contradictory; the first number a reviewer sees is the worst

**Evidence.** `results/results.md` still opens with "91.0% offline text replay" from 2026-09-30,
then narrates 28/100, 39/100, 42/100, 41/100 across five sections. `README.md`'s table shows
**28/100** as "final" while the git log and `submission.yaml` say 41–42/100.

**Why it matters.** Documentation is 20% of the score and the benchmark section is 60%; a reviewer
who reads the top of `results.md` or the README table reads the *worst* number first.

**Fix (FC-21, restated concretely).** Restructure `results/results.md` so the **first table** is the
current best live run, clearly labelled, with the older runs collapsed under "History":

```markdown
## Current best — live FDB-v3, exact match (HEAD 8855334, run dir results/live_20261003T112137Z/)
| strict pass | tool-selection | argument acc | turn-taken | avg latency |
|---|---|---|---|---|
| **41/100** (49% per responding sample) | **88.5%** | **60.1%** | 84/100 (16 lost to a dev-machine process-killer, not a code fault) | ~5.8 s |

> Exact match with the judge OFF is a harsh lower bound; the organisers' pinned gpt-4o judge scores
> semantic equivalence and is expected to be at or above this. Offline text replay (upper bound): 92/100.

<details><summary>Run history (oldest first)</summary> …existing sections… </details>
```

Do the same one-line correction in `README.md`'s numbers table. This is free points.

---

### F-G (S3) — Backchannel "Mm-hm." is sent per-turn but not rate-limited across interruptions; can read as filler spam

**Evidence.** `cascaded_agent.py:339-353` sends `session.say("Mm-hm.")` on every substantive final
where `should_backchannel` is true; `turn_state["acknowledged"]` is reset on every VAD onset
(`on_user_state`). On a disfluent recording with many VAD onsets (FILLER/HESITATION classes), the
same turn can produce multiple "Mm-hm."s. The guide's quality multiplier penalises "excessive
fillers" and the old-kit scorer had a verbatim-repeat deduction.

**Fix.** Rate-limit by wall clock instead of per-onset:

```python
# cascaded_agent.py
turn_state = {"acknowledged": False, "backchannel_pending": False, "last_bc_at": 0.0}
# in on_transcript's should_backchannel branch, also require:
#     time.time() - turn_state["last_bc_at"] > 4.0
# and set turn_state["last_bc_at"] = time.time() when sending.
```

---

### F-H (S3) — Progress narrator fixed lines can be the only speech for 10 s+ on slow injected latency

**Evidence.** `cascaded_agent.py:285-291`: two fixed lines at 3.5 s and 7.5 s. Under
`--latency slow`/`degraded` (the organisers could run any profile), a chain of 3 calls at 3–8 s each
means the user hears the same two sentences repeatedly — the "no false done claims" rule is
respected, but naturalness (quality multiplier) suffers and the second line can fire after the tool
already returned if the result lands between scheduling and the `say`.

**Fix.** (a) Rotate ≥ 3 content-free lines and (b) narrate the *tool*, schema-driven, not a generic
line:

```python
async def _progress_narrator() -> None:
    what = api_name.replace("_", " ")
    lines = (f"Still working on the {what}.", "One moment, this is taking a second.",
             f"The {what} is being a little slow — almost there.")
    for i, line in enumerate(lines):
        await asyncio.sleep(3.5 if i == 0 else 7.5)
        if rec["end"]:            # tool already finished: say nothing more
            return
        narrator_state["spoken"] += 1
        session.say(line, allow_interruptions=True, add_to_chat_ctx=False)
```

---

## 3. Confirmed-good logic — DO NOT "fix" these (a weak AI will want to)

1. **Epoch/version invalidation** (`agent.version` bumped in `invalidate()`/`revise()`, stale
   results dropped in `on_internal`/`still_valid`). This is correct and well-tested; leave it.
2. **Operation ledger** (`agent/ledger.py`): `unknown` outcomes are never auto-retried; late commits
   are reconciled and disclosed. This is the single strongest part of the design — it directly
   answers the guide's "never perform the same state-changing action twice."
3. **State-modifying calls are cancelled only when not yet dispatched** (adapter.py:419-436); once
   dispatched they finish and reconcile via the ledger. Do not change this to unconditional cancel —
   that is how double-bookings happen.
4. **No cross-scenario caching**: fresh `MockAPIRegistry` per room + `providers.reset_state()` per
   room (cascaded_agent.py:259-266). Keep.
5. **The STT de-biasing decision** (whisper_prompt is deliberately vocabulary-free after the
   hallucination incident). Do not re-add example values to the prompt.
6. **`bind_from_results`** chaining (anaphora → previous result ids). Works; the residual
   wrong-args in this area are covered by FC-10.
7. **Compound splitting** (`split_compound`/`_split_more`) — fragile-looking but pinned by many
   tests; only touch via FC-15.

---

## 4. The two guides disagree — and what to do about it

The **docx (FBD, current)** says: score = 60% FDB-v3 re-run + 20% extension + 20% docs, judged by
gpt-4o, LiveKit agent required. The **pdf (v1.0.0)** describes a *different, older* evaluation: a
virtual-clock queue harness, nine public scenarios, categories 40/35/15/10, quality multiplier
0.8–1.2×, multimodal 1.5×.

Your repo serves **both**: `livekit_agent/` + `run_fdb_v3.sh` target the docx; `harness/` +
`run_local.py` + `scenarios/` target the pdf kit. This is correct — but two things to verify with
the organisers *before* the deadline, because they change where to spend effort:

1. **Which evaluation is actually scored in Round 1?** The docx is dated later and says "the current
   Theme-05 guide"; your README already assumes it. Keep that assumption, but get written
   confirmation (email/Discord) and paste a screenshot into `docs/SUBMISSION_CHECKLIST.md`.
2. **Does the hidden ~60-scenario set use the old trace schema (state_snapshot, cancel within
   800 ms grace)?** If yes, your `harness/scorer.py`-compatible behaviours (snapshot on every spoken
   action — `agent.py:394`; cancel grace; filler budget) matter again. Your agent already emits
   snapshots on every `say()` — good; just do not remove that line.

---

## 5. Application order (hand to your AI in exactly this sequence)

| Step | Card | Expected gain | Risk |
|---|---|---|---|
| 1 | **F-A** wire tuned config (code defaults + script exports) | latency −1.5–3 s; fewer fragments → fewer duplicates; spoken numbers as digits | low; replay must stay 92 |
| 2 | **F-B** turn detector required in benchmark mode | removes the ~14 duplicate-call class on the clean re-run | low |
| 3 | **F-C** two workers + **FC-01** + **FC-02** | recovers up to ~15 no-response rooms | low |
| 4 | **FC-05** merge correction fragments | kills duplicate calls from VAD splits | medium; watch offline replay |
| 5 | **F-D** gate arg-continuation on refinability | wrong-args bucket | low |
| 6 | **F-E** emit `speech_started` at VAD onset (guarded) | interruption-recovery metric | medium; test false-onset path |
| 7 | **FC-03/06/07/08/09/10** argument fixes | argument accuracy 60% → 70%+ | medium each; one at a time |
| 8 | **F-G/F-H** filler/narrator hygiene | quality multiplier | trivial |
| 9 | **F-F / FC-21 / FC-22** evidence + README restructure | docs 20% | zero |

After every step: `pytest` → `--offline-text` replay ≥ 92 → `heldout_eval` 29/30 →
`integrity_audit` PASS. Then one full live `--limit 10` smoke run before the next full run.

---

## 6. Honest ceiling estimate

With steps 1–6 done: turn-taken → ~98–100 (from 84–85), duplicates → near 0, exact-match strict pass
realistically **55–65/100**. Under the organisers' gpt-4o semantic judge (which forgives wording,
plurals, and formatting — the exact residual buckets you documented), the *reported* pass rate should
sit meaningfully above that. Combined with a clean extension demo and the restructured docs, this is
the difference between "also ran" and "shortlisted."
