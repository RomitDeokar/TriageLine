# TriageLine — Full Score-Maximisation Analysis Report

**Purpose:** everything found by auditing this repo against the two Theme-05 guides
(`Theme05_Participant_Guide_UPDATED_FBD.docx`, `Theme 5_Guide.pdf`) and against the
**actual failure data of your own live runs** (`results/live_20260930T200524Z/pass_rate_report.json`,
`results/20260930_live_run/live_eval_report.json`, `results/results.md`).

**This report makes NO code changes.** Every fix below is written so that a weaker AI (or a
human) can apply it mechanically: exact file, exact function, exact before/after code, and the
exact command that proves the fix worked.

---

## 0. How the score is actually computed (read this first)

From the participant guide:

| Component | Weight | What counts |
|---|---|---|
| **FDB-v3 benchmark, re-run by the organisers** | **60%** | Only THEIR re-run counts (`./run_fdb_v3.sh` on a clean machine, pinned gpt-4o judge ON). Metrics: tool-selection acc, argument acc, **strict pass rate** (tie-breaker), latency. |
| Extension use case (Triage Line) | 20% | Runs end-to-end + shown working in the video. |
| Docs / architecture / video | 20% | README, honest numbers, real demo footage. |

Your current trajectory (your own live runs, judge OFF = exact match = lower bound):

| run | strict pass | tool-sel | arg acc | turn-taken | avg latency |
|---|---|---|---|---|---|
| 2026-09-30 | 28/100 | 80.6% | 39.7% | 99/100 | 5.8 s |
| 2026-10-02 | 39/100 | 84.9% | 56.9% | 85/100 | — |
| 2026-10-03 | 42/100 | 87.7% | 58.8% | 85/100 | — |
| **offline text replay (same code)** | **92/100** | 98.8% | 93.7% | 100/100 | n/a |

