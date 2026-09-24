# Triage Line — Architecture

## 1. Design goals (in priority order)

1. **The reasoning core must not know about LiveKit, Deepgram, Groq, Rime, or
   any specific UI framework.** Swapping providers or rebuilding the UI must
   never touch `core/`.
2. **Everything the UI needs is an event, not a query.** The UI is a passive
   subscriber to a single event bus — this is what lets you improve the UI
   independently and keep the live demo view trivially in sync with backend
   changes.
3. **Adding a new capability (new intent, new action type, new constraint) is
   a registration, not a rewrite.** See §6 Extension points.
4. **Everything must be testable and runnable with zero external providers.**
   Mock adapters implement the exact same interfaces as real ones.

## 2. High-level architecture

```
                         ┌─────────────────────────────┐
                         │      TRANSPORT LAYER          │
                         │ (real or mocked I/O)          │
                         │                                │
                         │  LiveKitAudioIO   MockAudioIO  │
                         │  DeepgramSTT      MockSTT      │
                         │  GroqLLM          MockLLM      │
                         │  RimeTTS          MockTTS      │
                         └───────────────┬───────────────┘
                                         │ implements
                                         ▼
                         ┌─────────────────────────────┐
                         │   PROVIDER INTERFACES (I/O)   │
                         │ STTProvider / LLMProvider /   │
                         │ TTSProvider / AudioIO (ABCs)  │
                         └───────────────┬───────────────┘
                                         │ used by
                                         ▼
        ┌───────────────────────────────────────────────────────────┐
        │                     DIALOGUE ENGINE                         │
        │  turn management · interruption detection · backchannel    │
        │  emits: TurnStarted, PartialTranscript, FinalTranscript,    │
        │         BargeIn, BackchannelSent                            │
        └───────────────┬───────────────────────────────────────────┘
                        │ ConversationEvent stream
                        ▼
        ┌───────────────────────────────────────────────────────────┐
        │                   DELIBERATION ENGINE (core)                 │
        │  - IntentRegistry (pluggable intent handlers)                │
        │  - ConstraintRegistry (pluggable self-critique rules)         │
        │  - DeliberationRecord builder                                 │
        │  - Re-deliberation trigger on contradicting input              │
        │  emits: DeliberationStarted, DeliberationResolved,             │
        │         SelfCritiqueFailed, ReDeliberationTriggered            │
        └───────────────┬───────────────────────────────────────────┘
                        │ ActionProposal
                        ▼
        ┌───────────────────────────────────────────────────────────┐
        │                  COMMIT STATE MACHINE (core)                 │
        │   PROPOSED → PENDING_CONFIRMATION → FINALIZED                │
        │                        │                                     │
        │                        └──────────────→ ABORTED               │
        │  emits: ActionProposed, ActionPending, ActionFinalized,       │
        │         ActionAborted                                         │
        └───────────────┬───────────────────────────────────────────┘
                        │ commit / rollback
                        ▼
        ┌─────────────────────────────┐      ┌───────────────────────┐
        │        PERSISTENCE            │      │      EVENT BUS         │
        │  SQLite: cases, actions,      │◄────►│  in-process pub/sub    │
        │  deliberation_log (append)    │      │  + WebSocket fan-out   │
        └─────────────────────────────┘      └───────────┬───────────┘
                                                          │
                          ┌───────────────────────────────┼───────────────────────┐
                          ▼                               ▼                       ▼
                 ┌─────────────────┐           ┌─────────────────┐     ┌─────────────────┐
                 │   LIVE UI (web)   │           │  REPLAY HARNESS   │     │   REPORTING       │
                 │  subscribes to    │           │  feeds scripted   │     │  reads persisted  │
                 │  WS event stream  │           │  events into the  │     │  trial logs,      │
                 │                   │           │  same core, no    │     │  produces summary  │
                 │                   │           │  transport layer  │     │  tables            │
                 └─────────────────┘           └─────────────────┘     └─────────────────┘
```

