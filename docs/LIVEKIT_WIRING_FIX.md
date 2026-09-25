# LiveKit Wiring Fix (P0 #1/#2 from LIVEKIT_AGENT_TECHNICAL_AUDIT.md)

Scope: connect the real, LiveKit-launched agent to the existing, already-tested
interruption/epoch/cancellation/dedup engine. No new agent, no redesign of
`ParticipantAgent`/`TriageAdapter`/the legacy Triage Line brain.

## 1. Before

```
LiveKit AgentSession
      |
cascaded_agent.py
      |
AssistantFnc (LLM function-tools)
      |
mock_apis.py  (registry.call(...) — direct)
```
`adapter.py`'s `TriageAdapter` / `ParticipantAgent` / epoch / dedup logic
existed, was tested, but had **zero callers** connecting it to a live LiveKit
session (`attach_livekit_session()` was dead code).

## 2. After

```
LiveKit AgentSession (VAD + Whisper STT + TTS)
      |  session.on("user_input_transcribed")  [attach_livekit_session()]
      v
TriageAdapter (adapter.py, UNMODIFIED)
      |
ParticipantAgent (agent/agent.py, UNMODIFIED)
      |  epoch/version -> stale-work cancellation -> revise/retract ->
      |  updated args -> op-ledger dedup
      v
tool_executor() in cascaded_agent.py
      |
mock_apis.py registry.call(...)   [via asyncio.to_thread]
      |
TriageAdapter.on_tool_completed() -> ParticipantAgent -> speak() -> session.say()
```

The gpt-4o LLM tool-calling step is **removed** from `cascaded_agent.py`, not
just left unused. Keeping it running in parallel would have re-created the
exact bypass the audit flagged (Step 7): a second, uncoordinated way to
invoke state-changing tools with no epoch tracking. Voice I/O (VAD, Whisper
STT, TTS) is unchanged and still real LiveKit.

## 3. Files changed

- `livekit_agent/cascaded_agent.py` — rewritten (same file, not a new agent). Removed `AssistantFnc`/LLM tool-calling; added `tool_executor`/`tool_canceller`/`speak` callbacks that call `TriageAdapter`; calls `attach_livekit_session()`.
- `livekit_agent/adapter_tests/test_5_cascaded_agent_wiring.py` — new test (see §6).
- `docs/LIVEKIT_WIRING_FIX.md` — this file.

**Not touched:** `adapter.py`, `agent/agent.py`, `agent/nlu.py`, `fdb_tools.py`, `mock_apis.py`, `triage_livekit_agent.py`, `triage_brain.py`, `legacy/*`, `run_fdb_v3.sh`.

## 4. Exact execution path (traced, not assumed)

1. `attach_livekit_session(session, adapter, ...)` registers `session.on("user_input_transcribed")`.
2. Final transcript -> `adapter.on_user_final(text)` -> busy? `on_barge_in` (interruption) : new turn.
3. `ParticipantAgent.on_interruption()`/`.revise()` bumps `self.version`, calls `cancel_where()` for stale calls.
4. New/continuing call -> `out_q` `"tool_call"` -> `TriageAdapter._pump_outputs()` -> `cascaded_agent.tool_executor(call_id, api, args)`.
5. `tool_executor` runs `registry.call(...)` via `asyncio.to_thread`, then `adapter.on_tool_completed(...)` — unless `call_id` was cancelled in the meantime, in which case the result is dropped (belt-and-suspenders on top of `ParticipantAgent`'s own stale-`call_id` drop).
6. `filler_speech`/`final_response` -> `speak()` -> `session.say(text)`.

## 5. Tests run (all executed locally in this session)

| Test | Result |
|---|---|
| `adapter_tests/test_1_interrupt_while_reasoning.py` | PASS (unchanged) |
| `adapter_tests/test_2_interrupt_while_tool_running.py` | PASS (unchanged) |
| `adapter_tests/test_3_duplicate_state_change.py` | PASS (unchanged) |
| `adapter_tests/test_4_triage_line_interruption.py` | PASS (unchanged) |
| `adapter_tests/test_5_cascaded_agent_wiring.py` (**new**) | PASS |
| `fdb_scenario_run.py` | **7/9 PASS** — same 2 pre-existing failures (`search_apartments` not issued; chained step-1 not tracked), unhidden, unmodified |

No existing test was removed, weakened, or had its assertions loosened.

## 6. What the new test (`test_5`) actually proves — and its limits

It installs a **fake `livekit` package** (so `cascaded_agent.py` can be
imported without the real dependency, which is unavailable in this sandbox)
and then:
- calls `cascaded_agent`'s real, registered `entrypoint()` function directly (the same function `@server.rtc_session()` would hand to a live LiveKit worker),
- confirms `cascaded_agent.attach_livekit_session` and `cascaded_agent.TriageAdapter` are literally the same objects as `adapter.py`'s (not copies/reimplementations),
- fires a fake `"user_input_transcribed"` event and confirms it reaches `ParticipantAgent` and issues a real `search_flights` tool call,
- fires a contradicting transcript and confirms the epoch bumps, the first call is cancelled, and a new call carries the corrected argument (`Miami`).

**What this does NOT prove:** that the real `livekit-agents` package's actual event names/payloads match the fake ones used here, that OpenAI STT/TTS work, or that a real LiveKit Cloud room join succeeds. Those require network + credentials unavailable in this sandbox.

## 7. CODE VERIFIED LOCALLY vs. REQUIRES LIVEKIT/CREDENTIALS

**CODE VERIFIED LOCALLY:**
- `cascaded_agent.py` compiles (`python3 -m py_compile`).
- `attach_livekit_session()` is now called from `cascaded_agent.entrypoint()` (confirmed by test 5 exercising it through that exact function).
- No `registry.call(` invocation remains reachable from `cascaded_agent.py` except through `tool_executor`, which is only reachable via `TriageAdapter`/`ParticipantAgent` (confirmed by `grep`).
- All interruption/epoch/cancellation/dedup behavior, now proven reachable from the LiveKit-shaped entrypoint, not just the adapter in isolation.

**REQUIRES LIVEKIT/NETWORK/CREDENTIALS (still unverified):**
- That `cascaded_agent.py` actually imports against the real `livekit-agents` package (SETUP.md's own claim of "1.8.3, imports cleanly" was not re-verified here — still no `livekit` package installed in this sandbox).
- That the real `session.on("user_input_transcribed")` event's payload shape matches what `attach_livekit_session()` expects.
- A live LiveKit Cloud room join, real Whisper STT, and real TTS output.
- The official FDB-v3 benchmark run (explicitly out of scope this session, per instructions — not attempted).

## 8. Known remaining gaps (not fixed here, out of scope)

- The 2 pre-existing `fdb_scenario_run.py` failures (`search_apartments`, chained step-1) — these are in `agent/nlu.py`'s slot-filling, not in the wiring fixed this session, and were explicitly not to be touched.
- Rapid multi-interruption behavior still has no dedicated test.
