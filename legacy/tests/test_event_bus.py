"""Tests for the async in-process event bus (Phase 3)."""

import asyncio

from core.event_bus import EventBus
from core.events import PartialTranscript


def run(coro):
    return asyncio.run(coro)


def _event(text: str) -> PartialTranscript:
    return PartialTranscript(call_id="c1", timestamp_ms=0, speaker="caller", text=text)


def test_subscriber_receives_published_event():
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event)

    async def scenario():
        await bus.subscribe(handler)
        await bus.publish(_event("hello"))

    run(scenario())
    assert len(received) == 1
    assert received[0].text == "hello"


def test_multiple_subscribers_all_receive_event():
    bus = EventBus()
    received_a, received_b = [], []

    async def handler_a(event):
        received_a.append(event)

    async def handler_b(event):
        received_b.append(event)

    async def scenario():
        await bus.subscribe(handler_a)
        await bus.subscribe(handler_b)
        await bus.publish(_event("hi"))

    run(scenario())
    assert len(received_a) == 1
    assert len(received_b) == 1


def test_events_arrive_in_publish_order():
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event.text)

    async def scenario():
        await bus.subscribe(handler)
        await bus.publish(_event("first"))
        await bus.publish(_event("second"))
        await bus.publish(_event("third"))

    run(scenario())
    assert received == ["first", "second", "third"]


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event)

    async def scenario():
        await bus.subscribe(handler)
        await bus.publish(_event("one"))
        await bus.unsubscribe(handler)
        await bus.publish(_event("two"))

    run(scenario())
    assert len(received) == 1


def test_async_handler_can_await_inside():
    bus = EventBus()
    received = []

    async def handler(event):
        await asyncio.sleep(0)
        received.append(event)

    async def scenario():
        await bus.subscribe(handler)
        await bus.publish(_event("hi"))

    run(scenario())
    assert len(received) == 1