**The single most important fact in this report:** the same code scores **92/100 on clean text**
but **42/100 on live audio**. That 50-point gap is not "the agent is dumb" — it is
(a) 15 rooms where the worker died (infrastructure, worth ~15 points on the organisers' stable machine, free),
(b) STT-degraded transcripts breaking your regex parsers (argument acc 93.7% → 58.8%), and
(c) fragmented endpointer finals producing duplicate/missing calls (tool-sel 98.8% → 87.7%).

Failure split in the recorded live run: **43 wrong-tools, 29 wrong-arguments**, and by domain
**travel_identity 5% pass, housing 15.4%, ecommerce 34.5%, finance 52%**. By disfluency:
**SELF_CORRECTION 11.8%, FILLER 17.2%, PAUSE 22.2%** — exactly the categories the guide says
win the benchmark.

---

## 1. Priority matrix (do them in this order)

| # | Fix | Where | Expected gain | Risk | Effort |
|---|---|---|---|---|---|
| P0 | Enable the already-built LLM planner (currently pinned OFF) | `run_fdb_v3.sh` | +10–20 strict | low-moderate | 30 min + 1 live run |
| P1 | Deepgram `numerals/smart_format/punctuate/endpointing` flags (env knobs referenced in a comment **do not exist**) | `livekit_agent/speech_providers.py` | +5–10 strict, −1–2 s latency | low (retune settle with it) | 1 h |
| P2 | Late argument fragments are dropped when the arg wasn't in the original call ("under 300", "on July 15") | `agent/agent.py: revise_schema_fields` | +4–8 strict | low | 1–2 h |
| P3 | Chained "add it to my cart" during an in-flight search is treated as intent **switch** → cancels the search it depends on → missing call | `agent/agent.py: on_interruption` | +3–6 strict | low | 1 h |
| P4 | Date corrections "no, the seventh" don't revise (no month in the fragment) | `agent/nlu.py` + `agent/agent.py: revise` | +1–3 strict | low | 1 h |
| P5 | `update_search_filter` emits garbage `filter_name`/`value` from verb phrases; numeric values stay strings | `agent/nlu.py: extract_filters` | +2–4 strict | low | 1 h |
| P6 | source_account / bill_type corrections never trigger a revise (fields not "typed") | `agent/agent.py: _typed_field` | +1–3 strict | low | 30 min |
| P7 | Spurious EXTRA calls from garbage fragments (wrong_tools=43) — unify/raise the barge-in new-intent threshold | `agent/agent.py: on_interruption` | +2–5 strict | medium | 1 h |
| P8 | Join space-separated letter+digit ids ("DL 555" → "DL555"); amount fallbacks ("5 grand", "a thousand dollars") | `agent/nlu.py` | +1–3 strict | low | 1 h |
| P9 | Latency trim: settle 1.0→0.6, max_settle 2.0→1.2, narrator first line 3.5→2.5 s | `livekit_agent/cascaded_agent.py` | latency metric | medium | 30 min + replay |
| P10 | Idempotency key: "1500" (string) vs 1500 (number) produce different op keys → duplicate state-modifying calls | `agent/agent.py: _canon` | safety points | low | 15 min |
| P11 | Reproduction hardening (the 60% is zero if the script fails on their machine) | `run_fdb_v3.sh` + docs | protects 60% | low | 1 h |
| P12 | Judge/quality multiplier polish (response wording, no repeated canned fillers) | `agent/nlu.py` ack/done phrases | ×0.8–1.2 multiplier | low | 1 h |

Estimated cumulative: strict pass **42 → 60–75** (exact-match) before the organisers' more
forgiving judge lifts it further. Nothing here pattern-matches benchmark items; everything is
generic language/timing behaviour, so it survives the hidden set and the integrity audit.

---

## 2. P0 — Enable the LLM planner you already built (biggest single win)

### Symptom (evidence)
Offline text replay: argument accuracy **93.7%**. Live: **58.8%**. Your rule parser is excellent
on clean text and brittle on STT output — that is exactly the problem an LLM is good at
(noisy, ungrammatical, disfluent transcripts). You already wrote the whole planner:
`agent/llm_planner.py` (schema-validated, temperature 0, max 4 calls) with a failover chain
(`agent/providers.py`: Gemini → Cerebras → OpenRouter → Mistral). **It is pinned off:**

```bash
# run_fdb_v3.sh, stage_2_configure(), line ~140
export TRIAGELINE_LLM_PLANNER="${TRIAGELINE_LLM_PLANNER_OVERRIDE:-0}" TRIAGELINE_LLM_MODE=fallback
```

`fallback` mode means: rules first; the planner is consulted **only** when the rules cannot
build a complete call (`agent/agent.py: start_task` → `if missing and await self.llm_fallback(...)`).
So enabling it changes nothing for the 92% of turns the rules already nail, and rescues the
turns where the transcript is mangled. The agent's safety story is unchanged: every planner
call still passes `llm_planner.validate()` (schema/enum/bounds/required-args) and then the
epoch + ledger + dedup gates.

### Fix (3 edits)

**Edit 1 — `run_fdb_v3.sh` (line ~140):**

```bash
# BEFORE
export TRIAGELINE_LLM_PLANNER="${TRIAGELINE_LLM_PLANNER_OVERRIDE:-0}" TRIAGELINE_LLM_MODE=fallback

# AFTER
export TRIAGELINE_LLM_PLANNER="${TRIAGELINE_LLM_PLANNER_OVERRIDE:-1}" TRIAGELINE_LLM_MODE=fallback
export TRIAGELINE_LLM_TIMEOUT_S="${TRIAGELINE_LLM_TIMEOUT_S:-3}"   # voice: never wait 8 s (default in providers.py)
```

**Edit 2 — `livekit_agent/.env.example` (and your real `.env.local`):** document that the
planner needs one key, Gemini free tier is enough:

```
# LLM planner (fallback mode: only used when the rules can't build a complete call)
GEMINI_API_KEY=...        # free at ai.google.dev — enables TRIAGELINE_LLM_PLANNER=1
```

**Edit 3 — `agent/llm_planner.py`, `SYSTEM` prompt:** add one sentence so the planner expects
ASR garbage instead of taking it literally:

```python
SYSTEM = (
    "Convert the spoken user request into the next executable tool call. Use ONLY declared tools. "
    "The transcript comes from speech recognition and may contain misheard words, missing words, "
    "or stray filler; prefer the interpretation that forms a complete, plausible call. "
    "Resolve hesitations and self-corrections using the latest value. Never execute a negated or "
    ...  # (rest unchanged)
)
```

### Risks and the guards already in place
- *Latency:* the planner runs in a background task (`agent.py: llm_fallback` → `self.spawn(work())`);
  the event loop never blocks. Worst case adds `TRIAGELINE_LLM_TIMEOUT_S` (3 s) to turns the rules
  couldn't handle anyway (which today produce a wrong/garbage call or a clarification the
  benchmark never answers — both score 0).
- *Hallucinated tools:* `validate()` drops unknown names, undeclared args, wrong types,
  out-of-range numbers, non-enum values.
