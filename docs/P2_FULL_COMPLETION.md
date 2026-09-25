# P2 — Wrong-tool fix, FDB-v3 offline replay, mobile API

## 1. State inherited
P1 was already merged to `main` (PR #5): adapter tests 6/6, local FDB scenarios 9/9.
Six further commits were on `genspark_ai_developer` but not merged: audit fixes R01–R22, the operation
ledger, and `fdb_v3_offline_replay.py`. They are now merged and pass every test.

## 2. "Wrong tool called": root cause and fix (`agent/nlu.py`)
**Root cause:** `score_tools` scored a tool by literal word overlap only. So in
"wireless headphones… *track* it under $100", the word "track" matched `track_order`, and the
product words matched nothing in `search_products`. Everyday synonyms missed their tools entirely:
package→order, perks→benefits, drive→commute, euros→exchange, and autopay/bill.

**Fix (general, not tied to any scenario):**
- A concept lexicon applied to both the utterance and the tool vocabulary. Name concepts weigh 1.0 and
  description concepts weigh 0.5.
- Concept evidence is counted only from words that did not already match literally, so nothing is
  counted twice.
- A head-noun bonus: the user naming the tool's object noun (cart, filter, autopay…) outranks a
  modifier match.
- Schema-role argument filling: currency pairs, from/to addresses, document type/number,
  bill type/account, filter name/value, and generic `<x>_type` fields.
- Spoken, spelled-out IDs: "A-B-C-1-2-3" → ABC123, "K-2" → K2. Each ID is bound to its own noun.
- `card_type` now matches the official `lk_agent_tool.py`, which uses a free string, not an enum.

## 3. Multi-request turns (`agent/agent.py`, additive; no redesign)
- `split_compound()` splits a turn into actionable clauses. It handles retractions ("skip that") and
  merges self-corrections. A search followed by "book it" stays as one planner chain.
- The clauses run one after another through the same `on_turn → start_task → call` path.
  A new user interruption (version bump) drops any clauses still waiting.
- `bind_from_results()` resolves "add it to my cart" and "from whatever you find" against the most
  recent valid (non-stale) tool result.

## 4. Mobile / native integration (`ui/server.py`)
- CORS headers on `/api/*`, plus `OPTIONS` preflight. The allowed origin can be set with `CORS_ORIGIN`.
- `GET /api/health`.
- REST and SSE contract: `POST /api/live/start`, `POST /api/live/<sid>/say|audio|frame|log|end`,
  `GET /api/live/<sid>/stream`. Checked with curl: a correction cancelled c1 and ran a revised c2.

## 5. Results (all verified in this sandbox)
| Check | Before | After |
|---|---|---|
| adapter tests 1–6 | 6/6 | 6/6 |
| `fdb_scenario_run.py` | 9/9 | 9/9 |
| pytest (`tests/`) | 56 | 81 |
| `fdb_compat_check.py` (12 tools) | 7/12 | 12/12 |
| Routing probe, 24 realistic phrasings | 19/24 | 24/24 |
| Official FDB-v3 data, `--text` mode, exact tool-name multiset | 37/100 | **57/100** |

`--text` mode feeds the official human transcripts through TriageAdapter → ParticipantAgent → the
official `mock_apis`. The ASR mode (faster-whisper) is CPU-slow and **was not completed** in this session.
**These are not official FDB-v3 scores.** Official scoring means running `evaluate_tool_calls.py`,
and it needs an OpenAI key for the LLM judge. The data is gitignored.

## 6. Remaining limitations
- 43/100 official examples still miss. Main causes: heavy disfluency, a single turn with several
  corrections, conditional chains ("only if commute > 15 min"), and filter values the rule-based
  parser cannot infer ("allow pets" → `pets_allowed=True`). An experimental disfluency cleaner
  scored 57→52, so it was removed.
- Real LiveKit Cloud credentials and a network path to them are still missing, so there is no
  live-room run.
