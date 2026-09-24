# Triage Line — UI Specification

The UI is a passive, read-mostly dashboard. It has exactly one write action
(triggering a scripted demo call) and otherwise only renders the WebSocket
event stream defined in `core/events.py`. This keeps it safe to iterate on
without risking the live call pipeline.

## 1. Screens

### 1.1 Live Call View (primary demo screen)
The screen judges watch during a live or recorded call.

**Layout (single page, 3 columns on desktop, stacked on mobile):**

```
┌───────────────────────┬───────────────────────────┬─────────────────────┐
│  CONVERSATION           │   DELIBERATION              │  DISPATCH STATE       │
│                          │                             │                       │
│  [caller] "my car        │  Current decision:          │  ┌─────────┐         │
│   broke down on..."      │  Dispatch tow truck?        │  │ PROPOSED │         │
│   (interim, greyed)      │                             │  └────┬────┘         │
│                          │  Known:                     │       ▼               │
│  [agent] "Got it, what's  │  - location: Hwy 9 mile 12   │  ┌─────────┐         │
│   your location?"         │  - vehicle: sedan            │  │ PENDING  │  ●live │
│                          │                             │  │ CONFIRM  │         │
│  ── barge-in ──           │  Uncertain:                  │  └────┬────┘         │
│  [caller] "wait, it's      │  - severity (fire risk?)     │       ▼               │
│   on fire actually"        │                             │  ┌─────────┐         │
│                          │  Options considered:         │  │FINALIZED │ or      │
│  [agent] (backchannel:     │  1. standard tow (rejected:  │  │ ABORTED  │         │
│   "okay—")                 │     fire risk)                │  └─────────┘         │
│                          │  2. emergency escalation      │                       │
│                          │     (chosen)                   │  transition log:     │
│                          │                             │  10:42:01 PROPOSED     │
│                          │  Confidence: 0.62              │  10:42:03 PENDING    │
│                          │  ⚠ self-critique: re-run        │  (live-updating)      │
│                          │   triggered — new info          │                       │
└───────────────────────┴───────────────────────────┴─────────────────────┘
│  Barge-in latency: 118ms   Time-to-decision: 1.4s   Call: #A19F   ● connected │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Behavior requirements:**
- Interim transcripts render immediately, visually distinct (lower opacity)
  from finalized transcripts, and are replaced in place (no duplicate lines)
  when finalized.
- A barge-in event shows an inline divider (`── barge-in ──`) in the
  transcript at the exact point it occurred.
- Deliberation panel re-renders on every `DeliberationStarted` /
  `DeliberationResolved` / `SelfCritiqueFailed` / `ReDeliberationTriggered`
  event; a re-deliberation must visibly flag itself (not just silently
  replace prior content) — e.g. a one-line banner "re-deliberating: new
  info" that fades after a few seconds but leaves the updated record.
- Dispatch state machine renders as a small vertical stepper; the active
  state pulses/highlights; transitions animate, don't jump-cut, so judges
  can follow it live.
- Bottom status bar always visible: connection status, current call ID,
  running barge-in latency, running time-to-decision — these are your
  "prove it's real-time" numbers, keep them always on screen during a demo.

### 1.2 Comparison / Reporting View
Shown after a replay harness run, or standalone against saved results.

- A table: naive vs deliberative, columns = trials, mismatches,
  false-dispatches, missed-emergencies, p50/p95 latency.
- A pass/fail banner (green/red) driven by the same thresholds the
  `reporting/summarize.py` script uses — UI must not duplicate the
  pass/fail logic, only render what the backend computed.
- One expandable row per trial linking to its full deliberation record for
  spot-checking during Q&A.

### 1.3 Deliberation Audit View (secondary, for Q&A depth)
- Search/filter past deliberation records by call ID or intent type.
- Each record shown exactly as persisted: known facts, uncertainties,
  options, rationale, confidence, constraint check results, superseded-by
  links for re-deliberations.
- This screen exists specifically so that when a judge asks "why did it do
  that," you can pull up the literal record instead of explaining verbally.

## 2. Component inventory (for whichever framework you build in)

- `TranscriptStream` — renders interim + final turns, barge-in dividers.
- `DeliberationPanel` — known/uncertain/options/confidence/critique display.
- `StateStepper` — generic vertical/horizontal state-machine visualizer;
  reused for both the dispatch state machine here and can be reused later
  for any new state machine you add.
- `TransitionLog` — timestamped append-only list, auto-scrolling.
- `MetricsBar` — latency + connection status, subscribes to a lightweight
  metrics event, updates without re-rendering the whole page.
- `ComparisonTable` — naive vs deliberative summary, framework-agnostic
  enough to also render server-side to markdown for the pitch deck.

## 3. Event contract (UI's only dependency on the backend)

The UI subscribes to one WebSocket (`/ws/call/{call_id}` for a live call,
`/ws/replay/{run_id}` for harness runs) and receives JSON messages matching
the event types below (defined authoritatively in `core/events.py` —
this table must stay in sync with that file, not the other way around):

| Event type              | Key fields                                          | UI reaction                       |
|--------------------------|------------------------------------------------------|------------------------------------|
| `PartialTranscript`       | speaker, text, ts                                    | update interim line                |
| `FinalTranscript`         | speaker, text, ts                                    | finalize line                      |
| `BargeIn`                 | ts, position_ms                                      | insert divider, bump latency metric|
| `BackchannelSent`         | text, ts                                              | small inline agent bubble          |
| `DeliberationStarted`     | decision_id, intent                                  | show "deliberating..." state       |
| `DeliberationResolved`    | decision_id, known, uncertain, options, chosen, confidence | populate panel |
| `SelfCritiqueFailed`      | decision_id, failed_constraint, fallback_action        | show warning banner                |
| `ReDeliberationTriggered` | decision_id, supersedes_id, reason                     | show re-deliberating banner        |
| `ActionProposed`          | action_id, type                                       | stepper → PROPOSED                 |
| `ActionPending`           | action_id                                             | stepper → PENDING_CONFIRMATION     |
| `ActionFinalized`         | action_id, ts                                         | stepper → FINALIZED, log entry     |
| `ActionAborted`           | action_id, reason, ts                                 | stepper → ABORTED, log entry       |
| `MetricsTick`             | barge_in_latency_ms, time_to_decision_ms               | update MetricsBar                  |

No UI component may read from the database directly for live rendering —
history views (§1.3) use REST endpoints backed by the same repository, but
the live call view is WS-events-only.

## 4. Visual/UX guidance

- Optimize for legibility from a few meters away on a shared screen: large
  type for the state stepper and metrics bar, high-contrast state colors
  (grey=not started, amber=pending, green=finalized, red=aborted).
- Never let the deliberation panel go blank between decisions — show the
  last resolved record until a new one starts, so there's never a moment
  that looks broken.
- Keep animations short (~150–250ms) — this is a live demo, not a showcase
  of transitions; the goal is "clearly happened," not "impressive motion."

## 5. Build order for the UI (if time-boxed)

1. `TranscriptStream` + `StateStepper` + `MetricsBar` wired to real events —
   this alone already demonstrates full-duplex + commit-safety live.
2. `DeliberationPanel` — this is what demonstrates the theme's "deliberative
   reasoning" half; don't ship without it.
3. `ComparisonTable` / reporting view — can be a static markdown render if
   time runs out; it doesn't need to be live.
4. `Deliberation Audit View` — nice-to-have for Q&A depth, cut first if
   behind schedule.