- *Duplicate state changes:* unchanged — `_exec_planned` goes through `issue_booking` / `call()`
  with the same operation ledger.

### Verify
```bash
python livekit_agent/replay_live_failures.py            # pass count should rise on the 29 live transcripts
./run_fdb_v3.sh --offline-text                          # must stay >= 92/100 (rules still dominate)
./run_fdb_v3.sh --limit 5 --require-judge               # live smoke with planner on
```

---

## 3. P1 — Deepgram flags: the code comment lies (env knobs don't exist)

### Symptom
`results/results.md` records that adding `punctuate/smart_format/numerals` + `endpointing_ms=350`
was worth **+6 strict and −11 degenerate transcripts** in the audio replay ("65 → 71" pass).
But look at the code:

```python
# livekit_agent/speech_providers.py, build_stt() (~line 162)
# NOTE: the plugin default is endpointing_ms=25 with smart_format/numerals off. ... Tune via
# TRIAGELINE_DEEPGRAM_* before changing this.
return deepgram.STT(model=model, language="en-US", interim_results=True, filler_words=True, **kw)
```

**`TRIAGELINE_DEEPGRAM_*` is referenced in the comment but no code reads it** (verified:
`grep -rn "TRIAGELINE_DEEPGRAM"` finds only the comment). The defaults the comment warns about
are exactly what ships: numbers come back as words ("five hundred" instead of `500`), and
`endpointing_ms=25` fragments every utterance into many finals — the root cause of half the
duplicate-call failures.

### Fix — `livekit_agent/speech_providers.py`, replace the deepgram branch:

```python
def _env_flag(name: str, default: str) -> bool:
    return os.environ.get(name, default) == "1"

# inside build_stt():
    if p == "deepgram":
        from livekit.plugins import deepgram
        kw = {"keyterm": terms[:50]} if terms and model.startswith("nova-3") else {}
        # Measured on the official audio replay (results/results.md 2026-10-01): formatting+numerals
        # and a real endpointing window are worth +6 strict pass. All are env-overridable so a bad
        # interaction can be rolled back without a code change.
        return deepgram.STT(
            model=model, language="en-US", interim_results=True, filler_words=True,
            punctuate=_env_flag("TRIAGELINE_DEEPGRAM_PUNCTUATE", "1"),
            smart_format=_env_flag("TRIAGELINE_DEEPGRAM_SMART_FORMAT", "1"),
            numerals=_env_flag("TRIAGELINE_DEEPGRAM_NUMERALS", "1"),
            endpointing=int(os.environ.get("TRIAGELINE_DEEPGRAM_ENDPOINTING_MS", "300")),
            **kw)
```

**Retune the commit gate to match the new final timing** (they are one system):

```python
# livekit_agent/cascaded_agent.py (~line 98)
_default_settle = "0.6" if MODE == "benchmark" else "0.9"      # was "1.0"
MAX_SETTLE_S = float(os.environ.get("TRIAGELINE_MAX_SETTLE_S", "1.2"))   # was "2.0"
```

### Verify
Run the audio replay diagnostic (the one that produced the 65→71 numbers in `results.md`)
before and after; expect `degenerate (≤3-word) transcripts` to stay 0 and argument accuracy
to rise (spoken numbers arrive as digits, so `extract_number` stops missing them).
Then `./run_fdb_v3.sh --limit 5 --require-judge`.

---

## 4. P2 — Late argument fragments are silently dropped

### Symptom (from the live failure report)
```
ecommerce_02  search_products  exp={"query":"wireless headphones","max_price":100}  act={"query":"wireless headphones"}
ecommerce_05  search_products  exp={"query":"desk","max_price":300}                 act={"query":"desk"}
housing_05    search_apartments exp={...,"max_price":1500,"pets_allowed":true}     act={"city":"Miami","pets_allowed":true}
travel_01     search_flights   exp={"destination":"Tokyo","date":"July 15"}        act={"date":"today"}   (destination lost too)
```

### Root cause
The utterance arrives as two finals: `"find me wireless headphones"` … `"under 100"`.
The adapter correctly detects the second as an argument continuation
(`adapter.py: _is_arg_continuation` matches "under …") and routes it to `on_barge_in` →
`ParticipantAgent.on_interruption` → `revise()` → `revise_schema_fields()`.
But there, for in-flight calls:

```python
# agent/agent.py, revise_schema_fields() (~line 1507)
diff = {k: v for k, v in found.items() if k in c["args"] and self._typed_field(k, props.get(k, {}))
        and str(v).casefold() != str(c["args"][k]).casefold()}
```

