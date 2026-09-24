# Triage Line UI (Phase 7B-1)

React + Vite + Tailwind shell wired to the Phase 7A FastAPI/WebSocket
backend. See `../docs/UI_SPEC.md` for the full spec this is a first slice
of, and `../docs/ARCHITECTURE.md` §7 for the tech-stack rationale.

Scope of this phase only: a connection shell + a raw debug event stream.
No Conversation/Deliberation/Dispatch rendering yet — that's Phase 7B-2.

## Run it

Terminal 1 — backend:

```bash
cd ..                      # repo root (triage-line/)
pip install -r requirements.txt
uvicorn api.app:app --reload --port 8000
```

Terminal 2 — frontend:

```bash
cd ui
npm install
npm run dev
```

Open the printed Vite URL (typically http://localhost:5173). Pick
"Replay run", enter a scenario/run id (e.g. `demo-1`), click Connect, then
in a third terminal trigger a demo run against that same run id:

```bash
curl -X POST http://localhost:8000/demo/run/demo-1/<scenario_id>
```

(`GET /demo/scenarios` on the backend lists valid `<scenario_id>` values.)
Events from that run should appear in the "Debug event stream" panel
live as the scenario replays.

## Files

- `src/lib/config.js` — builds the `/ws/call/{id}` or `/ws/replay/{id}`
  URL; the only place that knows about `ws://`/`wss://` and same-origin
  proxying.
- `src/lib/socket.js` — minimal WebSocket wrapper (open/close/message),
  no React, no event-schema knowledge.
- `src/lib/useEventSocket.js` — React hook exposing connection `status`,
  `latestEvent`, and a capped `events` history.
- `src/components/` — `ConnectionBadge`, `Panel` (generic placeholder used
  for Conversation/Deliberation/Dispatch), `DebugEventStream`.
- `src/App.jsx` — layout shell + channel picker (call vs. replay).

## Known limitation

This was built and reviewed in a sandboxed environment with no network
access, so `npm install` / `npm run build` and the backend's `pip install`
have not actually been executed here — see the assistant's final report
for details and what to verify locally.
