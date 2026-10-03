# 01 — DIAGNOSTIC REPORT (full bug / wrong-logic catalogue)

**Scope:** every defect that costs points on the official FDB-v3 run, ranked by points-at-stake.
**Evidence:** archived live reports in `results/`, the offline-replay reports, and *reproduced
parser probes* (commands included so you can re-run them).
**No code is changed by this package.** Each finding points to a fix card in `02_FIX_SPECS.md`.

---

## 0. How to read severity

| Tag | Meaning |
|---|---|
| **S1** | Caps the whole score (turn-take / reproducibility). Fix first. |
| **S2** | Large, repeated point loss (argument accuracy, extra calls). |
| **S3** | Small, repeated (safety, response quality, latency). |
| **S4** | Cosmetic / evidence hygiene (still free points). |

**Points-at-stake summary** (from the archived live run, judge **off** / exact match):

| Bucket | Count (of 100) | Rough point impact |
|---|---|---|
| No-response rooms (turn-take miss) | 15 (run 2) / 37 (run 1) | **huge** — each zeroes a scenario |
| Imperfect argument | 71 | very large |
| Imperfect tool selection | 43 (30 with *extra* calls, 19 with *missing*) | large |
| Stale/wrong reported numbers | 1 doc | S4 but free |

---

## S1 — Findings that cap the entire score

### BUG-01 (S1) — Turn-take is the dominant loss and it is nearly invisible in the headline
**Evidence.** `results/20260930_live_run/live_eval_report.json`:
```
"turn_taking": {"total":100,"turn_taken":63,"no_response":37,"turn_take_rate":0.63}
```
`results/results.md` (2026-10-02 run): *turn-taken 85/100 — 15 rooms lost to worker deaths*.
**Root cause.** Two separate things:
1. **Worker/room reliability.** On the run box the LiveKit worker process was being killed
   externally (the repo's own note: *"the dev machine's external process kills the worker"*). 15
   rooms are simply never answered → those scenarios score 0 on everything.
2. **The commit gate can strand a turn.** `livekit_agent/adapter.py` `_settle_then_route` waits for
   `settle_s`/`max_settle_s`, and `on_user_speech_end` only arms the timer `if self._pending_final`.
   If a final is empty or the VAD-end event is lost, `_pending_final` can stay empty and nothing is
   ever routed — the agent stays silent for that room.
**Why it dominates.** The official metrics `tool_selection_acc` and `argument_acc` are computed
**over turn-taken samples only**; the `*_all` variants (which include no-response samples scored 0)
were 67.9% / 29.8% vs 77.4% / 43.7% turn-taken in run 1. **One no-response room costs ~3× a wrong
argument.** Fixing reliability is worth more than every parser fix combined.

### BUG-02 (S1) — The one-command reproduction is fragile, and if it fails the 60% benchmark is 0
**Evidence.** `run_fdb_v3.sh` installs `nemo_toolkit[asr]==2.5.3` (large, torch), downloads ~736 MB of
benchmark data via `gdown` (Drive quota can fail), then requires a GPU box and 4 API keys. The
organisers run this on *their* clean machine; the guide says: *"if it still does not run, this
portion scores zero."*
**Root cause.** Too many hard prerequisites with no graceful degradation, plus a pinned Python range
3.10–3.12 that NeMo forces. A single failed step aborts the whole scored run.
**Note.** The repo already fails *loudly* (good), but loud failure still = 0. See
`03_SCORE_MAXIMIZATION.md §Reproducibility` for the hardening plan.

---

## S2 — Argument-accuracy defects (largest content loss)

> All rows below were **reproduced** with the current code. Command:
> `python3 -c "import sys;sys.path.insert(0,'.');from agent import nlu;from livekit_agent.fdb_tools import FDB_TOOLS; ..."`

### BUG-03 (S2) — `extract_id` returns the cue word / a partial id instead of the real id
**Evidence from the live run:**
| scenario | expected | got |
|---|---|---|
| ecommerce_10 | `order_id: 123ABC` | `order_id: ID` |
| ecommerce_13 | `order_id: DELIV` | `order_id: ID` |
| ecommerce_21 | `order_id: BOB` | `order_id: BOP` |

**Reproduced (current code):**
```
'track order id 123 abc'                -> extract_id='123'      (should be 123ABC)
'track my order the id is a b c 1 2 3'  -> extract_id=None
'order ID is DELIV'                     -> extract_id=None
'track order 1 2 3 A B C'               -> extract_id=None
```
**Root cause.** In `agent/nlu.py`:
- `_cue_undashed_id` (≈ L481–509) anchors on the cue noun but then matches `([A-Za-z]{1,6}\d{1,8}|\d{2,8}|...)`
  with `re.I`. Because it is case-insensitive, the literal token `ID` (or the word `ORDER`) can be
  captured as the "id". The later "UPPERCASE only" guard was added but is bypassed when the first
  regex matches early.
- `spelled_ids` (≈ L456) **requires a literal `-`** between characters, so real audio that spells
  `"one two three A B C"` with spaces (no dashes) is never joined. `normalize_spoken_ids` only runs
  when a cue word is present *and* there are ≥2 single-char tokens, but `_join_spelled` demands
  `any(ch.isdigit())` for the short form — `"A B C"` (letters only) only survives via the
  `allow_letters` path, which then needs a cue.
- `extract_id` L512: for a non-prefixed field it prefers `sp[-1]` (the **last** spelled id) which is
  often the wrong one when the utterance contains a date/number.
**Impact.** ~4–6 scenarios per run lose the whole argument check → the scenario fails.

### BUG-04 (S2) — Plural/number agreement on free-text `query`
**Evidence:** ecommerce_08 `expected query="mechanical keyboards"`, `got="mechanical keyboard"`.
**Root cause.** `extract_query` (`agent/nlu.py` L196) returns the literal noun phrase; nothing
normalizes singular↔plural. Under the organisers' **gpt-4o judge** this is *forgivable* (semantic
match), but it fails our local exact-match proxy, which is what we use to measure progress — so it
hides real signal. Fix cheaply (pluralize a bare English noun) but **never** hardcode item strings.