`k in c["args"]` — **a fragment can only overwrite an argument that was already set.**
`max_price` was never in the first call, so `diff` is empty, nothing is re-issued, and the
budget the user spoke is thrown away. Same for a late "on July 15" when the date was defaulted
to `"today"` (the default is written into `args`, **not** into `state["slots"]`, so `revise()`
doesn't even see a change to compare against).

### Fix — `agent/agent.py`, `revise_schema_fields()`, two changes:

```python
# 1) In the in-flight loop, allow ADDING a missing OPTIONAL argument (not just overwriting):
diff = {k: v for k, v in found.items()
        if self._typed_field(k, props.get(k, {}))
        and str(v).casefold() != str(c["args"].get(k, "")).casefold()
        and (k in c["args"] or not props.get(k, {}).get("required"))}
#                                     ^^^ new: an optional arg the user just supplied is added

# 2) Same for the last_done (already-completed read) branch (~line 1491): it currently requires
#    (k in ld["args"] or REPAIR_MARKERS). Add the same optional-add:
        and (k in ld["args"] or nlu.REPAIR_MARKERS.search(text)
             or not props.get(k, {}).get("required"))}
```

And in `revise()` (~line 1566), treat a **defaulted** date as replaceable:

```python
# BEFORE:  if d and d != slots.get("date"): changed["date"] = d
# AFTER: compare against the value actually used (slots OR the last call's assumed args)
d = nlu.extract_date(text)
current_date = slots.get("date") or next(
    (c["args"].get("date") for c in self.inflight.values() if "date" in c.get("args", {})), None)
if d and d != current_date:
    changed["date"] = d
```

### Verify
`python livekit_agent/replay_live_failures.py --only ecommerce` — the three `max_price` misses
should flip to pass. Offline text replay must stay ≥ 92 (these utterances were single-fragment
there, so nothing changes).

---

## 5. P3 — "add it to my cart" mid-search is treated as an intent switch (kills chained calls)

### Symptom
```
ecommerce_06  MISSING: ['add_to_cart']      ecommerce_13  MISSING: ['add_to_cart','track_order']
ecommerce_14  MISSING: ['add_to_cart']      travel_18     MISSING: ['book_flight']
```

### Root cause — `agent/agent.py: on_interruption()` (~line 1466):

```python
if switch or (nlu.INTENT_SWITCH.search(low) and top):
    await self.cancel_where(lambda c: True)     # <-- cancels the in-flight search
    self.invalidate()
    self.state = {"intent": None, "slots": {}}  # <-- wipes the slots
    ...
    await self.on_turn(text)
```

The user says `"find me a desk under 300"` (search starts) then, while it runs, `"and add it to my cart"`.
`score_tools` ranks `add_to_cart` top; it is a different family than the in-flight
`search_products`, so `switch=True` → **the search is cancelled and all state wiped**. Then
`on_turn("add it to my cart")` runs with no results to bind `product_id` from → required arg
missing → benchmark policy never asks for state-modifying calls → **no call at all**.

### Fix — `agent/agent.py`, in `on_interruption()`, BEFORE the switch branch:

```python
# A short barge-in whose action DEPENDS on the in-flight read (anaphora: "it", "that one")
# is a chained step, not an intent switch: queue it to run after the read completes.
if switch and self.inflight and self._ANAPHORA.search(text):
    readonly_inflight = [c for c in self.inflight.values()
                         if self.tools.get(c["api"], {}).get("kind") != "state_modifying"]
    if readonly_inflight:
        self.compound = (self.compound or []) + [text]
        self.compound_version = self.version
        return await self.say("filler_speech", "Got it — I'll do that as soon as this comes back.",
                              priority=True)
```

`_drain_compound()` already waits for `self.inflight` to empty and then runs the clause with
`self._chained = True`, so `bind_from_results()` resolves `product_id` from the search result.
No new machinery needed.

### Verify
`python livekit_agent/replay_live_failures.py --only ecommerce` — `missing_call` count should drop.

---

## 6. P4 — Date corrections without a month ("no, the seventh") never revise

### Symptom
```
travel_10  search_flights  exp={"destination":"Miami","date":"October 7"}  act={...,"date":"October 5"}
```

### Root cause
`nlu.extract_date()` only matches month+day, weekdays, ISO, or "today/tomorrow". A correction
fragment `"no wait, the seventh"` contains no month → `extract_date` returns `None` →
`revise()` sees no change → the stale date stays.

### Fix — two parts.

**Part A — `agent/nlu.py`, add a day-only extractor (generic, works with any known month):**

```python
_DAY_ONLY = re.compile(r"\b(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b|\b(" + _ORD_ALT + r")\b", re.I)

def extract_day_correction(text: str) -> Optional[int]:
    """A bare ordinal day ('the seventh', 'the 12th') — only meaningful as a correction to a
    date whose month is already known. Returns the day-of-month or None."""
    m = _DAY_ONLY.search(text or "")
    if not m:
        return None
    if m.group(1):
        return int(m.group(1))
    return _ORD_WORDS.get(m.group(2).lower())
```

**Part B — `agent/agent.py`, in `revise()` after the `extract_date` block:**

```python
d = nlu.extract_date(text)
if not d:
    day = nlu.extract_day_correction(text)
    cur = slots.get("date") or ""
    mon = re.match(r"([A-Za-z]+)\s+\d{1,2}", cur)          # "October 5" -> month "October"
    if day and mon and nlu.REPAIR_MARKERS.search(text):
        d = f"{mon.group(1)} {day}"
if d and d != slots.get("date"):
    changed["date"] = d
```

### Verify
`tests/test_nlu_numbers.py`-style unit test + `replay_live_failures.py --only travel`.

---

## 7. P5 — `update_search_filter` garbage arguments

### Symptom
```
housing_03  exp={"filter_name":"max_price","value":1800}
            act={"filter_name":"Max Price Eighteen","value":"Everything I've been seeing is way over bu…"}
housing_24  exp={"filter_name":"max_price","value":1500}  act={"filter_name":"i_want","value":"update"}
housing_25  exp={"filter_name":"max_price","value":3500}  act={"filter_name":"max_price","value":"thirty"}
```

### Root cause — `agent/nlu.py: extract_filters()`.
The generic fallback branch (`"<key> to <value>"`) accepts almost anything as a key
(`i_want` slipped through because `re.sub(r"^(?:.*\b(?:for|filter|the|set|update|change|and|my)\s+)", "", ...)`
doesn't strip `"i want"`). And the value `raw="thirty"` isn't a digit so it's kept as a **string**
even though the schema expects a number. Also `housing_03`: the first `_FILTER_KEYS` pass found
nothing ("max price to eighteen hundred" needed `_compound_normalize` which now runs, but the
fallback then re-matched garbage).

### Fix — `agent/nlu.py`, three edits inside `extract_filters()`:

```python
# 1) In the generic "<key> to <value>" loop, word-numbers must become numbers:
    raw = m.group(2)
    if raw in ("true", "yes", "on"):
        val = True
    elif raw in ("false", "no", "off"):
        val = False
    elif re.fullmatch(r"\d+(?:\.\d+)?", raw):
        fv = float(raw); val = int(fv) if fv.is_integer() else fv
    else:
        wv = _compound_value(raw.split())            # NEW: "thirty" -> 30, "eighteen hundred" -> 1800
        val = wv if wv is not None else raw

# 2) Expand the key blocklist so verb-phrase debris can never be a key:
_FILTER_KEY_BAD |= {"i", "im", "want", "wanna", "update", "change", "make", "set", "go",
                    "everything", "way", "over", "budget_is"}

# 3) Canonicalise spoken keys: "max price", "maximum price", "price cap" must all land on the
#    schema's real key. Add at the end, before `return`:
_KEY_CANON = {"max_price": {"max price", "maximum price", "price cap", "price limit", "budget", "max rent"},
              "min_price": {"min price", "minimum price"},
              "bedrooms": {"bedrooms", "beds", "bed"}, "min_bedrooms": {"min bedrooms", "minimum bedrooms"},
              "pets_allowed": {"pets", "pets allowed", "pet friendly"},
              "neighborhood": {"neighborhood", "neighbourhood", "area"},
              "parking": {"parking"}, "furnished": {"furnished"}}
_CANON_OF = {alias: key for key, aliases in _KEY_CANON.items() for alias in aliases}
# apply:  k = _CANON_OF.get(k.replace("_", " "), k)   right after k is computed
```

### Verify
`python -c "from agent import nlu; print(nlu.extract_filters('set max price to eighteen hundred'))"`
→ `[('max_price', 1800)]`. Then replay harness on housing.

---

## 8. P6 — Account/bill-type corrections never trigger a revise

### Symptom
```
finance_15/23  exp={"bill_type":"mortgage","source_account":"savings"}  act={...,"source_account":"checking"}
finance_22     exp={...,"source_account":"checking"}                     act={...,"source_account":"savings"}
```

### Root cause — `agent/agent.py: _typed_field()` (~line 1479):
a correction only revises args that are "typed" (enum / number / boolean / *_id / place / date).
`source_account` and `bill_type` are plain strings in the manifest, so "actually, from savings"
produces no diff and the in-flight call is never corrected.

### Fix — `agent/agent.py`:

```python
TYPED_ROLE = ("city", "destination", "location", "origin", "date",
              "account", "bill_type", "card_type", "currency", "filter")   # was 5 entries
```

These fields are all vocabulary-bound (ACCOUNTS / BILL_TYPES / CURRENCY_WORDS / filter keys),
so a spurious "revision" from free text is unlikely; risk is low.

---

## 9. P7 — Spurious EXTRA tool calls (wrong_tools = 43)

### Symptom
```
housing_15  EXTRA: ['calculate_commute','search_apartments','search_apartments']   (expected 1 call)
finance_12  EXTRA: ['calculate_commute','get_exchange_rate']
housing_09  EXTRA: ['search_products']        finance_19  EXTRA: ['get_exchange_rate','update_search_filter']
travel_03   EXTRA: ['search_flights']         ecommerce_20  EXTRA: ['track_order']
```

### Root cause
Garbage STT fragments still clear the barge-in new-intent bar: `on_interruption` accepts a new
tool at score **≥ 2.0**, while `settle_for` uses **1.5** and `_actionable` uses **2.5** — three
different thresholds for the same decision. Concept-overlap scoring (`_concepts`) lets a
fragment like "uh so the rate… wait" hit 2.0 on `get_exchange_rate`.

### Fix — `agent/agent.py`, `on_interruption()` (~line 1459):

```python
# BEFORE
top = ranked[0][1] if ranked and ranked[0][0] >= 2.0 else None

# AFTER: a barge-in may only open a NEW intent on strong, specific evidence —
# a high score AND (the tool's head noun or its action verb actually spoken).
top = None
if ranked and ranked[0][0] >= 2.5:
    cand = ranked[0][1]
    name_words = nlu.tokens(cand.replace("_", " "))
    head_noun = next((w for w in reversed(name_words) if nlu._stem(w) not in nlu._ACTION_VERB_STEMS), "")
    verb = next((w for w in name_words if nlu._stem(w) in nlu._ACTION_VERB_STEMS), "")
    if (head_noun and any(nlu._stem(w) == head_noun for w in nlu.tokens(text))) or \
       (verb and nlu._verb_spoken(verb, text)):
        top = cand
```

Also harden `_shopping_request` (`agent/nlu.py`) — it currently rejects apartment/flight
concepts found in the **query**, but a city or bedroom mention **anywhere in the fragment**
should veto a product search:

```python
# in _shopping_request(), right after the _SHOP_CUE check:
if cities_in(text) or re.search(r"(?i)\b(?:bedrooms?|rent|lease|apartment|flat|commute|flight|fare)\b", text or ""):
    return False
```

### Verify
Replay harness: `extra_call` count must drop; offline text replay must not drop (92/100 —
legitimate multi-tool turns score well above 2.5 with head nouns present).

---

## 10. P8 — Identifier & amount extraction gaps

### Symptom
```
travel_07  exp={"doc_number":"DL555"}  act={"doc_number":"DL"} / {"doc_number":"five five five"}
travel_02  exp={"doc_number":"P9-9-9-90011"}  act={"doc_number":"P88990011"}   (mis-heard digits: judge may forgive)
ecommerce_21 exp={"order_id":"BOB"} act={"order_id":"BOP"}                     (STT error: judge may forgive)
finance_01/04  exp={"amount":500,...}  act={"to_currency":"EUR"}               (amount + from_currency lost)
```

### Fixes — `agent/nlu.py`:

**(a) Join letter-run + digit-run across a space**, in `_arg_for`'s `_id` branch (~line 1034),
before the single-letter fallback:

```python
m = re.search(r"\b([A-Za-z]{1,4})\s+(\d{2,8})\b", text)   # "DL 555" -> "DL555"
if m:
    return (m.group(1) + m.group(2)).upper()
```

**(b) Digit-words immediately after a short letter token**, in `normalize_spoken_ids`:
extend the cue gate so that after a 1–4-letter uppercase token, a following run of digit words
joins even without a fresh cue word (the cue is already `"license"`/`"id"` two words back —
widen `words[:i]` cue scan or accept runs starting right after an ALL-CAPS token).

**(c) Amount fallbacks**, in `extract_number()` in the `amount` branch:

```python
if lname in ("amount", "value_amount", "sum") or lname.endswith("_amount"):
    m = _settled_match(...)                # existing
    if m:
        return _num(m.group(1) or m.group(2), integer)
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*grand\b", t, re.I)          # NEW: "5 grand" -> 5000
    if m:
        return _num(str(float(m.group(1)) * 1000), integer)
    nums_left = NUMBER_RE.findall(t)                                  # NEW: exactly one bare number -> use it
    if len(nums_left) == 1:
        return _num(nums_left[0], integer)
```

Note `finance_01/04` also lost `from_currency` — with the amount anchored, `_currency_pair`'s
"amount-attached currency = source" heuristic starts working, so (c) fixes both fields.

---

## 11. P9 — Latency (currently ~5.8 s avg; guide wants "a few hundred ms" to first feedback)

The FDB-v3 scorer measures `avg_response_latency_s` (yours: 5.77 s, max 25.28 s). The practice-kit
scorer weights latency 15%. Biggest levers, in order:

| Lever | File | Change | Saves |
|---|---|---|---|
| Commit gate | `cascaded_agent.py` | settle 1.0→**0.6**, max_settle 2.0→**1.2** (pairs with P1 endpointing=300) | ~0.4–0.8 s/turn |
| Progress narrator | `cascaded_agent.py` `_progress_narrator` | first line at **2.5 s** (was 3.5), second at 6 s | kills dead-air on slow chains |
| Backchannel | already instant ("Mm-hm." on final transcript) | keep | first_audio ≈ STT final + TTS |
| Turn detector | `run_fdb_v3.sh` stage 1 | make sure `python -m livekit.agents download-files` succeeded on the run machine; check `run_config.json: turn_detector == "1"` | prevents the 15–26 s tail |
| Planner timeout | P0 | `TRIAGELINE_LLM_TIMEOUT_S=3` | caps planner stalls |

Also: `adapter.py: _route_final`'s 8-second continuation window (`_is_arg_continuation`) is
shorter than some mock tool latencies (10 s+ profiles). Raise it so a post-result fragment
still amends instead of starting a duplicate request:

```python
# adapter.py (~line 346)
if (self._last_route_at and (time.time() - self._last_route_at) < 15.0     # was 8.0
        and _is_arg_continuation(text)):
```

**Do not** chase sub-second totals by cutting settle to 0 — that re-fragments utterances and
re-creates the duplicate-call failures (you already measured this: commit `7421de4` reverted an
over-aggressive retune). Change settle **only together with** the Deepgram endpointing flag (P1).

---

## 12. P10 — Idempotency keys treat `1500` and `"1500"` as different calls (duplicate state changes)

### Root cause — `agent/agent.py: _canon()`:
strings keep their value; a state-modifying call re-issued after a revision with
`value=1500` (int) vs `value="1500"` (str, see P5) produces **two different op keys** → the
operation ledger doesn't dedupe → two logged `update_search_filter` calls →
wrong_tools + safety deduction (the practice-kit scorer deducts 0.5 per duplicate
state-modifying completion; the FDB evaluator counts the extra call as unexpected).

### Fix — `agent/agent.py`, in `_canon()`:

```python
    if isinstance(v, str):
        s = nlu.norm(v)
        if re.fullmatch(r"-?\d+(?:\.\d+)?", s):            # NEW: numeric strings == numbers
            f = float(s)
            return int(f) if f.is_integer() else f
        return s if nlu.ID_RE.fullmatch(s) else s.casefold()
```

---

## 13. P11 — Reproduction hardening (protects the whole 60%)

The guide: *"If your script does not reproduce, we contact you once; if it still does not run,
this portion scores zero."* The 15 unanswered rooms in your own runs were **worker deaths** —
on the organisers' machine a single worker dying mid-run = real failures. Actions:

1. **Clean-machine test (mandatory):** fresh VM/container, `git clone`, fill only
   `livekit_agent/.env.local`, run `./run_fdb_v3.sh --limit 5 --require-judge`. Fix whatever
   breaks. Then the full run. Do this AFTER P0/P1 land (new env keys must be documented in
   `livekit_agent/.env.example` and the README key table).
2. **Verify the model-weight prefetch works offline-first:** stage 1 runs
   `python -m livekit.agents download-files`; if it fails the script degrades to VAD-only
   endpointing (`TRIAGELINE_TURN_DETECTOR=0`) — that silently costs the pause/self-correction
   scenarios. Add a hard warning in the summary when that fallback fired (grep
   `run_config.json` for `"turn_detector": "0"` after every run).
3. **Worker resilience:** you already run 2 workers + watchdog locally. Document in README that
   the organisers' run uses the same script, and keep the `--limit 5` smoke gate.
4. **`submission.yaml` / README numbers:** update to the final live run *with the judge on*
   before submitting, and keep the exact-match numbers labelled as lower bounds (you already
   do this well — keep it).

---

## 14. P12 — Quality multiplier (×0.8–1.2) and practice-kit safety points

Cheap wording wins, all in `agent/nlu.py` templates and `agent/agent.py`:

1. **Never repeat a canned filler verbatim** — the practice scorer deducts 0.15/repeat
   (`harness/scorer.py: REPEAT_FILLER_DEDUCTION`) and the judge reads it as unnatural.
   `say()` already dedups within a turn in live mode; extend the ack templates with 2–3
   paraphrase variants rotated by `self.seq % len(variants)`.
2. **Restate the grounded result** in every final response (you do this via
   `humanize_result` — keep it; it's what the judge's "grounding" rubric rewards).
3. **No premature "done"** — already guarded (`done_phrase` only after success; the
   replacement/cancel flows say "I've asked the system to stop…"). Keep this property when
   editing any new phrase.
4. **Filler budget:** practice scorer default is 4/scenario; your live path caps at 2/turn.
   Leave it.

---

## 15. Wrong-logic / better-algorithm summary (for the record)

| Current logic | Why it loses | Better algorithm |
|---|---|---|
| Pure-regex NLU on noisy STT (planner off) | 58.8% arg acc live vs 93.7% offline | Hybrid: rules first, schema-validated LLM fallback (P0) — already built |
| Fragment commit by regex "looks unfinished" only | 25 s latency tail; garbage commits | Semantic turn detector (already wired) + bounded gate (done) + Deepgram endpointing 300 ms (P1) |
| Barge-in = always cancel-all on "switch" | kills chained calls (P3) | Dependency check first: anaphoric action on an in-flight read = queue, don't cancel |
| Revision = overwrite-only | late args dropped (P2) | Revision = overwrite **or add missing optional** |
| Tool threshold 1.5/2.0/2.5 scattered | garbage fragments open new intents (P7) | one threshold (2.5) + head-noun/verb evidence |
| `op_key` raw JSON of args | "1500"≠1500 → duplicate commits | numeric canonicalisation (P10) |
| `_is_arg_continuation` 8 s window | slow tools → late fragment becomes a 2nd request | 15 s + bind to last read result |
| City extraction travel-cue only | housing queries without "flight/trip" lose the city | add `apartment/rent/lease/flat` cues to `_LOW_PLACE` and `_travel_context` (1-line, do with P2) |

---

## 16. Exact execution plan for the follow-up AI/human

```bash
# 0. Baseline: record current numbers
python livekit_agent/replay_live_failures.py --json /tmp/before.json
./run_fdb_v3.sh --offline-text            # expect 92/100 — the "don't break this" gate

# 1. Apply P0 (planner), P1 (Deepgram flags + settle retune) — commit, then:
python livekit_agent/replay_live_failures.py --json /tmp/after_p01.json   # diff vs before

# 2. Apply P2–P8 one at a time. After EACH: replay harness + offline text gate + targeted unit test.
#    Order matters: P2 and P3 touch the same function (on_interruption/revise) — apply P3 first.

# 3. Apply P9/P10, re-run audio replay diagnostic, check latency + duplicate counts.

# 4. Full live validation (keys required):
./run_fdb_v3.sh --limit 5 --require-judge   # smoke
./run_fdb_v3.sh --require-judge             # full scored run
#    Success criteria: strict pass > 42, arg acc > 58.8%, no room left unanswered,
#    run_config.json shows turn_detector=1 and the planner banner shows the failover chain.

# 5. Update results/results.md, README table, submission.yaml with the judged numbers.
# 6. Record the demo video on the FINAL build (interruption on benchmark + Triage Line flow).
```

**Regression gates that must hold after every step** (these are your integrity story):
`./run_fdb_v3.sh --offline-text` ≥ 92/100 · `python scripts/integrity_audit.py --strict-comments` → PASS ·
held-out paraphrase set ≥ 29/30 · `pytest tests/ -q` (env-only failures on `livekit` import are OK).
