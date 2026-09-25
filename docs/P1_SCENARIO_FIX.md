# P1 — Local Scenario Fixes and a Rapid-Interruption Test

Scope: make the two failing local FDB-v3-shaped scenarios pass and add a test for rapid successive interruptions.
The architecture is unchanged: LiveKit → `cascaded_agent.py` → `TriageAdapter` → `ParticipantAgent` → tool executor.
Not modified: `adapter.py`, `cascaded_agent.py`, the legacy Triage Line brain, `run_fdb_v3.sh`, and all existing tests and assertions.

## 1. search_apartments: root cause
Trace: `on_user_final` → `ParticipantAgent.on_turn` → `score_tools` picked `search_apartments` correctly → `build_args` → `extract_number("max_price")`.
The utterance "find a 3 bedroom apartment in Austin under 3000" contains two numbers. Neither number is next to a cue word from the field name (`max`, `price`), so the ambiguity guard returned `None`.
`max_price` was then reported as missing. The agent asked "what max price should I use?" and never issued the tool.
So the failure was in argument generation. Tool selection was correct.

## 2. search_apartments: fix (`agent/nlu.py`)
`extract_number` now recognises upper-bound or budget fields generically:
- field names starting with `max`, or containing price, budget, rent, cost, limit or amount
- these fields take the number introduced by a ceiling phrase: under, below, less than, up to, at most, budget of, within, and similar

The existing cue-word and ambiguity logic still runs for every other field. No tool name or scenario is hardcoded.

## 3. Chained call: root cause (this is the "wrong tool" LiveKit bug)
The planner's search→book chaining logic is written against the canonical family `{"flight_search", "book_flight"}`. That covers `FLIGHT_FAMILY`, the `plan=["book_flight"]` rewrite, `revise()` redo, and `on_flights()` grounding.
The FDB-v3 manifest names the search tool `search_flights`. So for "book a flight to Chicago for Ada Lovelace":
- `top == "book_flight"`, and the planner tried to rewrite it to `flight_search`.
- `flight_search` is not in the manifest, so it kept `book_flight`.
- It then called book_flight directly: the wrong tool, with no flight_id, and no search step.

So the failure was in tool selection (manifest naming mismatch). Issued-call tracking and timing were fine.

## 4. Chained call: fix (`agent/agent.py`)
- `ParticipantAgent.canonicalize_manifest()`: on `tool_manifest`, a tool whose name tokens match a canonical family member is keyed internally under the canonical name. Matching is order- and plural-insensitive: `{flight, search}` ⊆ tokens. It only applies when the canonical name is absent. `tool_alias` records internal→external.
- `call()` sends the manifest's own name (`search_flights`) in the `tool_call` action. The executor, the LiveKit tool and the logs therefore see the real FDB-v3 name.
- For read-only tools only, a missing top-level `date` string is defaulted to `"today"`, and the acknowledgement says so. The user can correct it by barge-in, which goes through the normal revise/epoch path. State-modifying tools are never defaulted.

## 5. Rapid-interruption test design (`livekit_agent/adapter_tests/test_6_rapid_interruptions.py`)
Sequence: A (Denver) → correction B (Miami) → correction C (Chicago), with each correction arriving while earlier tools are still running.
- The executor is a real slow async backend (`asyncio.sleep`), with latencies Denver 0.60 s, Miami 0.40 s, Chicago 0.15 s.
- Cancellation is deliberately best-effort, so the stale A and B results really arrive after C has started. A even arrives after C's result.

The test asserts:
- the epoch goes 0 < 1 < 2
- A and B are cancelled and out of `inflight`
- C is the only in-flight call and runs under the current epoch
- stale results produce no speech, no slot contamination and no follow-up call
- exactly one `book_flight`, using FL-CHICAGO, under the current epoch
- repeating the booking after success issues nothing (op ledger)
- three rapid identical bookings while one is pending issue only one call

## 6. Results (this sandbox)
- adapter tests test_1 to test_6: 6/6 PASS
- `fdb_scenario_run.py`: 9/9 PASS (previously 7/9)
- `tests/test_regressions.py`: 24/24 PASS
- Internal public suite (`run_local.py --all`): identical before and after the fix (56.6/100 in this sandbox; the audio/vision scenarios depend on unavailable model backends). No regression.
- `cascaded_agent.py`: unchanged. `attach_livekit_session()` is still called, and `registry.call` is reachable only inside the adapter-driven `tool_executor`.

## 7. Remaining limitations
- The real `livekit-agents` package, network access and credentials are unavailable. test_5 uses a stubbed session, so no live room smoke test has been done.
- These are hand-written, FDB-v3-shaped scenarios, not the official FDB-v3 benchmark. No official results are claimed.
- Canonicalization currently covers only the flight-search family. Other tools use the generic schema path.
- The "today" date default is a policy choice for read-only searches. It is announced to the user and can be corrected, but it is still an assumption.