### BUG-05 (S2) — Missing optional-but-checked argument `max_price`
**Evidence:** ecommerce_02/05/16 expected `max_price` (100/300/50) but got only `query`.
**Reproduced:**
```
'i am looking for wireless headphones under 100 dollars' -> {'query': 'wireless headphones', 'max_price': 100}  ✅
'wireless headphones for under a hundred dollars'        -> {'max_price': 100}   ❌ missing 'query'
```
**Root cause.** Two distinct issues:
- When the budget clause comes *first* / the query cue is absent, `extract_query` returns `None`
  (no `_QUERY_CUE` verb present) → the whole `search_products` fails its required `query`.
- `search_products.max_price` is marked `required: False` in `fdb_tools.py`, so a *missing* budget
  never blocks the call — but the benchmark **checks** it. The agent must extract it, not just be
  allowed to omit it.
**Impact.** ~8–10 commerce/housing scenarios.

### BUG-06 (S2) — Currency direction / missing `from_currency`
**Evidence:**
| scenario | expected | got |
|---|---|---|
| finance_01 | `{amount:500, from_currency:USD, to_currency:EUR}` | `{amount:500, to_currency:USD}` |
| finance_04 | `{amount:1000, from:USD, to:GBP}` | `{to_currency:GBP}` |
| finance_20 | `{amount:1000, from:USD, to:EUR}` | `{to_currency:EUR}` |
**Reproduced (good path works):**
```
'convert 500 US dollars to euros'  -> {amount:500, from:USD, to:EUR}   ✅
'how many euros is 500 dollars'    -> {amount:500, from:USD, to:EUR}   ✅
```
**Root cause.** `_currency_pair` (`agent/nlu.py` ≈ L755) decides source by "currency attached to an
amount". When audio delivers the amount **detached** ("a thousand … dollars", or the amount spoken
in a separate fragment), `src_hits` is empty and the fallback `rest[0]` picks the wrong side. Under
fragmented STT (the exact failure mode the benchmark is built to induce) this fires often.
**Impact.** finance domain is the weakest of the four on argument accuracy.

