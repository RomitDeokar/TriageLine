"""Phase 7A: FastAPI + WebSocket backend.

This package is the *only* thing a UI is allowed to talk to (per
docs/ARCHITECTURE.md section 3: "the UI is not allowed to import from
core/ or transport/ directly -- it only consumes typed events over a
WebSocket"). Nothing in here contains dialogue/deliberation/commit
decision logic -- it is a relay in front of core.event_bus.EventBus and
a thin trigger for harness.replay's existing scenarios.

No frontend (React/Vite/Tailwind) lives here -- see docs/NOT_IMPLEMENTED.md
and BUILD_PROMPT.md's phase notes for what's still Phase 7B/7C/7D.
"""
