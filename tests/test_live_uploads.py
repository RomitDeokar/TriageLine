"""Live-assistant upload hardening (audit §7): content-validated uploads, cleanup on session end."""
import base64
import io
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "ui"))

PIL = pytest.importorskip("PIL")
import live  # noqa: E402


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
