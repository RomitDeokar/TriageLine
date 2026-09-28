"""Mobile token contract, auth isolation, and production route restrictions."""
import base64
import hashlib
import hmac
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "ui"))

import mobile  # noqa: E402
import server  # noqa: E402


def _request(url, method="GET", token=None, origin=None):
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(url, method=method, data=b"{}" if method == "POST" else None, headers=headers)
    try:
        res = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as exc:
        res = exc
    with res:
        return res.status, dict(res.headers), json.loads(res.read())


def test_scoped_signed_participant_tokens(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "lk_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "lk_secret")
    first, second = mobile.issue_token(), mobile.issue_token()
    assert first["room"] != second["room"]
    assert first["identity"] != second["identity"]
    assert first["mode"] == "LIVE AUDIO + MOCK TOOLS"
    header, payload, signature = first["token"].split(".")
    expected = base64.urlsafe_b64encode(hmac.new(b"lk_secret", (header + "." + payload).encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    assert hmac.compare_digest(signature, expected)
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert claims["video"]["room"] == first["room"]
    assert claims["video"]["roomJoin"] is True
    assert claims["exp"] - claims["iat"] == 600


def test_production_http_auth_and_no_mock_routes(monkeypatch):
    monkeypatch.setenv("TRIAGELINE_API_KEY", "x" * 36)
    monkeypatch.setenv("TRIAGELINE_PRODUCTION", "1")
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "lk_key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "lk_secret")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        status, headers, body = _request(base + "/api/mobile/token", "POST", origin="https://evil.example")
        assert status == 401 and body["code"] == "unauthorized"
        assert "Access-Control-Allow-Origin" not in headers
        status, _, body = _request(base + "/api/mobile/token", "POST", "x" * 36)
        assert status == 201 and body["url"] == "wss://example.livekit.cloud"
        assert "lk_secret" not in json.dumps(body)
        for route in ("/api/ready", "/api/run", "/api/live/start", "/api/live/anything/log"):
            status, _, _ = _request(base + route, "POST", "x" * 36)
            assert status == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


def test_unconfigured_mobile_endpoint_does_not_expose_errors(monkeypatch):
    monkeypatch.setenv("TRIAGELINE_API_KEY", "k" * 36)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, body = _request(f"http://127.0.0.1:{httpd.server_address[1]}/api/mobile/token", "POST", "k" * 36)
        assert status == 503 and body["code"] == "unavailable"
        assert "secret" not in json.dumps(body).lower()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)
