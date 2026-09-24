"""WebSocket connection management + event fan-out.

This is the "WebSocket bridge" docs/UI_SPEC.md section 3 describes: a
thin layer that takes JSON-serializable event dicts (already produced by
core.events.Event.to_dict()) and forwards them to every currently
connected client. It knows nothing about deliberation, commit states, or
scenarios -- it only knows how to hold a set of sockets and broadcast to
them, and to do so without one dead connection breaking the others.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("triage_line.api.websocket")


class ConnectionManager:
    """Tracks connected WebSocket clients and fans events out to all of them.

    Connections are grouped by `channel` (e.g. a call_id or run_id) so a
    `/ws/call/{call_id}` and `/ws/replay/{run_id}` socket only receive the
    events for their own channel, matching the two endpoints UI_SPEC.md
    section 3 describes ("The UI subscribes to one WebSocket ... for a
    live call ... for harness runs"). A `None` channel is treated as a
    global/broadcast-to-everyone channel.
    """

    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, channel: str) -> None:
        await websocket.accept()
        self._connections.setdefault(channel, set()).add(websocket)

    def disconnect(self, websocket: WebSocket, channel: str) -> None:
        sockets = self._connections.get(channel)
        if sockets is None:
            return
        sockets.discard(websocket)
        if not sockets:
            self._connections.pop(channel, None)

    def connection_count(self, channel: str | None = None) -> int:
        if channel is not None:
            return len(self._connections.get(channel, ()))
        return sum(len(sockets) for sockets in self._connections.values())

    async def broadcast(self, channel: str, payload: dict[str, Any]) -> None:
        """Send `payload` as JSON to every client connected on `channel`.

        A client that has gone away mid-broadcast is dropped rather than
        allowed to raise and stop delivery to the rest -- one disconnected
        client must never break the event stream for everyone else.
        """

        sockets = list(self._connections.get(channel, ()))
        if not sockets:
            return

        message = json.dumps(payload)
        dead: list[WebSocket] = []
        for socket in sockets:
            try:
                await socket.send_text(message)
            except Exception:  # noqa: BLE001 - a broken client is data, not a crash
                logger.info("dropping dead websocket connection on channel %s", channel)
                dead.append(socket)

        for socket in dead:
            self.disconnect(socket, channel)
