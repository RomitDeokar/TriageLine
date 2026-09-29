"""Regressions for the 2026-09-29 submission-readiness fixes (audit bugs 1-4, LLM mode, multi-call plans,
per-turn timing, speech-provider order, pre-dispatch cancellation). No network; official mock tools."""
import asyncio
import os
import sys
from unittest.mock import patch

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "livekit_agent")]

from agent import agent as agent_mod  # noqa: E402
from agent import llm_planner as planner  # noqa: E402
from agent import nlu  # noqa: E402
from livekit_agent.adapter import TriageAdapter  # noqa: E402
from livekit_agent.fdb_tools import FDB_TOOLS  # noqa: E402


@pytest.fixture(autouse=True)
def bench(monkeypatch):
    monkeypatch.setenv("TRIAGELINE_BENCHMARK_POLICY", "1")
    monkeypatch.setenv("TRIAGELINE_LLM_PLANNER", "0")
    monkeypatch.delenv("TRIAGELINE_LLM_MODE", raising=False)


def _registry():
    import mock_apis
    return mock_apis.MockAPIRegistry(latency_profile="instant", enable_logging=False)


async def _drive(turns, settle_s=0.0, gap=0.05, before_final=None):
    reg, calls, spoken = _registry(), [], []

    async def ex(cid, api, args):
        calls.append((api, dict(args)))
        try:
            res = reg.call(api, **args)
        except TypeError as e:
            res = {"status": "error", "error": "invalid_args", "message": str(e)}
        await ad.on_tool_completed(cid, res, status="error" if res.get("status") == "error" else "ok")

    async def sp(kind, text):
        spoken.append((kind, text))

    async def cn(cid):
        pass

    ad = TriageAdapter(tool_executor=ex, tool_canceller=cn, speak=sp, settle_s=settle_s)
    await ad.start(FDB_TOOLS)
    for t in turns:
        await ad.on_user_final(t)
        await asyncio.sleep(gap)
    await ad.wait_idle(timeout=10)
    await ad.stop()
    return calls, spoken


def run(turns, **kw):
    return asyncio.run(_drive(turns, **kw))


# ---------------------------------------------------------------- bug 1: trailing budget clause
def test_trailing_budget_clause_refines_single_search():
    calls, _ = run(["so I'm after some bluetooth speakers... and, uh, I'd prefer... to stay under "
                    "80 dollars ideally. Anything like that?"])
    assert [c[0] for c in calls] == ["search_products"]
    assert calls[0][1]["max_price"] == 80 and "speaker" in calls[0][1]["query"]


def test_two_filter_values_still_split():
    calls, _ = run(["Set the filter for balcony to true and gym to yes."])
    assert [c[1]["filter_name"] for c in calls if c[0] == "update_search_filter"] == ["balcony", "gym"]


# ---------------------------------------------------------------- bug 2: onset without transcript
def test_speech_onset_without_transcript_does_not_strand_request():
    async def go():
        reg, calls = _registry(), []

        async def ex(cid, api, args):
            calls.append(api)
            await ad.on_tool_completed(cid, reg.call(api, **args))

        async def noop(*a):
            pass

        ad = TriageAdapter(tool_executor=ex, tool_canceller=noop, speak=noop, settle_s=0.1, max_settle_s=0.2)
        await ad.start(FDB_TOOLS)
        await ad.on_user_final("Track order QX4410 please.")
        await ad.on_user_speech_start()          # a breath / click: no final follows
        await ad.on_user_final("")               # STT heard only noise
        await asyncio.sleep(0.6)
        await ad.stop()
        return calls
    assert asyncio.run(go()) == ["track_order"]


def test_speech_end_restarts_normal_commit():
    async def go():
        calls = []

        async def ex(cid, api, args):
            calls.append(api)

        async def noop(*a):
            pass

        ad = TriageAdapter(tool_executor=ex, tool_canceller=noop, speak=noop, settle_s=0.05, max_settle_s=5.0)
        await ad.start(FDB_TOOLS)
        await ad.on_user_final("Track order QX4410 please.")
        await ad.on_user_speech_start()          # long wait armed
        await ad.on_user_speech_end()            # back to the short wait
        await asyncio.sleep(0.4)
        await ad.stop()
        return calls
    assert asyncio.run(go()) == ["track_order"]


