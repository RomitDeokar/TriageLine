"""Submission regressions: real mock tools, provider payloads and real SDK audio.

No hosted API requests: transports are patched at their network boundary.
"""
import asyncio
import base64
import io
import json
import time
from unittest.mock import patch

import pytest

from agent import llm_planner as planner
from harness.mock_env import TOOL_REGISTRY
from livekit_agent.fdb_tools import FDB_TOOLS


@pytest.fixture(autouse=True)
def local_env(monkeypatch):
    monkeypatch.setenv("TRIAGELINE_LLM_PLANNER", "0")
    monkeypatch.setenv("TRIAGELINE_BENCHMARK_POLICY", "0")


def test_gemini_payload_supports_full_manifest(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-not-a-secret")
    response = {"candidates": [{"content": {"parts": [{"functionCall": {
        "name": "update_search_filter", "args": {"filter_name": "parking", "value": True}}}]}}]}
    captured = []

    def request(req, timeout):
        captured.append(json.loads(req.data))
        return io.BytesIO(json.dumps(response).encode())

    with patch("urllib.request.urlopen", request):
        calls = planner.plan("Require parking", {**TOOL_REGISTRY, **FDB_TOOLS})
    assert calls[0]["args"]["value"] is True
    schemas = {f["name"]: f["parametersJsonSchema"] for f in captured[0]["tools"][0]["functionDeclarations"]}
    assert "type" not in schemas["update_search_filter"]["properties"]["value"]
    assert schemas["lookup_manual"]["properties"]["image_embedding"]["items"] == {"type": "number"}
    assert "any" not in json.dumps(schemas)


def test_planner_applies_defaults_and_bounds():
    assert planner.validate([{"name": "add_to_cart", "args": {"product_id": "P4"}}], FDB_TOOLS) == [
        {"name": "add_to_cart", "args": {"product_id": "P4", "quantity": 1}}]
    for value in (0, -1, 0.5, True, float("nan"), float("inf")):
        assert not planner.validate([{"name": "add_to_cart", "args": {"product_id": "P4", "quantity": value}}], FDB_TOOLS)


def test_primary_planner_handles_complete_request(monkeypatch):
    from tests._probe import run
    monkeypatch.setenv("TRIAGELINE_LLM_PLANNER", "1")
    with patch.object(planner, "plan", return_value=[{"name": "track_order", "args": {"order_id": "REPAIRED9"}}]) as plan:
        calls, _ = run(["Track order ABC123"], tail=0.2)
    assert plan.called
    assert calls == [("track_order", {"order_id": "REPAIRED9"})]


def test_failed_planner_falls_back_without_loop(monkeypatch):
    from tests._probe import run
    monkeypatch.setenv("TRIAGELINE_LLM_PLANNER", "1")
    with patch.object(planner, "plan", return_value=[]) as plan:
        calls, _ = run(["Track order ABC123"], tail=0.2)
    assert plan.call_count == 1
    assert calls == [("track_order", {"order_id": "ABC123"})]


def test_multiple_slots_in_clarification_answer():
    from tests._probe import run
    calls, _ = run(["Find apartments in Portland", "2 bedrooms under 2200"], tail=0.2)
    assert calls == [("search_apartments", {"city": "Portland", "bedrooms": 2, "max_price": 2200})]


def test_browser_tools_reach_actual_backends():
    from ui.live import ToolAdapter
    async def check():
        adapter = ToolAdapter("test")
        assert set(FDB_TOOLS) - {"search_flights"} <= set(adapter.registry)
        product = await adapter.execute("search_products", {"query": "desk", "max_price": 200})
        result = await adapter.execute("add_to_cart", {"product_id": product["products"][0]["product_id"], "quantity": 2})
        assert result["quantity"] == 2
        error = await adapter.execute("search_apartments", {"city": "Portland"})
        assert error["error"] == "invalid_args"
    asyncio.run(check())


def test_browser_chain_and_dedup():
    from ui.live import LiveSession
    session = LiveSession("test-chain")
    try:
        session.user_text("Search for a desk under 200 dollars then add it to my cart", False)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if any(e.get("api") == "add_to_cart" and e.get("status") == "done" for e in session.log):
                break
            time.sleep(0.02)
        assert any(e.get("api") == "add_to_cart" and e.get("status") == "done" for e in session.log)
        session.user_text("Add PROD1 to my cart", False)
        time.sleep(0.2)
        calls = [e for e in session.log if e.get("api") == "add_to_cart" and e.get("status") == "running"]
        assert len(calls) == 1
    finally:
        session.close()
        session.thread.join(timeout=2)


def test_browser_gemini_audio_uses_container_and_no_fake_confidence(tmp_path, monkeypatch):
    from agent import perception
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setenv("TRIAGELINE_STT_PROVIDER", "gemini")
    monkeypatch.delenv("TRIAGELINE_OFFLINE", raising=False)
    audio = tmp_path / "audio.webm"
    audio.write_bytes(b"RIFF" + b"\0" * 50)
    captured = []
    def request(req, timeout):
        captured.append(json.loads(req.data))
        return io.BytesIO(json.dumps({"candidates": [{"content": {"parts": [{"text": "Track order ABC123"}]}}]}).encode())
    with patch("urllib.request.urlopen", request), patch.object(perception, "load_asr", side_effect=AssertionError("local ASR must not load")):
        result = perception.transcribe(str(audio))
    assert result["text"] == "Track order ABC123"
    assert result["words"] == [] and result["confidence_available"] is False
    assert captured[0]["contents"][0]["parts"][1]["inlineData"]["mimeType"] == "audio/wav"


def test_offline_audio_never_calls_gemini(monkeypatch):
    from agent import perception
    monkeypatch.setenv("TRIAGELINE_OFFLINE", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    with patch.object(perception, "resolve", return_value="audio.wav"), patch.object(perception, "load_asr", return_value=False), patch.object(perception, "_gemini_transcribe") as remote:
        assert perception.transcribe("audio.wav")["ok"] is False
        remote.assert_not_called()


def test_real_livekit_gemini_stt_and_tts():
    pytest.importorskip("livekit.agents")
    from livekit import rtc
    from livekit.agents import stt
    from livekit_agent import gemini_speech as speech
    async def check():
        async def generated(model, body, timeout):
            if "tts" in model:
                return {"candidates": [{"content": {"parts": [{"inlineData": {
                    "mimeType": "audio/L16;codec=pcm;rate=24000", "data": base64.b64encode(b"\0" * 9600).decode()}}]}}]}
            assert body["contents"][0]["parts"][1]["inlineData"]["mimeType"] == "audio/wav"
            return {"candidates": [{"content": {"parts": [{"text": "Track order ABC123"}]}}]}
        with patch.object(speech, "generate", generated):
            frame = rtc.AudioFrame(data=b"\0" * 3200, sample_rate=16000, num_channels=1, samples_per_channel=1600)
            recognizer = speech.GeminiSTT()
            event = await recognizer.recognize(frame)
            assert event.type == stt.SpeechEventType.FINAL_TRANSCRIPT
            assert event.alternatives[0].text == "Track order ABC123"
            engine = speech.GeminiTTS()
            async with engine.synthesize("Done") as stream:
                frames = [e.frame async for e in stream]
            assert sum(f.samples_per_channel for f in frames) == 4800
            await recognizer.aclose()
            await engine.aclose()
    asyncio.run(check())
