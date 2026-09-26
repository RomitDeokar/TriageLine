"""Tests for the Phase 7A FastAPI + WebSocket backend (api/).

Requires `fastapi` (and, for TestClient, `httpx`) to be installed. If
either is missing, `tests/_run_all.py` reports this whole module as
SKIPPED -- via its ModuleNotFoundError handling -- rather than faking a
pass. To actually execute these: `pip install fastapi httpx`, then
`python3 tests/_run_all.py` or `pytest tests/test_api.py`.

The `ConnectionManager` tests below run against a duck-typed fake socket
and need no server at all; the `create_app`/`TestClient` tests exercise
the real FastAPI routes end-to-end, including a real replay-harness run
whose events genuinely cross the WebSocket boundary (not a stub).
"""

from __future__ import annotations

import asyncio
import json

import sys as _sys

if "pytest" in _sys.modules:  # under pytest: skip cleanly instead of a collection error (B13); the plain
    import pytest              # tests/_run_all.py runner reports the ModuleNotFoundError below as SKIPPED
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.app import create_app
from api.websocket import ConnectionManager
from core.events import DeliberationStarted


def run(coro):
    return asyncio.run(coro)


# --- ConnectionManager unit tests (no HTTP/WS server involved) --------


class _FakeWebSocket:
    """Duck-typed stand-in for fastapi.WebSocket -- ConnectionManager only
    ever calls .accept() and .send_text() on it, never isinstance-checks it.
    """

    def __init__(self, fail_on_send: bool = False) -> None:
        self.accepted = False
        self.sent: list[str] = []
        self._fail_on_send = fail_on_send

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, message: str) -> None:
        if self._fail_on_send:
            raise RuntimeError("connection closed")
        self.sent.append(message)


def test_connect_registers_and_accepts_the_socket():
    manager = ConnectionManager()
    socket = _FakeWebSocket()

    run(manager.connect(socket, "call:abc"))

    assert socket.accepted
    assert manager.connection_count("call:abc") == 1


def test_broadcast_with_no_subscribers_is_a_noop():
    manager = ConnectionManager()
    run(manager.broadcast("call:nobody", {"event_type": "PartialTranscript"}))


def test_broadcast_sends_json_to_every_connected_client_on_the_channel():
    manager = ConnectionManager()
    a, b = _FakeWebSocket(), _FakeWebSocket()

    async def scenario():
        await manager.connect(a, "replay:r1")
        await manager.connect(b, "replay:r1")
        await manager.broadcast("replay:r1", {"event_type": "BargeIn", "call_id": "c1"})

    run(scenario())

    assert len(a.sent) == 1
    assert len(b.sent) == 1
    assert a.sent[0] == b.sent[0]
    assert "BargeIn" in a.sent[0]


def test_broadcast_only_reaches_clients_on_the_matching_channel():
    manager = ConnectionManager()
    a, b = _FakeWebSocket(), _FakeWebSocket()

    async def scenario():
        await manager.connect(a, "replay:r1")
        await manager.connect(b, "replay:r2")
        await manager.broadcast("replay:r1", {"event_type": "BargeIn"})

    run(scenario())

    assert len(a.sent) == 1
    assert len(b.sent) == 0


def test_disconnect_removes_the_socket_and_stops_future_delivery():
    manager = ConnectionManager()
    socket = _FakeWebSocket()

    async def scenario():
        await manager.connect(socket, "call:x")
        manager.disconnect(socket, "call:x")
        await manager.broadcast("call:x", {"event_type": "BargeIn"})

    run(scenario())

    assert manager.connection_count("call:x") == 0
    assert socket.sent == []


def test_a_broken_socket_does_not_prevent_delivery_to_other_clients():
    manager = ConnectionManager()
    good, broken = _FakeWebSocket(), _FakeWebSocket(fail_on_send=True)

    async def scenario():
        await manager.connect(good, "call:x")
        await manager.connect(broken, "call:x")
        await manager.broadcast("call:x", {"event_type": "BargeIn"})

    run(scenario())

    assert len(good.sent) == 1
    # A failed send is treated as that client disconnecting, not a crash
    # that would stop delivery to everyone else on the channel.
    assert manager.connection_count("call:x") == 1


def test_event_to_dict_survives_a_broadcast_round_trip_as_json():
    manager = ConnectionManager()
    socket = _FakeWebSocket()
    event = DeliberationStarted(
        call_id="c1", timestamp_ms=42, decision_id="dec-0001", intent="tow_request"
    )

    async def scenario():
        await manager.connect(socket, "call:c1")
        await manager.broadcast("call:c1", event.to_dict())

    run(scenario())

    received = json.loads(socket.sent[0])
    assert received["event_type"] == "DeliberationStarted"
    assert received["decision_id"] == "dec-0001"
    assert received["call_id"] == "c1"