# ---------------------------------------------------------------- bug 4: missing required args
def test_state_modifying_call_never_sent_without_required_arg():
    calls, spoken = run(["Please add two to my cart."])
    assert not [c for c in calls if c[0] == "add_to_cart" and "product_id" not in c[1]]


def test_chained_steps_bind_previous_result_and_committed_filter():
    calls, _ = run(["First set my max price to 1300. Then search for apartments in Denver. "
                    "And then check the biking time from that place to the library."])
    names = [c[0] for c in calls]
    assert names == ["update_search_filter", "search_apartments", "calculate_commute"]
    assert calls[1][1].get("max_price") == 1300
    assert calls[2][1].get("origin_address") and calls[2][1]["mode"] in ("biking", "cycling", "bike")


def test_quantity_not_overridden_by_apportioning_phrase():
    a, _ = nlu.build_args(FDB_TOOLS["add_to_cart"], nlu.normalize_asr(
        "Can you add, like, 3 of item Q-7-1 to my cart? One for me and two for my kids."), {})
    assert a["quantity"] == 3 and a["product_id"] == "Q71"


def test_type_field_follows_self_repair_over_schema_example():
    a, _ = nlu.build_args(FDB_TOOLS["get_card_benefits"], "perks on my platinum card, sorry I mean my bronze card", {})
    assert a["card_type"] == "bronze"


# ---------------------------------------------------------------- LLM mode + multi-call plans
def test_llm_mode_defaults():
    assert agent_mod.llm_mode() == "fallback"                 # benchmark policy
    os.environ["TRIAGELINE_BENCHMARK_POLICY"] = "0"
    try:
        assert agent_mod.llm_mode() == "primary"              # phone assistant
    finally:
        os.environ["TRIAGELINE_BENCHMARK_POLICY"] = "1"


def test_rules_first_in_benchmark_mode(monkeypatch):
    monkeypatch.setenv("TRIAGELINE_LLM_PLANNER", "1")
    with patch.object(planner, "plan", return_value=[]) as plan:
        calls, _ = run(["Track order QX4410 please."])
    assert calls == [("track_order", {"order_id": "QX4410"})]
    assert not plan.called                                     # complete rule call: planner never consulted


def test_multi_call_plan_is_executed_in_order(monkeypatch):
    monkeypatch.setenv("TRIAGELINE_LLM_PLANNER", "1")
    monkeypatch.setenv("TRIAGELINE_LLM_MODE", "primary")
    plan = [{"name": "search_products", "args": {"query": "desk lamp"}},
            {"name": "track_order", "args": {"order_id": "ZZ901"}}]
    with patch.object(planner, "plan", return_value=plan):
        calls, _ = run(["find a desk lamp and track order ZZ901"])
    assert [c[0] for c in calls] == ["search_products", "track_order"]


# ---------------------------------------------------------------- speech provider order
def test_benchmark_prefers_streaming_deepgram(monkeypatch):
    from livekit_agent import speech_providers as sp
    monkeypatch.setenv("DEEPGRAM_API_KEY", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "y")
    monkeypatch.setenv("TRIAGELINE_MODE", "benchmark")
    assert sp.selected()["stt_provider"] == "deepgram" and sp.selected()["tts_provider"] == "deepgram"
    monkeypatch.setenv("TRIAGELINE_MODE", "assistant")
    assert sp.selected()["stt_provider"] == "gemini"


# ---------------------------------------------------------------- pre-dispatch cancel of a state change
def test_undispatched_state_modifying_call_is_really_cancelled():
    async def go():
        started = []

        async def ex(cid, api, args):
            started.append(api)

        async def noop(*a):
            pass

        ad = TriageAdapter(tool_executor=ex, tool_canceller=noop, speak=noop)
        await ad.start(FDB_TOOLS)
        await ad.out_q.put({"action": "tool_call", "payload": {"call_id": "cX", "api_name": "add_to_cart",
                                                               "args": {"product_id": "P1", "quantity": 1}}})
        await ad.out_q.put({"action": "cancel_tool", "payload": {"call_id": "cX"}})
        await asyncio.sleep(0.1)
        await ad.stop()
        return started
    assert asyncio.run(go()) == []
