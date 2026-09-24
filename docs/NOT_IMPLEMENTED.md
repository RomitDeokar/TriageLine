# Not Implemented Yet

This project is currently at **Phase 5 — Commit State Machine + Persistence,
complete**. Phases 1–5 are implemented and tested (see `tests/`). The
following pieces of the spec have not been implemented yet and exist only
as empty modules or placeholder docstrings, per NFR-6 (honesty about
mocked/simplified subsystems):

- `core/models.py` (Case, Action, CallSession) — still a placeholder.
  `CommitRecord` (Phase 5) and `DeliberationRecord` (Phase 4) currently
  cover the structured-data needs of the commit/deliberation layers; a
  unifying `Case`/`CallSession` model would matter once a replay harness
  or reporting layer (below) needs to group everything for one call.
- Replay/evaluation harness — no scripted-scenario runner yet. Phase 5's
  persistence tests create and drive `DeliberationEngine`/
  `CommitStateMachine` directly rather than through a harness that feeds
  scripted call scenarios.
- Naive vs deliberative comparison strategy and reporting tables.
  `core/commit/strategies.py` intentionally ships only the one
  deliberative `CommitStrategy` (see that file's docstring) — a "naive"
  strategy that skips confirmation-gating is explicitly future work per
  the Phase 5 continuation spec, not an oversight.
- Live provider adapters (LiveKit, Deepgram, Groq, Rime) — only the
  deterministic mock providers from Phase 2 exist; `providers/live/` is
  still empty.
- FastAPI/WebSocket backend and the React UI described in `UI_SPEC.md` —
  the event bus (`core/event_bus.py`) is in-process only; nothing relays
  its events over a network yet, and nothing yet reads `persistence/` for
  the Deliberation Audit View (`UI_SPEC.md` section 1.3).
- Session teardown is not wired into the dialogue engine yet.
  `CommitStateMachine.force_resolve_pending()` exists and is tested (FR-3.4:
  never leave an action non-terminal after a call ends), but nothing calls
  it automatically when a call actually disconnects — that requires the
  live/transport layer, which doesn't exist yet.

## Known simplifications inside what *is* implemented (Phase 1–5)

- **Fact extraction is heuristic, not NLU** (`core/deliberation/intents.py`)
  — location/distance/vehicle/severity detection is keyword/regex based.
  Deterministic and sufficient for offline testing against the mock LLM's
  scripted phrasing; would need real structured extraction against a live
  LLM for unscripted callers.
- **Interruption resolution uses a synchronous snapshot**
  (`DeliberationBackedInterruptionStrategy`), not the full persisted
  deliberation pipeline — see the Phase 4 note this file previously
  carried; still true, unchanged by Phase 5.
- **Decision-group supersession, not full multi-thread decision tracking**
  (`core/deliberation/engine.py`) — sufficient for the required
  contradiction/self-correction scenarios; a call with several independent
  concurrent consequential actions in flight would need more structure.
- **Service radius is a flat numeric threshold, not real geocoding**
  (`core/deliberation/constraints.py`) — `distance_miles` is whatever the
  caller's text happens to state, compared against a configured flat
  radius.
- **Deliberation persistence writes happen inline in `DeliberationEngine`,
  not via an event-bus subscriber.** This was a deliberate, minimal Phase 5
  addition to Phase 4's engine (an optional `repository` constructor
  parameter, defaulting to `None` so every existing Phase 4 caller and
  test is unaffected) — see that file's docstring for why the
  `DeliberationResolved` event alone isn't sufficient (it's intentionally
  lightweight for the UI and omits rationale, structured constraint
  results, and supersession pointers).
- **SQLite is the only persistence backend, single-process, no connection
  pooling or migrations.** `persistence/db.py`'s `Database` wraps one
  `sqlite3` connection; adequate for the hackathon/offline-testing scope,
  not for concurrent multi-process access. `ARCHITECTURE.md` section 7
  already names this as the hackathon-scale choice, with Postgres as the
  documented later swap-in (would only require changing
  `persistence/db.py`).
- **A resave of an existing deliberation decision can only ever change its
  `superseded_by` pointer** — enforced at the SQL layer
  (`ON CONFLICT ... DO UPDATE SET superseded_by = ...`) in
  `persistence/repository.py`. This is intentional (see NFR-5
  auditability) but means there is currently no supported way to correct
  a persisted deliberation record's content after the fact other than
  superseding it with a new decision — which is the intended audit trail
  behavior, not a limitation to fix.

This document will be updated as later phases implement each remaining
piece.