## 3. Why this shape

- **Transport is a leaf, not a hub.** The dialogue engine only ever talks to
  provider *interfaces*. A `MockSTT`/`MockTTS`/`MockAudioIO` implementing the
  same ABCs means the entire core + UI can run and be demoed with zero
  external API keys — this is your guaranteed-runnable baseline for judging.
- **The event bus is the single seam the UI depends on.** The UI is not
  allowed to import from `core/` or `transport/` directly — it only consumes
  typed events over a WebSocket. This means you can rewrite the UI in
  anything (React, plain HTML, a terminal dashboard) without ever touching
  backend code, and multiple UIs (judge dashboard + post-call audit view) can
  subscribe to the same stream.
- **The replay harness is a first-class consumer of core, not a hack.** It
  drives the dialogue/deliberation/commit layers directly with scripted
  `ConversationEvent`s, bypassing transport entirely — this is what makes 100+
  trial runs fast (no real audio) and deterministic (no network jitter),
  while still exercising the *exact* decision code the live agent uses.
- **Two registries, not two agent codebases.** The "naive" and "deliberative"
  agents are the same core with the deliberation/commit layers configured
  differently (`STRATEGY=naive` skips self-critique and commits on
  `ActionProposed` directly). This guarantees the comparison is fair — same
  code path, one flag different — which is a stronger evidence story than two
  hand-written agents that quietly diverge.

## 4. Repository layout

```
triage-line/
├── core/                        # zero external deps beyond stdlib + pydantic
│   ├── events.py                 # all typed event dataclasses
│   ├── event_bus.py               # in-process pub/sub + WS fanout adapter
│   ├── dialogue/
│   │   ├── engine.py               # turn-taking, interruption, backchannel
│   │   └── turn_state.py
│   ├── deliberation/
│   │   ├── engine.py               # orchestrates a deliberation pass
│   │   ├── intents.py               # IntentRegistry + built-in intents
│   │   ├── constraints.py           # ConstraintRegistry (self-critique rules)
│   │   └── record.py                # DeliberationRecord model
│   ├── commit/
│   │   ├── state_machine.py         # ActionStateMachine (PROPOSED..FINALIZED)
│   │   └── strategies.py            # naive vs deliberative commit strategy
│   └── models.py                  # Case, Action, CallSession dataclasses
│
├── providers/                    # provider interfaces + implementations
│   ├── interfaces.py               # STTProvider, LLMProvider, TTSProvider, AudioIO (ABCs)
│   ├── mock/                       # fully offline fakes, deterministic
│   │   ├── mock_stt.py
│   │   ├── mock_llm.py
│   │   └── mock_tts.py
│   └── live/                       # real adapters, added when keys exist
│       ├── livekit_audio_io.py
│       ├── deepgram_stt.py
│       ├── groq_llm.py
│       └── rime_tts.py
│
├── persistence/
│   ├── db.py                       # SQLite schema + migrations
│   └── repository.py               # CaseRepository, ActionRepository
│
├── harness/
│   ├── scenarios/                   # YAML/JSON scripted call scenarios
│   ├── ground_truth.py               # independent oracle, not core-derived
│   └── replay.py                      # drives core with scripted events
│
├── reporting/
│   └── summarize.py                 # trial logs -> comparison table (md + terminal)
│
├── ui/                            # completely separate; only talks WS+HTTP
│   ├── server.py                    # FastAPI: WS event relay + REST for history
│   └── web/                         # React/Vite app — see UI_SPEC.md
│
├── config/
│   └── settings.py                  # env-driven, one Settings object
│
├── tests/
│   ├── test_dialogue_engine.py
│   ├── test_deliberation_engine.py
│   ├── test_commit_state_machine.py
│   └── test_replay_harness.py
│
├── docs/
│   ├── REQUIREMENTS.md
│   ├── ARCHITECTURE.md              # this file
│   ├── UI_SPEC.md
│   ├── COMMIT_MECHANISM.md
│   └── NOT_IMPLEMENTED.md
│
├── run_demo.py                    # one command: mock providers + UI + a scripted call
└── requirements.txt
```

