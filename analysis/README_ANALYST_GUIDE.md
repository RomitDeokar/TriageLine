# README — Analyst Guide (read before changing anything)

This file teaches the vocabulary and the mechanics so that **any** AI (including a weak one) can act
on `02_FIX_SPECS.md` without breaking the working parts. It contains **no fixes** — only context.

---

## 1. What this project is

`TriageLine` is a **voice-native agent** submitted to Samsung PRISM GenAI Hackathon 2026, Theme 05
(*Interruptible Real-Time Agents*). It has three jobs:

1. **Stay responsive** — speak within a few hundred ms, never dead air, never falsely claim "done".
2. **Work asynchronously** — tools/perception/reasoning run in the background, never blocking speech.
3. **Recover cleanly** — when the user changes their mind mid-utterance, drop stale intent, update
   arguments, and never repeat a state-changing action.

It is scored on **Full-Duplex-Bench v3 (FDB-v3)** through LiveKit, plus an extension use case
(*Triage Line*, roadside triage).

## 2. How the score is actually computed (two different scorers — don't mix them up)

There are **two** scoring systems in this repo. Keep them separate in your head.

### (A) The official FDB-v3 scorer — this is the 60% that matters
- Runs `v3/evaluate_tool_calls.py` and `v3/evaluate_pass_rate.py` from the cloned benchmark.
- Reports: **turn-take rate**, **tool-selection accuracy**, **argument accuracy**,
  **strict pass rate**, **response quality**, **latency**.
- **Tool-selection and argument accuracy are computed only over `turn_taken` samples.** If the agent
  never responds, the sample is counted as a miss in the `*_all` variant and is excluded from the
  headline metric. **This is why turn-take is the #1 lever.**
