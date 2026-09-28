"""Live transport regressions: SSE event ids + replay, single consumer, clean teardown, input validation."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from ui import api, live


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("TRIAGELINE_OFFLINE", "1")
    with TestClient(api.create_app()) as c:
        yield c


def _wait(pred, t=6.0):
    end = time.time() + t
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_events_have_monotonic_ids_and_replay():
    s = live.SESSIONS.start()
    try:
        s.user_text("Find a flight to Denver", False)
        assert _wait(lambda: any(e["kind"] == "say" for e in s.log))
        ids = [e["id"] for e in s.log]
        assert ids == sorted(ids) and len(set(ids)) == len(ids)
        assert [e["id"] for e in s.events_after(ids[0])] == ids[1:]
    finally:
        live.SESSIONS.end(s.sid)


def test_new_stream_claim_supersedes_old_consumer():
    s = live.SESSIONS.start()
    try:
        g1 = s.claim_stream()
        g2 = s.claim_stream()
        assert g2 > g1 and s.stream_gen == g2 and s.out.empty()
    finally:
        live.SESSIONS.end(s.sid)


def test_close_emits_closed_and_stops_loop_cleanly():
    s = live.SESSIONS.start()
    live.SESSIONS.end(s.sid)
    assert s.log[-1]["kind"] == "closed"
    s.thread.join(3)
    assert not s.thread.is_alive() and s.loop.is_closed()


def test_http_rejects_non_object_json_and_bad_numbers(client):
    r = client.post("/api/run", content=b"[1,2]", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    sc = {"events": [{"event_type": "user_speech_chunk", "timestamp_ms": "NaN", "payload": {"text": "hi"}}]}
    assert client.post("/api/run", json={"scenario": sc}).status_code == 400
    r = client.post("/api/run", json={"path": "scenarios/pub_01_text_simple.json", "time_scale": "x"})
    assert r.status_code == 400 and r.json()["code"] == "bad_request"


def test_sse_stream_replays_with_ids(client):
    import threading
    client.post("/api/auth/login", json={})
    sid = client.post("/api/live/start", json={}).json()["sid"]
    client.post(f"/api/live/{sid}/say", json={"text": "Find a flight to Denver"})
    s = client.app.state.sessions.get(sid)
    assert _wait(lambda: len(s.log) >= 2)
    # the in-process client only returns when the generator ends: close the session shortly after
    threading.Timer(1.0, lambda: client.app.state.sessions.end(sid)).start()
    t0 = time.monotonic()
    r = client.get(f"/api/live/{sid}/stream?last=0")
    assert time.monotonic() - t0 < 4          # closed session terminates the stream promptly (event wake-up)
    lines = r.text.splitlines()
    assert any(ln.startswith("id: 1") for ln in lines)
    data = [json.loads(ln[6:]) for ln in lines if ln.startswith("data: ")]
    assert data[0]["replay"] is True and data[0]["id"] == 1
    assert data[-1]["kind"] == "closed"


def test_emit_wakes_subscribers():
    import asyncio
    s = live.SESSIONS.start()
    try:
        async def go():
            ev = asyncio.Event()
            s.subscribe(asyncio.get_running_loop(), ev)
            s.emit("probe")
            await asyncio.wait_for(ev.wait(), 1)
            s.unsubscribe(asyncio.get_running_loop(), ev)
        asyncio.run(go())
    finally:
        live.SESSIONS.end(s.sid)


def test_cancelled_read_only_tool_never_reports_done():
    """L1: cancel of a read-only FDB call emits 'cancelled' and a late result is dropped."""
    import asyncio

    async def go():
        sess = live.LiveSession.__new__(live.LiveSession)
        sess.pending, sess.events, sess.cancelled = {}, [], set()
        sess.in_q, sess.out_q = asyncio.Queue(), asyncio.Queue()
        sess.loop = asyncio.get_running_loop()
        sess.emit = lambda kind, **kw: sess.events.append((kind, kw))

        class Agent:
            tools = {"track_order": {"kind": "read_only"}}
        sess.agent = Agent()

        class Slow:
            mode = "mock"
            fdb_tools = {"track_order": {}}

            async def execute(self, api, args):
                await asyncio.sleep(0.3)
                return {"status": "success", "order": "late"}
        sess.tools = Slow()
        pump = asyncio.create_task(sess._pump())
        await sess.out_q.put({"action": "tool_call", "payload": {"call_id": "c1", "api_name": "track_order",
                                                                 "args": {"order_id": "A"}}})
        await asyncio.sleep(0.05)
        await sess.out_q.put({"action": "cancel_tool", "payload": {"call_id": "c1"}})
        await asyncio.sleep(0.5)
        pump.cancel()
        statuses = [kw.get("status") for k, kw in sess.events if k == "task"]
        assert statuses == ["running", "cancelled"]
        ev = sess.in_q.get_nowait()
        assert ev["event_type"] == "tool_cancelled" and ev["payload"]["confirmed"] is True
        assert sess.in_q.empty()                     # no tool_result for the cancelled call
    asyncio.run(go())


def test_interrupt_is_not_blocked_by_command_lock(client):
    """A barge-in must not queue behind a slow upload holding the per-session command lock."""
    client.post("/api/auth/login", json={})
    sid = client.post("/api/live/start", json={}).json()["sid"]
    lock = client.app.state.sessions.get(sid).command_lock
    lock._locked = True          # simulate a held lock (a slow /audio decode) without cross-loop acquisition
    try:
        t0 = time.monotonic()
        r = client.post(f"/api/live/{sid}/interrupt", json={})
        assert r.status_code == 200 and time.monotonic() - t0 < 1
    finally:
        lock._locked = False
        client.post(f"/api/live/{sid}/end", json={})