## 5. Data flow for one call (happy path)

1. `AudioIO` streams caller audio → `STTProvider` → `PartialTranscript` /
   `FinalTranscript` events on the bus.
2. `DialogueEngine` consumes transcripts, manages turn state, calls
   `LLMProvider` for the next agent utterance, and detects barge-in via
   `AudioIO`'s playback-progress signal (works identically for mock and real).
3. When the LLM's response includes a consequential action, `DialogueEngine`
   hands an `ActionProposal` to the `DeliberationEngine`.
4. `DeliberationEngine` looks up the matching `IntentHandler`, runs it,
   builds a `DeliberationRecord`, runs all registered `ConstraintCheck`s
   (self-critique), and emits `DeliberationResolved`.
5. `DeliberationEngine` hands the resolved proposal to the
   `ActionStateMachine`, which moves it `PROPOSED → PENDING_CONFIRMATION`.
6. `TTSProvider` speaks the confirmation; `DialogueEngine` forwards
   playback-progress/interruption events to the `ActionStateMachine`.
7. On sufficient confirmed evidence (see `COMMIT_MECHANISM.md`), the state
   machine transitions to `FINALIZED` and persists via `ActionRepository`.
   Every transition emits an event → UI updates live, no polling.

## 6. Extension points (how this stays open to growth)

| Want to add...                          | Touch only...                                              |
|------------------------------------------|-------------------------------------------------------------|
| A new incident type (e.g. medical)        | Register a new `IntentHandler` in `core/deliberation/intents.py` — no changes to engine, state machine, or UI. |
| A new self-critique rule                  | Add a `ConstraintCheck` to `core/deliberation/constraints.py`'s registry. |
| A new real STT/LLM/TTS provider            | Implement the relevant ABC in `providers/live/`; nothing else changes. |
| A new UI (e.g. mobile, different framework)| Build against the WS event schema in `core/events.py`; core is untouched. |
| A new consequential-action type            | Add to `core/models.py` `ActionType` enum + one `IntentHandler`. |
| Multi-call / multi-operator support        | `CallSession` is already keyed by `call_id` end-to-end; add a session registry in `ui/server.py`. |

This table is the actual contract: if a change requires touching more than
the listed file(s), the architecture has been violated and should be fixed
before merging.

## 7. Tech stack recommendation

- **Backend/core**: Python 3.11+, `pydantic` for event/model schemas,
  `asyncio` throughout (matches `livekit-agents`' async model for a clean
  later swap-in).
- **Event transport to UI**: FastAPI + native WebSockets (no extra broker
  needed at hackathon scale; swap for Redis pub/sub only if you add
  multi-process scaling later — the event bus interface is designed to make
  that a one-file change).
- **Persistence**: SQLite for the hackathon (zero setup); `persistence/db.py`
  is the only file that would change for Postgres later.
- **UI**: React + Vite + Tailwind, talking only to `ui/server.py`'s
  WebSocket + REST endpoints. See `UI_SPEC.md`.
- **Real voice providers**: LiveKit Agents + Deepgram (STT) + Groq (LLM) +
  Rime (TTS), wired behind `providers/live/`, unchanged interfaces.

## 8. What "efficient" means here, concretely

- No component blocks on another synchronously across a network boundary
  where it doesn't have to — the event bus decouples UI rendering from the
  hot conversational path entirely.
- The replay harness never spins up transport, so 100+ trials run in
  seconds, not minutes — this is what makes iterating on the deliberation
  logic fast during the remaining hackathon time.
- Adding UI polish is zero-risk to demo stability, because the UI cannot
  crash the call pipeline — it's a passive subscriber with no write path
  into `core/`.
