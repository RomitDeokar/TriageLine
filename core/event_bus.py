"""In-process async pub/sub event bus.

Decouples event producers (dialogue/deliberation/commit layers) from
consumers (a future WebSocket relay, the replay harness, etc). No
FastAPI, WebSocket, or database dependency belongs here — those are
later phases' job to build on top of this.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from core.events import Event

Subscriber = Callable[[Event], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    async def subscribe(self, handler: Subscriber) -> None:
        self._subscribers.append(handler)

    async def unsubscribe(self, handler: Subscriber) -> None:
        if handler in self._subscribers:
            self._subscribers.remove(handler)

    async def publish(self, event: Event) -> None:
        for handler in list(self._subscribers):
            await handler(event)
