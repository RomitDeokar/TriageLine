# Triage Line — Requirements Document

## 1. Purpose

Triage Line is a full-duplex, interruptible voice dispatch agent for
roadside-assistance / incident triage calls. It exists to demonstrate two
things simultaneously, for the "Interruptible Real-Time Agents / Deliberative
Reasoning" hackathon theme:

1. Real full-duplex conversational behavior (listen-while-speak, instant
   barge-in, intelligent resume).
2. Visible, auditable deliberation before any consequential action is taken.

## 2. Scope

### 2.1 In scope (must ship)
- Voice conversation pipeline (STT → dialogue manager → TTS) with real
  barge-in and backchanneling.
- Deliberation engine that produces structured, persisted reasoning records
  before every dispatch decision, with self-critique and re-deliberation on
  new/contradicting input.
- Confirmation-safe commit state machine for dispatch actions.
- Naive vs deliberative agent, both runnable against a shared offline replay
  harness for head-to-head evaluation.
- Live operator/judge-facing UI showing conversation, deliberation state, and
  dispatch state machine in real time.
- Reporting script producing a pass/fail comparison table.
- Fully offline-runnable mode (mocked STT/TTS/provider) so the whole system
  is gradeable/demoable without live API keys; a real-provider adapter layer
  that can be filled in with credentials.

### 2.2 Out of scope (v1)
- Real emergency-services integration (911/local equivalent) — simulated only.
- Multi-language support.
- Authentication/user accounts — single-operator demo context.
- Billing, payments, or real dispatch-unit GPS tracking (mocked location data
  only).
- Mobile apps — UI is a responsive web page only.

## 3. Users

- **Caller** — the person on the phone reporting an incident (voice only).
- **Operator/Judge** — watches the live dashboard during the call, may also
  review past deliberation logs after the call.
- **Developer** — extends the system after the hackathon (new intents, real
  provider wiring, better UI).

## 4. Functional requirements

### FR-1 Conversation handling
- FR-1.1 System SHALL stream caller audio to STT and produce both interim
  and final transcripts.
- FR-1.2 System SHALL detect caller speech onset while agent TTS is playing
  and stop playback within a configurable target latency (default 150ms).
- FR-1.3 System SHALL support emitting short backchannel utterances during
  caller speech without ending the agent's conversational turn.
- FR-1.4 On interruption, system SHALL decide (via the deliberation engine,
  not a hardcoded rule) whether to: resume the prior utterance, ask a
  targeted follow-up, or fully re-plan based on new input.

### FR-2 Deliberation engine
- FR-2.1 Before any action tagged `consequential` (e.g. dispatch, escalate,
  close-case), system SHALL produce a deliberation record containing: known
  facts, open uncertainties, options considered, chosen option, rationale,
  confidence score (0–1).
- FR-2.2 System SHALL run a self-critique pass validating the chosen option
  against explicit constraints (service radius, severity thresholds,
  required-field completeness) before the action executes.
- FR-2.3 If self-critique fails, system SHALL either re-deliberate
  automatically (if more info can be requested from the caller) or downgrade
  to a safe default action (e.g. escalate-to-human) — never fail silently.
- FR-2.4 On contradicting caller input after a prior deliberation, system
  SHALL re-run deliberation for the affected decision and record the
  supersession explicitly (old record retained, not overwritten).
- FR-2.5 All deliberation records SHALL be persisted (append-only) and
  queryable by call ID.

### FR-3 Dispatch commit mechanism
- FR-3.1 A dispatch action SHALL pass through explicit named states (see
  Architecture doc) from proposal to finalized/aborted; no action may
  transition directly from "proposed" to "finalized."
- FR-3.2 Finalization SHALL require confirmed evidence the caller received
  the critical confirmation (definition of "confirmed evidence" is an
  implementation decision, documented in `docs/COMMIT_MECHANISM.md`).
- FR-3.3 An interruption or disconnect before finalization SHALL abort the
  pending action, with the reason recorded.
- FR-3.4 The system SHALL never leave an action in a non-terminal state after
  a call ends — session teardown SHALL force-resolve any pending action.

### FR-4 Naive comparison agent
- FR-4.1 System SHALL provide a second agent variant that performs the same
  dialogue/tool-calling but commits immediately on LLM decision, with no
  confirmation-safety or deliberation gating.
- FR-4.2 Both agents SHALL share the same tool/database layer so results are
  directly comparable.

### FR-5 Replay/evaluation harness
- FR-5.1 System SHALL support replaying scripted call scenarios (including
  interruption timing, self-corrections, disconnects) against both agents
  without live audio.
- FR-5.2 For each trial, system SHALL record: outcome state, whether it
  matches an independently computed ground truth, and decision latency.
- FR-5.3 System SHALL run at least 100 trials per agent per evaluation pass.

### FR-6 Reporting
- FR-6.1 System SHALL generate a summary table: trial counts, mismatch
  counts, false-dispatch count, missed-emergency count, latency percentiles,
  for both agents side by side.
- FR-6.2 Output SHALL be available as both terminal output and a
  copy-pasteable markdown table.

### FR-7 Live UI
- FR-7.1 UI SHALL show live conversation transcript (both partial and final).
- FR-7.2 UI SHALL show the current deliberation record as it's produced,
  including confidence and rationale, updating in real time.
- FR-7.3 UI SHALL show the dispatch state machine's current state and
  transition history for the active call.
- FR-7.4 UI SHALL show a running latency indicator (barge-in response time,
  time-to-decision).
- FR-7.5 UI SHALL be usable in a browser with no build step beyond `npm run
  dev` (or equivalent), and readable at a glance from a few meters away
  during a stage demo.

See `docs/UI_SPEC.md` for detailed screen/component requirements.

## 5. Non-functional requirements

- NFR-1 **Runnability**: full offline-mock mode must run with zero external
  API keys — `pip install -r requirements.txt && python run_demo.py` (or
  documented equivalent) must work unmodified on a clean machine.
- NFR-2 **Extensibility**: adding a new intent (e.g. "medical emergency" vs
  "flat tire") must require touching one config/registry point, not scattered
  edits across the codebase (see Architecture doc, §Extension points).
- NFR-3 **Testability**: deliberation engine and commit state machine must be
  unit-testable without any audio/LLM provider — pure state-transition tests.
- NFR-4 **Latency**: barge-in stop latency target ≤150ms in live mode;
  documented and measured, not just asserted.
- NFR-5 **Auditability**: every dispatch decision must be reconstructable
  after the fact purely from persisted logs (deliberation records + state
  transition log), without needing the original audio.
- NFR-6 **Honesty**: any mocked/simplified subsystem must be clearly labeled
  in code and in `docs/NOT_IMPLEMENTED.md`, not silently passed off as real.

## 6. Success criteria (demo-day)

- A live (or backup-recorded) call showing: normal booking → interrupted
  booking with correct abort → self-correction mid-call handled visibly →
  live UI tracking all of it.
- A reporting table showing the deliberative agent outperforming the naive
  agent on mismatch/false-dispatch/missed-emergency counts across ≥100
  trials.
- A judge can point at the UI mid-call and ask "why did it just do that?" and
  the answer is on screen, not just in your head.

## 7. Glossary

- **Consequential action** — any action with a real-world side effect (unit
  dispatch, emergency escalation, case closure) as opposed to purely
  conversational turns.
- **Deliberation record** — the structured artifact produced by FR-2.1.
- **Ground truth** — the independently computed "correct" outcome for a
  replay trial, computed without using the agent's own decision logic (so
  evaluation isn't circular).
