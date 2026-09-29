# legacy/ — the Triage Line brain (extension use case)

This package is the **dialogue → deliberation → commit** engine behind the Triage Line extension
(roadside / incident triage). It is not a second submission: it is imported by
`livekit_agent/triage_brain.py`, which `livekit_agent/triage_livekit_agent.py` runs inside the same LiveKit
agent shell as the FDB-v3 worker.

- `core/` — call state machine, deliberation (hazard / injury / location checks), two-phase commit of
  simulated actions (tow dispatch, emergency escalation) with explicit confirmation, and
  `force_resolve_pending` on disconnect so no action is ever left non-terminal.
- `providers/`, `persistence/`, `harness/`, `reporting/`, `api/` — supporting modules from the original
  stand-alone prototype; only what `triage_brain.py` imports is used at run time.

All actions are **simulated** (in-memory dispatch log). Tests:
`python livekit_agent/adapter_tests/test_4_triage_line_interruption.py` and
`python livekit_agent/adapter_tests/test_8_triage_confirmation_safety.py`.
Historical notes from the stand-alone prototype: `legacy/docs/` (superseded; see `docs/archive/README.md`).
