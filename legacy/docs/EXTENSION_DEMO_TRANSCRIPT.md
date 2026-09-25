# Triage Line LiveKit Extension — Demo Transcript

Status: this is a **real, passing, offline run** of the actual production
code path (`livekit_agent/triage_brain.py` driving unmodified
`legacy/core` dialogue/deliberation/commit classes), executed via
`livekit_agent/adapter_tests/test_4_triage_line_interruption.py`, which
reuses legacy's own `MockSTT`/`MockTTS`/`MockAudioIO` in place of real
caller audio. It is **not** a run against a live LiveKit room — no
LiveKit/OpenAI credentials or network egress are available in this
sandbox (same limitation `livekit_agent/SETUP.md` already discloses for
the untouched FDB-v3 template). `livekit_agent/triage_livekit_agent.py`
is the real entrypoint, written against the same API cascaded_agent.py
uses, but unexercised against a live room here.

## Architecture exercised

```
LiveKit AgentSession (VAD/STT/TTS)
  -> TriageCallSession (livekit_agent/triage_brain.py)
       -> DialogueEngine            (legacy/core/dialogue/engine.py, unmodified)
       -> DeliberationEngine        (legacy/core/deliberation/engine.py, unmodified)
       -> CommitStateMachine        (legacy/core/commit/state_machine.py, unmodified)
       -> dispatch log              (TriageBrainLLM._log_dispatch — mocked, see below)
```

## Scenario: breakdown report, then a location correction mid-confirmation

**1. Caller:** "My car broke down near Highway 9."
Deliberation resolves `BreakdownIntent` → `dispatch_tow`, `location=Highway 9`,
all required fields present → `CommitStateMachine.propose()` →
`PENDING_CONFIRMATION`.
**Agent (spoken):** "I'll dispatch a tow truck to Highway 9. Should I go ahead?"

**2. Caller interrupts mid-prompt:** "Actually, I'm on Highway 12."
`DialogueEngine.check_for_barge_in()` detects caller speech during
`AGENT_SPEAKING`, stops TTS, transitions to `CALLER_SPEAKING`. The new
final transcript is appended to context and a full deliberation pass
re-runs over the whole transcript. `BreakdownIntent` re-extracts location —
now `Highway 12` — in the same `incident_response` decision group, so the
new `DeliberationRecord.supersedes` the first one. The extension layer
(`TriageBrainLLM._cancel_stale_actions`) then explicitly **aborts** the
stale Highway‑9 `CommitRecord` (`aborted_reason=superseded_by_redeliberation`)
and proposes a fresh Highway‑12 action, `PENDING_CONFIRMATION`.
**Agent (spoken):** "I'll dispatch a tow truck to Highway 12. Should I go ahead?"

**3. Confirmation safety check:** the Highway‑12 action sits in
`PENDING_CONFIRMATION`; the dispatch log is still empty. Nothing was
finalized by deliberation confidence or re-planning alone — only an
explicit `confirm()` reaches `FINALIZED` (enforced by
`CommitStateMachine`, unmodified).

**4. Caller confirms:** "Yes, go ahead." → `confirm()` → `FINALIZED` →
one dispatch logged, for **Highway 12 only**. Highway 9 was never
dispatched.

**5. Terminal-state check:** both actions this call produced
(Highway‑9 `ABORTED`, Highway‑12 `FINALIZED`) are terminal.

**6. Disconnect/teardown:** caller adds a further remark that starts a new,
never-answered proposal, then the call ends. `TriageCallSession.teardown()`
calls `CommitStateMachine.force_resolve_pending()` (pre-existing legacy
method, previously unwired to any transport) — the dangling action is
force-aborted (`caller_disconnected`). No action is left non-terminal.

Full assertions for all six steps: `livekit_agent/adapter_tests/test_4_triage_line_interruption.py`
(run: `python3 livekit_agent/adapter_tests/test_4_triage_line_interruption.py` → `PASS`).

## What is real

- Dialogue turn-taking, barge-in detection/stop, deliberation (intent
  matching, self-critique constraints), supersession, and the
  `PROPOSED → PENDING_CONFIRMATION → FINALIZED/ABORTED` state machine are
  the actual legacy Phase 1–5 code, unmodified except one bug fix (below).
- The correction → cancel-stale-action → re-propose → re-confirm flow is
  real control flow, not scripted/staged output.
- `force_resolve_pending` is now actually wired to call teardown, closing
  the gap `legacy/docs/NOT_IMPLEMENTED.md` names explicitly.

## Bug fix made to enable this (legacy/core/deliberation/intents.py)

`_extract_location_snippet` previously returned the **first** location
mention in the caller's full transcript. Since `_caller_text` re-joins the
*entire* caller transcript every pass (by design, so later corrections are
visible), the first-match behavior meant a correction could never actually
override an earlier statement — Highway 9 would have kept winning even
after "Actually, I'm on Highway 12." Changed to return the **last**
match. No other logic in `legacy/core` was changed.

## What is simplified / mocked (not real)

- **Dispatch itself**: `TriageBrainLLM._log_dispatch` only appends to an
  in-memory list. There is no real tow-dispatch, CAD, or emergency-services
  API integration anywhere in this repo.
- **Geocoding/distance/routing**: unchanged from legacy —
  `location` is whatever substring the caller's words happen to contain;
  `distance_miles` is regex-parsed from phrases like "mile 12", not
  computed from any map service (`ServiceRadiusConstraint` already
  documents this).
- **NLU/fact extraction**: unchanged from legacy — keyword/regex based
  (`core/deliberation/intents.py`), not a real language model.
- **STT/TTS/VAD in the live path**: `triage_livekit_agent.py` wires real
  `openai.STT`/`openai.TTS`/`silero.VAD` calls, but this has not been run
  against a live room in this sandbox (no credentials/network here) — see
  Status above.
- **TTS playback progress**: `LiveKitTTSAdapter.progress()` returns a
  0.0/1.0 placeholder, not true sub-utterance timing (`AgentSession`'s
  `SpeechHandle` doesn't expose that in a version-stable way); this only
  affects a metrics field, not correctness of barge-in stop/resume.
- **Live-path barge-in event name**: the exact livekit-agents event for
  mid-agent-speech VAD onset is version-sensitive (same caveat
  `livekit_agent/adapter.py`'s `attach_livekit_session` already documents);
  the fast path in `triage_livekit_agent.py` is best-effort and unverified
  against a real SDK build.
- **Persistence**: `TriageCallSession` can take a repository (SQLite,
  unmodified from legacy) but the demo test runs with `repository=None` —
  in-memory only, per-call.
