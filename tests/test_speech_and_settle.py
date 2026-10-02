"""Regression tests for the live-path fixes (STT biasing, bounded settle, condition evaluation,
cue-word ids). These lock the behaviours that measurably improved the official-audio replay and
must not silently regress. Run: pytest tests/test_speech_and_settle.py"""
import asyncio
import os
import time

import pytest

from agent import nlu
from livekit_agent.fdb_tools import FDB_TOOLS


# ---------------------------------------------------------------- STT biasing must not leak answers
def test_tool_vocabulary_excludes_manifest_example_values():
    """The STT bias vocabulary is built from tool/argument NAMES and generic description words —
    never from the quoted example answers inside descriptions (ids contain digits, so a digit
    anywhere in the vocabulary means an example value leaked back in)."""
    vocab = nlu.tool_vocabulary(FDB_TOOLS)
    assert vocab, "vocabulary should not be empty"
    leaked = [w for w in vocab if any(ch.isdigit() for ch in w)]
    assert not leaked, f"example values leaked into the STT bias vocabulary: {leaked}"


def test_whisper_prompt_is_generic_and_value_free():
    """The Whisper/openai initial_prompt must carry no vocabulary or example values: measured on
    the official released audio, a vocabulary prompt degrades decoding badly."""
    from livekit_agent import speech_providers as sp

    prompt = sp.whisper_prompt(sp.bias_terms(FDB_TOOLS))
    assert prompt
    assert not any(ch.isdigit() for ch in prompt), prompt
    lowered = prompt.lower()
    assert not any(term.lower() in lowered for term in sp.bias_terms(FDB_TOOLS) if len(term) > 3), prompt


# ---------------------------------------------------------------- settle wait is bounded
@pytest.mark.parametrize("unfinished", ["and then", "I would like to find"])
def test_settle_wait_is_bounded_by_max_settle(unfinished):
    """An unfinished-looking fragment must not hold the commit anywhere near the old 10 s cap; the
    wait is bounded by max_settle_s (this produced 15-26 s reply latency before the fix)."""
    async def main() -> float:
        async def execute(cid, api, args):  # pragma: no cover - nothing should be dispatched
            raise AssertionError("no tool call expected for an unfinished fragment")

        async def speak(kind, text):
            pass

        async def cancel(cid):
            pass

        from livekit_agent.adapter import TriageAdapter

        ad = TriageAdapter(tool_executor=execute, tool_canceller=cancel, speak=speak,
                           settle_s=0.1, max_settle_s=0.3)
        await ad.start(FDB_TOOLS)
        await ad.on_user_final(unfinished)
        t0 = time.monotonic()
        task = ad._settle_task
        if task is not None:
            await task
        dt = time.monotonic() - t0
        await ad.stop()
        return dt

    dt = asyncio.run(main())
    assert dt < 3.0, f"settle wait {dt:.1f}s — not bounded by max_settle_s"


# ---------------------------------------------------------------- speech floor: no commit mid-utterance
def test_no_commit_while_user_is_still_speaking():
    """A final that arrives while VAD says the caller is still speaking is a FRAGMENT: it must be
    merged into the turn, never executed. Committing it was the main live duplicate/stale-call cause."""
    async def main():
        calls = []

        async def execute(cid, api, args):
            calls.append((api, dict(args)))
            await ad.on_tool_completed(cid, {"status": "success"}, status="ok")

        async def speak(kind, text):
            pass

        async def cancel(cid):
            pass

        from livekit_agent.adapter import TriageAdapter

        ad = TriageAdapter(tool_executor=execute, tool_canceller=cancel, speak=speak,
                           settle_s=0.1, max_settle_s=0.2)
        await ad.start(FDB_TOOLS)
        await ad.on_user_speech_start()
        await ad.on_user_final("search for a desk")
        await asyncio.sleep(1.0)
        assert calls == [], f"committed a mid-utterance fragment: {calls}"
        await ad.on_user_final("under 300")
        await asyncio.sleep(1.0)
        assert calls == [], f"committed while still speaking: {calls}"
        await ad.on_user_speech_end()
        await ad.wait_idle(timeout=5)
        await ad.stop()
        return calls

    calls = asyncio.run(main())
    assert [c[0] for c in calls] == ["search_products"], calls
    assert calls[0][1].get("max_price") == 300 and calls[0][1].get("query") == "desk", calls


# ---------------------------------------------------------------- cue words are never ids
def test_extract_id_never_returns_the_cue_word():
    for heard in ("The order ID is ID", "my order id is number", "order code is code"):
        assert nlu.extract_id(nlu.normalize_asr(heard), "order_id") is None, heard
    # a genuinely spelled letter-only id is still resolved (the benchmark contains several)
    assert nlu.extract_id(nlu.normalize_asr("track order K L M"), "order_id") == "KLM"


# ---------------------------------------------------------------- condition tests the entity price
def test_condition_prefers_entity_price_over_later_aggregate():
    """'if the item is over 50' must test the searched item's price, not a later cart total from a
    subsequent state change (otherwise an else-branch fires on the wrong number)."""
    from agent.agent import ParticipantAgent

    agent = ParticipantAgent(asyncio.Queue(), asyncio.Queue())
    agent.results = [
        ("search_products", {"status": "success", "products": [{"product_id": "X1", "price": 49.99}]}),
        ("add_to_cart", {"status": "success", "product_id": "X1", "quantity": 2, "cart_total": 199.98}),
    ]
    assert agent.condition_holds("everything is over 50") is False
    assert agent.condition_holds("the item is under 100") is True
