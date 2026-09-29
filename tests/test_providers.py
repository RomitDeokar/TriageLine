"""Provider failover chain: retries, failover, fatal errors, malformed output, secrets never logged."""
import io
import json
import logging
import urllib.error

import pytest

from agent import llm_planner as planner
from agent import providers
from livekit_agent.fdb_tools import FDB_TOOLS

TRACK = {"track_order": FDB_TOOLS["track_order"]} if "track_order" in FDB_TOOLS else dict(list(FDB_TOOLS.items())[:1])
TOOL = next(iter(TRACK))
REQ_ARG = next((k for k, v in (TRACK[TOOL].get("args") or {}).items() if v.get("required")), None)


def _http_error(code, retry_after=None):
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    return urllib.error.HTTPError("https://x", code, "err", headers, io.BytesIO(b'{"secret":"body"}'))


def _gemini_ok(name=TOOL, args=None):
    args = args if args is not None else ({REQ_ARG: "QRT417"} if REQ_ARG else {})
    return {"candidates": [{"content": {"parts": [{"functionCall": {"name": name, "args": args}}]}}]}


def _openai_ok(name=TOOL, args=None, text=None):
    args = args if args is not None else ({REQ_ARG: "QRT417"} if REQ_ARG else {})
    msg = {"content": text} if text is not None else \
        {"content": None, "tool_calls": [{"type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
    return {"choices": [{"message": msg}]}


class Transport:
    """Scripted urlopen: one entry per request, keyed by the host that must be called."""

    def __init__(self, script):
        self.script, self.seen = list(script), []

    def __call__(self, req, timeout):
        self.seen.append((req.full_url, json.loads(req.data), dict(req.header_items())))
        host, outcome = self.script.pop(0)
        assert host in req.full_url, f"expected {host}, called {req.full_url}"
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(json.dumps(outcome).encode())


@pytest.fixture
def two_providers(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-secret-value")
    monkeypatch.setenv("CEREBRAS_API_KEY", "c-secret-value")
    monkeypatch.setenv("TRIAGELINE_LLM_CHAIN", "gemini,cerebras")
    monkeypatch.setattr(providers.time, "sleep", lambda s: None)


def test_auto_chain_only_uses_free_providers(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert providers.chain() == ["gemini"]          # nothing free configured -> placeholder, unconfigured
    assert not planner.enabled()
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.setenv("CEREBRAS_API_KEY", "x")
    assert providers.chain() == ["cerebras", "openrouter"]
    assert planner.enabled()


def test_unknown_provider_is_a_config_error(monkeypatch):
    monkeypatch.setenv("TRIAGELINE_LLM_CHAIN", "gemini,nope")
    with pytest.raises(ValueError):
        providers.chain()
    assert planner.plan("track order QRT417", TRACK) == []
    assert not planner.enabled()


def test_429_retries_same_provider_then_succeeds(two_providers, monkeypatch):
    t = Transport([("generativelanguage", _http_error(429, 0.1)), ("generativelanguage", _gemini_ok())])
    monkeypatch.setattr("urllib.request.urlopen", t)
    calls = planner.plan("track order QRT417", TRACK)
    assert calls and calls[0]["name"] == TOOL and len(t.seen) == 2


def test_repeated_5xx_fails_over_to_next_provider(two_providers, monkeypatch):
    t = Transport([("generativelanguage", _http_error(503)), ("generativelanguage", _http_error(503)),
                   ("api.cerebras.ai", _openai_ok())])
    monkeypatch.setattr("urllib.request.urlopen", t)
    calls = planner.plan("track order QRT417", TRACK)
    assert calls and calls[0]["name"] == TOOL
    url, body, headers = t.seen[-1]
    assert body["tools"][0]["type"] == "function" and body["tool_choice"] == "auto"
    assert headers.get("Authorization") == "Bearer c-secret-value"


def test_fatal_401_skips_retry_and_cools_down(two_providers, monkeypatch):
    t = Transport([("generativelanguage", _http_error(401)), ("api.cerebras.ai", _openai_ok()),
                   ("api.cerebras.ai", _openai_ok())])
    monkeypatch.setattr("urllib.request.urlopen", t)
    assert planner.plan("track order QRT417", TRACK)
    # second request: gemini is cooling down, cerebras is called directly (no wasted round trip)
    assert planner.plan("track order QRT417", TRACK)
    assert [u for u, _, _ in t.seen].count(next(u for u, _, _ in t.seen if "generativelanguage" in u)) == 1
    st = providers.status()
    assert st["chain"][0]["cooling_down"] and st["chain"][0]["last"]["status"] == 401


def test_all_providers_fail_returns_empty_and_reply_degrades(two_providers, monkeypatch):
    def boom(req, timeout):
        raise TimeoutError()
    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert planner.plan("track order QRT417", TRACK) == []
    assert planner.reply("hello") == planner.UNAVAILABLE


def test_malformed_and_invalid_calls_are_rejected(two_providers, monkeypatch):
    bad = {"choices": [{"message": {"tool_calls": [{"function": {"name": TOOL, "arguments": "{not json"}},
                                                   {"function": {"name": "rm_rf", "arguments": "{}"}}]}}]}
    t = Transport([("generativelanguage", _http_error(400)), ("api.cerebras.ai", bad)])
    monkeypatch.setattr("urllib.request.urlopen", t)
    assert planner.plan("track order QRT417", TRACK) == []


def test_reply_uses_chain_and_gemini_thinking_config(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    t = Transport([("generativelanguage", {"candidates": [{"content": {"parts": [
        {"text": "thinking...", "thought": True}, {"text": "Paris is the capital of France."}]}}]})])
    monkeypatch.setattr("urllib.request.urlopen", t)
    assert planner.reply("capital of france?") == "Paris is the capital of France."
    url, body, _ = t.seen[0]
    assert "gemini-3.5-flash-lite" in url
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "MINIMAL"}
    assert "tools" not in body


def test_openai_schema_omits_empty_required():
    for fn in providers.openai_tools(FDB_TOOLS):
        params = fn["function"]["parameters"]
        assert params.get("required", ["x"]) != []
        assert "any" not in json.dumps(params)


def test_thinking_config_per_family():
    assert providers._gemini_thinking("gemini-2.5-flash") == {"thinkingBudget": 0}
    assert providers._gemini_thinking("gemini-3.8-flash") == {"thinkingLevel": "LOW"}
    assert providers._gemini_thinking("gemini-3.5-flash-lite") == {"thinkingLevel": "MINIMAL"}
    assert providers._gemini_thinking("some-other-model") is None


def test_history_is_truncated(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    t = Transport([("generativelanguage", _gemini_ok())])
    monkeypatch.setattr("urllib.request.urlopen", t)
    planner.plan("track order QRT417", TRACK, history=["x" * 10000] * 20)
    payload = json.loads(t.seen[0][1]["contents"][0]["parts"][0]["text"])
    assert len(payload["previous_context"]) == 6
    assert all(len(h) <= planner.HISTORY_ITEM_CHARS for h in payload["previous_context"])


def test_secrets_and_bodies_never_logged(two_providers, monkeypatch, caplog):
    def boom(req, timeout):
        raise _http_error(500)
    monkeypatch.setattr("urllib.request.urlopen", boom)
    with caplog.at_level(logging.DEBUG):
        planner.plan("my card is 4111 1111 1111 1111", TRACK)
    text = caplog.text
    assert "secret-value" not in text and "4111" not in text and '"secret"' not in text
    assert "HTTP 500" in text


def test_custom_openai_compatible_endpoint(monkeypatch):
    monkeypatch.setenv("TRIAGELINE_LLM_CHAIN", "custom")
    monkeypatch.setenv("TRIAGELINE_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("TRIAGELINE_LLM_MODEL_CUSTOM", "qwen3:8b")
    t = Transport([("localhost:11434", _openai_ok())])
    monkeypatch.setattr("urllib.request.urlopen", t)
    assert planner.enabled() and planner.plan("track order QRT417", TRACK)
    assert t.seen[0][1]["model"] == "qwen3:8b"


# ---------------------------------------------------------------- interactive confirmation gate

def _planner_run(monkeypatch, turns, proposed, benchmark):
    from unittest.mock import patch
    from tests._probe import run
    monkeypatch.setenv("TRIAGELINE_LLM_PLANNER", "1")
    monkeypatch.setenv("TRIAGELINE_BENCHMARK_POLICY", "1" if benchmark else "0")
    with patch.object(planner, "plan", side_effect=lambda *a, **k: list(proposed)), \
            patch.object(planner, "reply", return_value="ok"):
        return run(turns, tail=0.3)


PROPOSED = [{"name": "modify_autopay", "args": {"bill_type": "utilities", "source_account": "checking"}}]
TURN = "hmm could you sort out the autopay thing for the utilities from checking"


def test_llm_side_effect_requires_confirmation(monkeypatch):
    calls, spoken = _planner_run(monkeypatch, [TURN], PROPOSED, benchmark=False)
    assert calls == []
    assert any(k == "clarification_request" and "confirm" in t.lower() for k, t in spoken)


def test_llm_side_effect_runs_after_yes(monkeypatch):
    calls, _ = _planner_run(monkeypatch, [(TURN, 0.3), "yes please"], PROPOSED, benchmark=False)
    assert calls == [("modify_autopay", {"bill_type": "utilities", "source_account": "checking"})]


def test_llm_side_effect_declined(monkeypatch):
    calls, spoken = _planner_run(monkeypatch, [(TURN, 0.3), "no, don't"], PROPOSED, benchmark=False)
    assert calls == []
    assert any("won't" in t for _, t in spoken)


def test_benchmark_policy_keeps_template_behaviour(monkeypatch):
    calls, _ = _planner_run(monkeypatch, [TURN], PROPOSED, benchmark=True)
    assert calls == [("modify_autopay", {"bill_type": "utilities", "source_account": "checking"})]
