"""Edge cases from the 2026-09-26 review (C1-C4, B1-B8, B14): fragmented finals, corrections before /
during / after a call, spoken ids, ASR confusions, filler turns, dedup, teardown. Run: pytest tests/"""
import asyncio
import os
import random

import pytest

from tests._probe import drive, run
from agent import nlu

SPLIT = ["Hi, I want to track my order.", "Let me find the order number here.", "It's X Y Z eight eight."]


@pytest.fixture(autouse=True)
def _bench(monkeypatch):
    monkeypatch.setenv("TRIAGELINE_BENCHMARK_POLICY", "1")


def calls_of(calls, api):
    return [a for n, a in calls if n == api]


# ---------------------------------------------------------------- fragmented finals (C2)
@pytest.mark.parametrize("gap", [0.05, 0.12, 0.25])
def test_split_utterance_with_settle_gate(gap):
    # settle window (scaled 10x down) longer than the inter-fragment gap -> one merged, committed turn
    c, _ = run([(f, gap) for f in SPLIT], settle_s=0.3, tail=0.5)
    assert c == [("track_order", {"order_id": "XYZ88"})], c


def test_split_utterance_no_settle_never_uses_sentence_as_id():
    c, _ = run(["Hi, I want to track my order.", "Could you track it for me?", "It's B O B one two"])
    assert c == [("track_order", {"order_id": "BOB12"})], c


def test_split_into_five_fragments():
    frags = ["Search for", "um, 2 bedroom", "apartments in", "Denver", "under 2500."]
    c, _ = run([(f, 0.05) for f in frags], settle_s=0.15, tail=0.6)
    assert calls_of(c, "search_apartments") == [{"city": "Denver", "bedrooms": 2, "max_price": 2500}], c


def test_unfinished_turn_waits_longer():
    from livekit_agent.adapter import turn_looks_unfinished
    assert turn_looks_unfinished("Could you track my order and")
    assert turn_looks_unfinished("Let me find the order number, um")
    assert not turn_looks_unfinished("Track order BOB12.")


# ---------------------------------------------------------------- corrections (B3, C3)
def test_correction_split_across_finals_logs_once_with_gate():
    c, _ = run([("Find 2 bedroom apartments in Austin under 2000,", 0.05), ("actually 3 bedrooms", 0.05)],
               settle_s=0.2, tail=0.6)
    assert calls_of(c, "search_apartments") == [{"city": "Austin", "bedrooms": 3, "max_price": 2000}], c


def test_correction_after_completed_read_redoes_it():
    c, s = run([("Find 2 bedroom apartments in Austin under 2000", 0.3), ("actually 3 bedrooms", 0.3)])
    got = calls_of(c, "search_apartments")
    assert got[-1]["bedrooms"] == 3 and len(got) == 2, c
    assert not any("still on it" in t for _, t in s)


def test_same_read_twice_is_logged_once():
    c, _ = run([("Track order BOB12", 0.3), ("Track order BOB12", 0.3)])
    assert calls_of(c, "track_order") == [{"order_id": "BOB12"}], c


def test_same_state_change_twice_is_logged_once():
    c, _ = run([("Add 2 of item P52 to my cart", 0.3), ("Add 2 of item P52 to my cart", 0.3)])
    assert len(calls_of(c, "add_to_cart")) == 1, c


# ---------------------------------------------------------------- clarification answers (B1, B2)
@pytest.mark.parametrize("answer", ["A-B-C-1-2-3", "The order ID is A-B-C-1-2-3", "it's A B C one two three"])
def test_clarification_answered_bare_or_sentence(answer, monkeypatch):
    monkeypatch.setenv("TRIAGELINE_BENCHMARK_POLICY", "0")
    c, _ = run([("Can you track my order", 0.2), (answer, 0.2)])
    assert c == [("track_order", {"order_id": "ABC123"})], c


def test_clarification_answered_with_new_intent(monkeypatch):
    monkeypatch.setenv("TRIAGELINE_BENCHMARK_POLICY", "0")
    c, _ = run([("Can you track my order", 0.2), ("Actually, search for headphones under 100", 0.3)])
    assert calls_of(c, "search_products") and not calls_of(c, "track_order"), c


# ---------------------------------------------------------------- spoken ids / ASR confusions (C1, B6)
@pytest.mark.parametrize("spoken,want", [
    ("order number is x, y, z, eight, eight", "XYZ88"), ("item P five two", "P52"),
    ("flight DL double five five", "DL555"), ("item Kilo two", "K2"), ("order ID B O B one two", "BOB12")])
