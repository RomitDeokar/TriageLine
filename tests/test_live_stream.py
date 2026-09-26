"""Live transport regressions: SSE event ids + replay, single consumer, clean teardown, input validation."""
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "ui"))

import live  # noqa: E402
import server  # noqa: E402


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


def _srv():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _post(url, raw):
    req = urllib.request.Request(url, data=raw, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_rejects_non_object_json_and_bad_numbers():
    httpd, base = _srv()
    try:
        code, body = _post(base + "/api/run", b"[1,2]")
        assert code == 400 and body["code"] == "bad_request"
        sc = {"events": [{"event_type": "user_speech_chunk", "timestamp_ms": "NaN", "payload": {"text": "hi"}}]}
        code, body = _post(base + "/api/run", json.dumps({"scenario": sc}).encode())
        assert code == 400
        code, body = _post(base + "/api/run", json.dumps({"path": "scenarios/pub_01_text_simple.json", "time_scale": "x"}).encode())
        assert code == 400
    finally:
        httpd.shutdown()


def test_sse_stream_replays_with_ids():
    httpd, base = _srv()
    s = live.SESSIONS.start()
    try:
        s.user_text("Find a flight to Denver", False)
        assert _wait(lambda: len(s.log) >= 2)
        req = urllib.request.Request(f"{base}/api/live/{s.sid}/stream?last=0")
        with urllib.request.urlopen(req, timeout=5) as r:
            lines = []
            while len([ln for ln in lines if ln.startswith(b"id:")]) < 2:
                lines.append(r.readline())
        assert any(ln.startswith(b"id: 1") for ln in lines)
        data = [json.loads(ln[6:]) for ln in lines if ln.startswith(b"data: ")]
        assert data[0]["replay"] is True and data[0]["id"] == 1
    finally:
        live.SESSIONS.end(s.sid)
        httpd.shutdown()
