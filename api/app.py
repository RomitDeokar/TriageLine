"""FastAPI application entry point for the Triage Line UI backend.

Phase 7A only: this exposes the WebSocket event bridge and a demo trigger
over the existing replay harness. It intentionally does not implement, or
know about, any UI framework -- see docs/UI_SPEC.md section 3, which this
app's shape follows directly:

- `GET  /health`                        -- liveness/readiness probe.
- `GET  /demo/scenarios`                -- lists the existing required
                                            scenario ids this backend can
                                            demo-run (see api/replay_bridge.py).
- `POST /demo/run/{run_id}/{scenario_id}` -- runs one existing scenario
                                            through one existing agent
                                            strategy, relaying its real
                                            event trace live to every
                                            client on `/ws/replay/{run_id}`.
- `WS   /ws/call/{call_id}`             -- live-call event channel (no
                                            producer exists for this yet in
                                            Phase 7A -- transport/dialogue
                                            wiring is a later phase -- but
                                            the channel is real and ready).
- `WS   /ws/replay/{run_id}`            -- harness/demo-run event channel.

Run with:  uvicorn api.app:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from api.replay_bridge import (
    UnknownAgentTypeError,
    UnknownScenarioError,
    available_demo_scenarios,
    run_demo_scenario,
)
from api.websocket import ConnectionManager

logger = logging.getLogger("triage_line.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("Triage Line API starting up (offline/mock mode)")
    yield
    logger.info("Triage Line API shutting down")


def create_app() -> FastAPI:
    """Application factory.

    A factory (rather than a bare module-level `app`) so tests can build a
    fresh app -- and therefore a fresh, isolated `ConnectionManager` with
    no state left over from a previous test -- instead of sharing one
    process-wide instance.
    """

    app = FastAPI(title="Triage Line API", version="7a", lifespan=lifespan)
    app.state.connections = ConnectionManager()

    @app.get("/health")
    async def health() -> dict:
        connections: ConnectionManager = app.state.connections
        return {"status": "ok", "live_connections": connections.connection_count()}

    @app.get("/demo/scenarios")
    async def list_demo_scenarios() -> dict:
        return {"scenarios": sorted(available_demo_scenarios())}

    @app.websocket("/ws/call/{call_id}")
    async def ws_call(websocket: WebSocket, call_id: str) -> None:
        await _serve_websocket(app, websocket, channel=f"call:{call_id}")

    @app.websocket("/ws/replay/{run_id}")
    async def ws_replay(websocket: WebSocket, run_id: str) -> None:
        await _serve_websocket(app, websocket, channel=f"replay:{run_id}")

    @app.post("/demo/run/{run_id}/{scenario_id}")
    async def trigger_demo(run_id: str, scenario_id: str, agent_type: str = "deliberative"):
        connections: ConnectionManager = app.state.connections
        channel = f"replay:{run_id}"
        try:
            result = await run_demo_scenario(connections, channel, scenario_id, agent_type)
        except UnknownScenarioError as exc:
            return JSONResponse(status_code=404, content={"error": str(exc)})
        except UnknownAgentTypeError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        return result.to_dict()

    return app


async def _serve_websocket(app: FastAPI, websocket: WebSocket, channel: str) -> None:
    """Accept, register, and hold a WebSocket open for a single channel.

    Per docs/UI_SPEC.md section 3 ("No UI component may read from the
    database directly for live rendering ... the live call view is
    WS-events-only"), this socket is a pure event sink: it has no write
    path into core. The only thing an incoming client message does is get
    discarded; waiting on `receive_text()` is simply how a clean
    disconnect is detected without polling.
    """

    connections: ConnectionManager = app.state.connections
    await connections.connect(websocket, channel)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        connections.disconnect(websocket, channel)


app = create_app()