def test_spoken_alphanumerics(spoken, want):
    assert want in nlu.normalize_asr(spoken)


@pytest.mark.parametrize("heard,api,args", [
    ("add two of item P five two to my card", "add_to_cart", {"product_id": "P52", "quantity": 2}),
    ("the idea is 1, 2, 3, ABC, where's my package", "track_order", {"order_id": "123ABC"}),
    ("where is it? the origin number is x, y, c, eight, eight", "track_order", {"order_id": "XYC88"}),
    ("add, like, 2 of item P-5-2", "add_to_cart", {"product_id": "P52", "quantity": 2}),
])
def test_asr_confusions(heard, api, args):
    c, _ = run([heard])
    assert calls_of(c, api) == [args], c


def test_card_benefits_is_not_rewritten_to_cart():
    assert "card" in nlu.normalize_asr("what are the benefits on my platinum card")


# ---------------------------------------------------------------- filler / greeting / unknown (B4)
@pytest.mark.parametrize("t", ["um", "...", "uh huh", "hmm okay"])
def test_filler_only_turns_are_silent(t):
    c, s = run([t])
    assert c == [] and s == []


def test_greeting_reply_is_short():
    c, s = run(["Hi there"])
    assert c == [] and len(s) == 1 and len(s[0][1].split()) <= 8


def test_greeting_then_request():
    c, _ = run(["Hi! Track order BOB12 please."])
    assert c == [("track_order", {"order_id": "BOB12"})]


def test_unknown_domain_makes_no_call():
    c, s = run(["What's the weather on Mars?"])
    assert c == [] and s


# ---------------------------------------------------------------- benchmark policy (C4)
def test_benchmark_policy_calls_with_known_args():
    c, _ = run(["find me an apartment in Austin"])
    assert calls_of(c, "search_apartments") == [{"city": "Austin"}], c


# ---------------------------------------------------------------- chains (B7)
def test_three_step_chain_with_result_reference():
    c, _ = run(["Search for 2 bedroom apartments in Denver under 2500, then calculate the commute from whatever "
                "you find to Union Station, and update the search filter to pet friendly."], tail=1.0)
    assert [n for n, _ in c] == ["search_apartments", "calculate_commute", "update_search_filter"], c
    assert calls_of(c, "calculate_commute")[0]["origin_address"] == "APT1"


def test_two_filters_in_one_sentence():
    c, _ = run(["Set the filter for parking to true and laundry to in-unit."], tail=0.8)
    assert calls_of(c, "update_search_filter") == [{"filter_name": "parking", "value": True},
                                                   {"filter_name": "laundry", "value": "in-unit"}], c


@pytest.mark.parametrize("limit,added", [(50, False), (95, True)])
def test_conditional_branches_on_result(limit, added):
    c, _ = run([f"Search for headphones under 100 and if the first result is under {limit} add it to my cart."],
               tail=1.0)
    assert bool(calls_of(c, "add_to_cart")) is added, c


# ---------------------------------------------------------------- teardown (B8, B14)
def test_close_drops_buffered_text_no_late_call():
    async def go():
        from tests._probe import _registry
        from livekit_agent.adapter import TriageAdapter
        from livekit_agent.fdb_tools import FDB_TOOLS
        calls = []

        async def ex(cid, api, args):
            calls.append(api)

        async def noop(*a):
            pass
        ad = TriageAdapter(tool_executor=ex, tool_canceller=noop, speak=noop, settle_s=0.5)
        await ad.start(FDB_TOOLS)
        await ad.on_user_final("Track order BOB12")
        ad.close()
        ad.close()                       # idempotent
        await ad.flush()
        await ad.on_user_final("Track order XYZ88")
        await asyncio.sleep(0.7)
        await ad.stop()
        return calls
    assert asyncio.run(go()) == []


# ---------------------------------------------------------------- fuzz: no duplicate state change
def test_fuzz_no_duplicate_state_change_under_any_ordering():
    rnd = random.Random(7)
    pool = ["Add 2 of item P52 to my cart", "add two of item P five two to my cart", "actually just 2",
            "um", "Add 2 of item P52 to my cart please", "never mind"]
    for _ in range(12):
        frags = [(rnd.choice(pool), rnd.choice([0.0, 0.02, 0.1])) for _ in range(rnd.randint(2, 5))]
        c, _ = run(frags, tail=0.3)
        keys = [str(sorted(a.items())) for n, a in c if n == "add_to_cart"]
        assert len(keys) == len(set(keys)), (frags, c)