### BUG-07 (S2) — Wrong entity selected after a self-correction
**Evidence:** ecommerce_09 `expected query="hiking boots"`, `got="running shoes"`.
**Reproduced:**
```
'i am looking for running shoes no wait hiking boots' -> extract_query=None   ❌
'show me laptops actually tablets'                    -> extract_query='laptops actually tablets'  ❌ (not repaired)
```
**Root cause.** `extract_query` builds its candidate from `_QUERY_CUE.finditer(clean)` where
`clean = strip_fillers(seg)`. When the query cue is `looking for`, the phrase runs to the end of the
clause and `_QUERY_END` does **not** include `no wait`/`actually`, so the un-repaired full string is
returned. The repair-aware `_repaired_tail` is tried, but the un-repaired `text` branch then wins
because `cands` is non-empty there. Result: the **original** entity is used.
**Impact.** Every SELF_CORRECTION scenario (the benchmark's hardest, L3, weighted ×1.25).

### BUG-08 (S2) — Wrong place captured (last-city-wins vs the corrected city)
**Evidence:** housing_09 `expected city="Chicago"`, `got="Boston"`.
**Reproduced:**
```
'apartments in boston near chicago' -> extract_city=Chicago
```
**Root cause.** `extract_city` → `_pick_after_repair` returns the **last** non-origin city. If the
user says "…in Boston, actually Chicago" the repair marker fixes it, but if the correction arrives
as a *separate STT final* that is not merged, the original stands; and if the user names a landmark
("near Chicago") the landmark wins over the destination. There is also a real tie: `cities_in`
returns every gazetteer hit and the destination role is inferred only by "not origin".
**Impact.** housing domain (worst argument accuracy, 26–37%).

### BUG-09 (S2) — `update_search_filter` gets a garbage `filter_name` and a sentence as `value`
**Evidence:**
| scenario | got |
|---|---|
| housing_03 | `filter_name="Max Price Eighteen", value="Everything I've been seeing is way over budget"` |
| housing_13 | `filter_name="Also Bump Minimum", value="Yeah. I think I need more space…"` |
**Root cause.** `extract_filters` (`agent/nlu.py` ≈ L903–927) has a **generic** fallback
`"<key> to <value>"` that runs whenever the text contains `filter|set|update|change`. Its key-guard
(`_FILTER_KEY_BAD`, ≤3 words) does not catch multi-word verb phrases like `"max price eighteen"` or
`"also bump minimum"`, and its value capture `[a-z0-9][a-z0-9_\-]*` happily grabs a following word.
**Reproduced (clean path works, garbage path broken):**
```
'i want to set the maximum rent filter to eighteen hundred dollars' -> [('max_price', 1800)] ✅
"everything i've been seeing is way over budget so bump the maximum rent" -> []  (no value -> falls to garbage fallback)
```
**Impact.** All `update_search_filter` scenarios (5+ in the live run produced garbage).

### BUG-10 (S2) — Wrong `source_account` (bank account) chosen
**Evidence:** finance_15 `expected savings`, `got checking`; finance_22 `expected checking`,
`got savings`; finance_23 `expected savings`, `got checking`.
**Root cause.** `_special_string_arg` → `settled_mention(text, ACCOUNTS)` returns the **last**
mentioned account. When the utterance contains both ("move autopay off checking, use savings") or a
negated account, the negation guard (`not|no|never|instead of|rather than` + 16 chars) is too narrow.
**Impact.** 3+ finance scenarios.

### BUG-11 (S2) — `destination_address`/`origin_address` normalization
**Evidence:** housing_06 `expected "Gym"`, `got "the gym"`; housing_10 `origin "five hundred Central Ave"`
(words, pre-fix). Current `_clean_place` strips a leading "the" only before a capitalized token
(L953: `re.sub(r"^(?:the)\s+(?=[A-Z])" ,"", s)`), so lowercase `"the gym"` survives.
**Root cause.** `_clean_place` (`agent/nlu.py` L943) keeps `"the gym"` because `_PLACE_WORDS` includes
`gym` but the `the`-strip requires uppercase. The benchmark's expected value is `"Gym"`.
**Impact.** commute scenarios (housing).

### BUG-12 (S2) — `order_id`/`product_id` not bound from a *result* for a follow-up
**Evidence:** ecommerce_06/13/14/15 missing `add_to_cart` / `track_order` entirely.
**Root cause.** `bind_from_results` (`agent/agent.py` L925) only binds `*_id` / `*address`/`location`/
`origin` fields, and only when `_chained` or an anaphor is present. `add_to_cart` needs the
`product_id` produced by `search_products`; `track_order` needs an id from a prior `search_products`
or the utterance. When the id is only in the *result*, the binding is attempted but the `product_id`
field shape (`product_id`, not `*_id` suffix-only) may miss `want` keys. Verified gap: the binding
list prefers `leaf`/`address`/`location` — product results expose `product_id`, which matches the
`ident` branch only if `leaf.endswith("_id")`; `product_id` does, `id` does not.

---

## S2 — Extra / duplicate tool calls (precision killers)

### BUG-13 (S2) — Spurious `search_flights` on non-flight scenarios
**Evidence:** ecommerce_01 `actual: [search_flights, track_order, book_flight]`; ecommerce_10 extra
`search_flights`; travel_03/14/19 extra `search_flights`; travel_20 extra `search_products, search_flights`.
**Root cause.** `score_tools` (`agent/nlu.py` L626) awards a **verb bonus** to any tool whose name verb
is spoken — `_VERB_SYNONYMS["search"]` = `search|find|look(?:ing)? (?:for|up)|…|show me|…`. So the
everyday "I'm trying to **find** my order" gives `search_flights` +1.5, and its head-noun bonus is
small, but the **name-token overlap** via `CONCEPTS["flight"]`/`order` can also fire. Combined with
"book" present in the scenario, `book_flight` also scores. In `on_turn` L670 the code calls
`self.revise(...)` when `cur & fam`, which can *also* emit `search_flights`/`book_flight` as part of
"redo".
**Impact.** 30 samples had an extra call → tool-selection precision < 1 → scenario fails.

### BUG-14 (S2) — Duplicate `search_products` (2–3×) on one turn
**Evidence:** ecommerce_02/05/08 `[search_products, search_products(, search_products)]`.
**Root cause.** Fragmented STT finals are committed separately. The adapter's `_is_arg_continuation`
(`adapter.py` L444) only recognises a **narrow** set of continuations (`on <date>`, `under N`, …);
a bare second fragment like `"under two hundred"` after `"search for mechanical keyboards"` is a
continuation, but `"no wait, hiking boots"` is *also* committed as a new turn and re-issues the
search. The read-only dedup in `call()` (C3) only suppresses an **identical** `op_key`; a changed
`query` is a new call.
**Impact.** ~6 commerce scenarios.

### BUG-15 (S2) — Duplicate `add_to_cart` after a quantity correction
**Evidence:** ecommerce_11 `actual: [add_to_cart, add_to_cart]`.
**Root cause.** `revise_schema_fields` (`agent/agent.py` L1483) is meant to cancel and re-issue a
changed typed field; for a **state-modifying** call whose cancel cannot be confirmed it parks a
`pending_retry` (good) — but `add_to_cart` is treated as `state_modifying` in `FDB_TOOLS`, and the
quantity fix path issues a new call while the first may still land. The op-ledger keys differ
(`quantity:1` vs `quantity:2`) so dedup does not catch it, and the duplicate *completion* is then a
safety deduction **and** an extra tool-selection call.

### BUG-16 (S2) — Spurious `update_search_filter` / `calculate_commute`
**Evidence:** finance_19 extra `update_search_filter`; housing_15/19 extra `calculate_commute`;
housing_14 extra `search_apartments`.
**Root cause.** Same routing issue as BUG-13: `_VERB_SYNONYMS["update"]` matches `change|set|switch|make it`
and `_VERB_SYNONYMS["calculate"]` matches `how long|how far|commute`. A finance sentence containing
"change" or "set" therefore lifts `update_search_filter`, and any "how long" lifts `calculate_commute`.
The tie-break threshold (`ranked[0][0] >= 1.5`) is too low.

---

## S3 — Latency, safety and response-quality defects

### BUG-17 (S3) — Latency is 5–25 s; the commit gate + narrator add directly to it
**Evidence.** `live_eval_report.json`: `avg_response_latency_s: 5.769`, `max: 25.28`.
`results.md`: "the commit gate could wait ~10 s while text looked unfinished".
**Root cause.**
1. `adapter.py` `_settle_then_route` re-arms while `turn_looks_unfinished` is true, bounded by
   `max_settle_s` (default 2.0 s) — but the *live* defaults in `cascaded_agent.py` are
   `SETTLE_S=1.0`, `MAX_SETTLE_S=2.0`, so a normal turn already waits ≥1 s before routing.
2. `cascaded_agent.py` `_progress_narrator` speaks *"Still working on that."* at 3.5 s and
   *"One moment…"* at 11 s. These are `session.say` with `allow_interruptions=True` but they are
   **agent audio**, so the official runner's audio→audio latency may start on them (helpful) — but
   they are non-substantive for our internal metric and they add filler/quality noise.
3. Deepgram STT is configured with plugin **defaults** (`endpointing_ms=25`, `smart_format=False`,
   `numerals=False`) per the comment in `speech_providers.py` — this fragments turns and returns
   spoken numbers as words, which *causes* BUG-05/BUG-06 *and* forces the settle gate to wait longer.
**Impact.** Latency 15% of the internal score; in FDB it is a reported metric and a tie-breaker.

### BUG-18 (S3) — Backchannel can be counted as "the response" or spam fillers
**Evidence.** `cascaded_agent.py` `on_transcript` calls `session.say("Mm-hm.", …)` whenever
`should_backchannel()` (default on, ≥3 words). The official runner records agent audio; a
backchannel that lands *before* the real answer helps latency but hurts truthfulness/non-redundancy,
and repeated "Mm-hm." is a canned loop.
**Root cause.** The internal tracker excludes backchannels (`turn_state["backchannel_pending"]`),
but the **official** runner has no such notion — it sees agent speech.

### BUG-19 (S3) — Premature completion claims (safety deduction, ×0.5 cap)
**Evidence.** `harness/scorer.py` `CLAIM_PATTERNS` + `FUTURE_GUARDS`; the internal scorer can deduct
up to 0.5. The live agent's `describe_success` says *"Done — you're booked…"* only after the result,
which is correct, but `on_flights` → `issue_booking(announce="Found … — booking it for {name} now.")`
says "booking it … now" (guarded by "now" → fine). Risk is low but the *progress narrator* lines
("Still working on that.") are safe. The dangerous one is `_exec_planned` which says `self.ack(...)`
before the call — ack is a promise, safe.
**Action:** audit only; keep the future-guard wording.

### BUG-20 (S3) — Protocol / snapshot hygiene
**Evidence.** `protocol.py` requires a non-empty `text` for every spoken action and a
`state_snapshot` on `final_response`. `agent.py` `say()` always attaches a snapshot (L374) — good.
Risk: `clipped`/empty filler strings would raise a `protocol_error` (−0.10 each). No current
violation found; keep it.
**One real risk:** `say()` **dedups fillers by text** and returns *without* emitting anything when
the budget is exhausted (L363–370). If the *only* response for a turn was a filler that got
suppressed, the turn-take is lost → BUG-01. **Never let filler suppression suppress the
`final_response`** (it doesn't today — `final_response` bypasses the budget — but verify after any
change).

---

## S4 — Evidence / submission defects (free points)

### BUG-21 (S4) — Reported numbers are stale and contradictory
**Evidence.**
- `results/results.md` headline: **91.0%** (offline text) and a "FINAL LIVE RUN … 28/100".
- `results/20260930_live_run/pass_rate_report.json`: `overall_pass_rate: 0.13` (13/100).
- Latest commit message: *"strict pass 28 → 42/100, tool-sel 87.7%, arg 58.8%"*.
Four different live numbers appear in the submission. A reviewer reads the **first** one they see
(the 13/100 or 28/100), not the latest.
**Root cause.** New runs were appended to `results.md` but the headline/table were not regenerated,
and older `results/*` folders were left in place.
**Action.** Regenerate `results/results.md` from the latest report; move superseded folders to
`results/archive/`; state the mode of every number.

### BUG-22 (S4) — The README's own "known simplifications" undersells the submission
**Evidence.** README lists "Location/vehicle extraction … regex/keyword based", "gateway keeps
sessions process-local", etc. Honest is good, but the guide scores **documentation/architecture/
video 20%**. The README buries the *extension use case* (Triage Line) and the *one-command
reproduction* below long diagnostics. A reviewer skimming for "does it run end to end?" may miss it.

### BUG-23 (S4) — Judge coverage / mode mixing
**Evidence.** `run_fdb_v3.sh` has `judge_coverage.py` and warns when checks fall back to exact match.
The archived runs are judge-*off*. If the organisers' judge is on, our numbers are a **lower bound** —
that is true and should be stated once, clearly, next to every number (it partly is).

---

## 5. Verified-working logic (DO NOT "fix" these)

To stop a weak AI from breaking good code, these were checked and are correct:

| Logic | Where | Verified |
|---|---|---|
| Repair-aware `extract_city` (`"to boston no wait chicago"` → Chicago) | `nlu.py` `_pick_after_repair` | ✅ probe |
| Spelled-id join with dashes (`"T-L-W-3-8-2"` → TLW382) | `nlu.py` `spelled_ids` | ✅ |
| Compound amounts (`"eighteen hundred"` → 1800, `"1,500"` → 1500) | `nlu.py` `_compound_normalize` | ✅ probe |
| Currency direction on clean input | `nlu.py` `_currency_pair` | ✅ probe |
| Filter extraction on clean input (`"maximum rent filter to eighteen hundred"`) | `nlu.py` `extract_filters` | ✅ probe |
| `search_apartments` full args incl. `pets_allowed` | `nlu.build_args` | ✅ probe |
| Epoch guard / stale-result drop | `agent.py` `invalidate`/`still_valid` | ✅ code |
| Non-blocking tool tasks | `adapter.py` `_pump_outputs`/`_run_tool` | ✅ code |
| No-participation gate respected (never go silent) | `agent.py` `say` | ✅ code |

---

## 6. Root-cause → fix-card map

| Bug | Fix card | Category |
|---|---|---|
| BUG-01 turn-take | FC-01, FC-02 | reliability |
| BUG-02 reproduction | FC-20 | reproducibility |
| BUG-03 id extraction | FC-03 | arguments |
| BUG-13/16 spurious calls | FC-04 | tool selection |
| BUG-14/15 duplicates | FC-05 | tool selection / safety |
| BUG-05/09 missing/garbage args | FC-06 | arguments |
| BUG-06 currency | FC-07 | arguments |
| BUG-07/08 self-correction | FC-08 | arguments |
| BUG-10/11 accounts/places | FC-09 | arguments |
| BUG-12 result binding | FC-10 | arguments |
| BUG-17 latency | FC-11, FC-12 | latency |
| BUG-18 backchannel/narrator | FC-13 | quality |
| BUG-04 plurals | FC-14 | arguments |
| BUG-21/22/23 evidence | FC-21, FC-22 | submission |