- With `--use-llm`, an LLM **judge** (gpt-4o) does **semantic** argument matching (e.g. "mechanical
  keyboard" ≈ "mechanical keyboards"), and scores response quality. The organisers always run with
  the judge on. Our archived live runs had the judge **off** (exact match = a harsh lower bound).
- **3 runs per scenario, median, then weighted average** (audio/visual ×1.5, L3/L4 ×1.25).

### (B) The internal practice scorer — `harness/scorer.py`
- Used by `run_local.py` against `scenarios/*.json` (the older Theme-05 kit). **Not** the scored
  metric. Weights: task 40 / recovery 35 / latency 15 / safety 10.
- Useful because it *shows* the interruption/cancellation/safety logic working. Keep it green, but
  do not optimise for it at the expense of (A).

### The key facts about the official scorer that drive every fix
| Fact | Consequence |
|---|---|
| Tool-sel / arg-acc counted over turn-taken only | A no-response room is a triple loss |
| `search_flights`'s expected call is often implicit | Extra `search_flights` calls are one of the top "extra tool" buckets |
| Judge = semantic match | Wording/plural differences are forgivable **under the judges' run**, but exact-match is our local proxy; fix the easy ones anyway |
| Extra calls reduce **precision** | One spurious call can fail the scenario even when the expected call is present |
| Latency is measured **audio→audio** by the official runner | Our internal `TRIAGELINE_SETTLE_S` gate adds directly to it |

## 3. The architecture (one diagram, so you know where things live)

```
caller audio ─▶ Silero VAD ─▶ STT (Deepgram nova-3 | Gemini) ─┐
                                                              ▼
        ┌──────────── TriageAdapter (livekit_agent/adapter.py) ────────────┐
        │ commit gate  · VAD/partial barge-in · every tool = own task      │
        └──────────────┬───────────────────────────────────▲──────────────┘
               events  ▼                                   │ tool_call / cancel_tool
        ┌──────────── ParticipantAgent (agent/agent.py, agent/nlu.py) ─────┐
        │ FAST  parsing · revise/retract/switch · acknowledgement          │
        │ SLOW  tools, chained clauses bound to earlier results            │
        │ COORD epoch version · in-flight ledger · operation ledger        │
        └──────────────┬───────────────────────────────────────────────────┘
                       ▼ speech           official FDB-v3 mock_apis.py (fresh per room)
              TTS (Deepgram aura-2 | Gemini) ─▶ agent audio
```

**File map (the only files that matter for scoring):**

| File | Role | Touch when |
|---|---|---|
| `agent/nlu.py` | rule-based parsing: intent ranking (`score_tools`), argument filling (`build_args`), repair handling | argument/intent bugs |
| `agent/agent.py` | the agent loop: turn handling, cancellation, ledgers, compound requests, book/revise | behaviour bugs |
| `livekit_agent/adapter.py` | turn-commit gate, barge-in, fragmented-final merging | turn-take / latency / duplicate-call bugs |
| `livekit_agent/cascaded_agent.py` | LiveKit wiring, backchannel, progress narrator | latency / responsiveness |
| `livekit_agent/speech_providers.py` | STT/TTS selection and options | transcription-quality bugs |
| `livekit_agent/fdb_tools.py` | the 12-tool manifest (FDB-v3) | schema-driven logic |
| `harness/scorer.py` | internal practice scorer (for regression only) | never (it's the grader's twin) |
| `run_fdb_v3.sh` | the one-command reproduction (the organisers run this) | reproducibility only |

## 4. Event model (what the agent receives / emits)

**Incoming events** (`harness/protocol.py` + adapter): `tool_manifest`, `user_speech_chunk`,
`user_audio_chunk`, `video_frame`, `interruption`, `tool_result`, `scenario_end`.

**Outgoing actions**: `filler_speech`, `tool_call`, `cancel_tool`, `clarification_request`,
`final_response`.

Rules that matter:
- `final_response` **must** carry a `state_snapshot` (protocol + safety deduction otherwise).
- Only "real speech" (≥3 chars, ≥50% alphabetic) stops the latency clock. `"..."` earns nothing.
- Spoken actions are `filler_speech`, `clarification_request`, `final_response`.

## 5. The "don't break these" invariants

These already work. A fix must not remove them.

1. **Epoch guard.** `ParticipantAgent.version` is bumped on every invalidation; slow completions
   carry the version they started under and are dropped if stale (`on_internal`, `still_valid`).
2. **Operation ledger.** Every state-modifying call has a lifecycle
   (`pending → committed/failed/cancelled/unknown`). No duplicate commit, no auto-retry of an
   unknown outcome.
3. **Non-blocking tool calls.** Each `tool_call` is its own `asyncio.Task` in the adapter — slow
   tools must never block speech.
4. **No-participation rule.** A silent agent scores 0, so never "fix" a problem by going quiet.
5. **Integrity.** Nothing keys off scenario ids/timestamps/expected strings. Keep it that way.

## 6. How to reproduce (and the golden rule)

```bash
# Fast, key-free diagnostics (seconds): the practice kit + the offline text replay
python run_local.py --all --agent agent.agent:ParticipantAgent
python3 livekit_agent/fdb_v3_offline_replay.py --data <fdb_v3_data_released> --text

# Full scored run (needs keys + a GPU box), this is what the organisers run:
PYTHON=python3.12 ./run_fdb_v3.sh --require-judge
```

**Golden rule:** a full live run is ~2 h. **Never** validate a fix with a live run. Use
`livekit_agent/replay_live_failures.py` (replays a live run's own transcripts + fragment timing
offline, seconds) and the offline text replay. Only run live once the offline gates are green.

## 7. The regression gates a fix must not break

| Gate | Command | Expected |
|---|---|---|
| Python tests | `.venv/bin/python -m pytest tests livekit_agent/adapter_tests -q` | all pass (8 env-only failures allowed) |
| Offline text replay | `python3 livekit_agent/fdb_v3_offline_replay.py --data <dir> --text` | ≥ current baseline (91–92/100) |
| Held-out paraphrases | `.venv/bin/python scripts/heldout_eval.py` | 29/30 |
| Integrity | `.venv/bin/python scripts/integrity_audit.py --strict-comments` | PASS |
| Local practice | `python run_local.py --all --agent agent.agent:ParticipantAgent` | ≥ 87/100 |

## 8. How to read the fix cards

Each card in `02_FIX_SPECS.md` is:

```
FC-xx  <title>
  Symptom        what the live run showed
  Root cause     file:line + the exact wrong logic
  Change         the exact edit (before → after)
  Why it's safe  which invariant it respects
  Test           the command that proves it
  Risk           what it could break + how to revert
```

Do them **in order**. FC-01…FC-05 are the high-value, low-risk ones. FC-10+ are refinements.