# --- FastAPI app tests (via TestClient) --------------------------------


def test_create_app_returns_a_fastapi_instance_with_isolated_state():
    app1 = create_app()
    app2 = create_app()
    assert app1.state.connections is not app2.state.connections


def test_health_endpoint_reports_ok_with_zero_connections_initially():
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["live_connections"] == 0


def test_list_demo_scenarios_includes_the_required_scenario_ids():
    client = TestClient(create_app())
    response = client.get("/demo/scenarios")
    assert response.status_code == 200
    scenarios = response.json()["scenarios"]
    for expected in ("normal_tow", "emergency", "contradiction", "rejected_action"):
        assert expected in scenarios


def test_demo_run_with_unknown_scenario_returns_404():
    client = TestClient(create_app())
    response = client.post("/demo/run/run1/not_a_real_scenario")
    assert response.status_code == 404


def test_demo_run_with_unknown_agent_type_returns_400():
    client = TestClient(create_app())
    response = client.post(
        "/demo/run/run1/normal_tow", params={"agent_type": "not_a_real_agent"}
    )
    assert response.status_code == 400


def test_call_channel_websocket_connects_and_disconnects_cleanly():
    client = TestClient(create_app())
    with client.websocket_connect("/ws/call/abc123"):
        pass  # reaching here without raising is the assertion


def test_websocket_connection_is_reflected_in_health_live_connection_count():
    client = TestClient(create_app())
    with client.websocket_connect("/ws/replay/run1"):
        response = client.get("/health")
        assert response.json()["live_connections"] == 1
    response = client.get("/health")
    assert response.json()["live_connections"] == 0


def test_demo_run_relays_the_real_event_trace_to_a_connected_websocket_client():
    client = TestClient(create_app())
    with client.websocket_connect("/ws/replay/run1") as websocket:
        response = client.post(
            "/demo/run/run1/normal_tow", params={"agent_type": "deliberative"}
        )
        assert response.status_code == 200
        result = response.json()

        received_types = [
            websocket.receive_json()["event_type"] for _ in range(len(result["event_trace"]))
        ]

    trace_types = [e["event_type"] for e in result["event_trace"]]
    # Delivery is per-event-task over the WebSocket, so exact interleaving
    # order isn't guaranteed the way in-process list order is -- compare
    # as multisets. The substantive point is that these are the *real*
    # deliberation events, not a stub.
    assert sorted(received_types) == sorted(trace_types)
    assert "DeliberationStarted" in received_types
    assert "DeliberationResolved" in received_types
    assert "ActionFinalized" in received_types


def test_multiple_websocket_clients_all_receive_the_same_demo_run():
    client = TestClient(create_app())
    with client.websocket_connect("/ws/replay/run2") as ws_a, client.websocket_connect(
        "/ws/replay/run2"
    ) as ws_b:
        response = client.post("/demo/run/run2/emergency")
        result = response.json()
        count = len(result["event_trace"])

        types_a = sorted(ws_a.receive_json()["event_type"] for _ in range(count))
        types_b = sorted(ws_b.receive_json()["event_type"] for _ in range(count))

    assert types_a == types_b == sorted(e["event_type"] for e in result["event_trace"])


def test_disconnecting_one_client_does_not_break_the_run_for_another():
    client = TestClient(create_app())
    with client.websocket_connect("/ws/replay/run3") as ws_a:
        with client.websocket_connect("/ws/replay/run3"):
            pass  # connects, then immediately disconnects

        response = client.post("/demo/run/run3/normal_tow")
        result = response.json()
        count = len(result["event_trace"])
        received = sorted(ws_a.receive_json()["event_type"] for _ in range(count))

    assert received == sorted(e["event_type"] for e in result["event_trace"])


def test_naive_agent_demo_run_still_uses_the_same_relay_and_commit_safety():
    client = TestClient(create_app())
    with client.websocket_connect("/ws/replay/run4") as websocket:
        response = client.post("/demo/run/run4/normal_tow", params={"agent_type": "naive"})
        result = response.json()
        count = len(result["event_trace"])
        received = [websocket.receive_json()["event_type"] for _ in range(count)]

    # Naive skips deliberation entirely (FR-4.1) -- same real event
    # contract, genuinely different content, which is what
    # harness/comparison.py already relies on for the Phase 6 comparison.
    assert "DeliberationStarted" not in received
    assert result["unsafe_finalizations"] >= 1
