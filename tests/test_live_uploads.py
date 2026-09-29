"""Live-assistant upload hardening (audit §7): content-validated uploads, cleanup on session end."""
import base64
import io
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PIL = pytest.importorskip("PIL")
from ui import live  # noqa: E402


def _png_b64() -> str:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 10, 10)).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@pytest.fixture()
def session():
    s = live.SESSIONS.start()
    yield s
    live.SESSIONS.end(s.sid)


def test_fake_image_rejected(session):
    with pytest.raises(ValueError):
        session.user_frame("data:image/png;base64,AAAA")          # the exact audit probe


def test_real_image_reencoded_as_jpeg(session):
    ref = session.user_frame(_png_b64())
    with open(os.path.join(ROOT, ref), "rb") as f:
        assert f.read(3) == b"\xff\xd8\xff"                          # JPEG SOI marker


def test_non_audio_rejected(session):
    with pytest.raises(ValueError):
        session.user_audio(base64.b64encode(b"<html>not audio</html>").decode(), False)


def test_bad_base64_rejected(session):
    with pytest.raises(ValueError):
        session.user_frame("data:image/png;base64,@@@not-base64@@@")


def test_uploads_deleted_when_session_ends():
    s = live.SESSIONS.start()
    s.user_frame(_png_b64())
    assert os.listdir(s.dir)
    live.SESSIONS.end(s.sid)
    assert not os.path.exists(s.dir)


def test_server_transcript_is_echoed_to_the_ui(session):
    """A voice clip's recognised text reaches the PWA as an ``asr`` event (the UI shows what was heard)."""
    import asyncio
    import time
    fut = asyncio.run_coroutine_threadsafe(session.agent.on_audio_result(
        [{"text": "track order QRS765", "words": [], "error": None}]), session.loop)
    fut.result(5)
    deadline = time.time() + 2
    while time.time() < deadline and not any(e["kind"] == "asr" for e in session.events_after(0)):
        time.sleep(0.05)
    asr = [e for e in session.events_after(0) if e["kind"] == "asr"]
    assert asr and asr[0]["text"] == "track order QRS765" and asr[0]["error"] is None
    users = [e for e in session.events_after(0) if e["kind"] == "user"]
    assert all("event_type" in e and "as_" not in e for e in users)
