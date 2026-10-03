"""Regression tests for turn/repair fixes: no city leaking into an *_address field, elliptical
free-text corrections, and a trailing filler repair word not being treated as a turn boundary.
Synthetic values only (never benchmark items)."""
import asyncio

from agent import nlu
from livekit_agent.fdb_tools import FDB_TOOLS


def test_city_context_does_not_leak_into_address_field():
    """An *_address arg must never fall through to the generic city/destination role match."""
    spec = FDB_TOOLS["calculate_commute"]["args"]["destination_address"]
    got = nlu._arg_for("destination_address", spec, "how long is the commute", {"destination": "Faketown"})
    assert got is None, got
    ospec = FDB_TOOLS["calculate_commute"]["args"]["origin_address"]
    assert nlu._arg_for("origin_address", ospec, "and then", {"city": "Faketown"}) is None


def test_address_field_still_extracts_a_real_place():
    spec = FDB_TOOLS["calculate_commute"]["args"]["destination_address"]
    assert nlu._arg_for("destination_address", spec, "walk from Elm Road to the depot", {}) == "the depot"


def test_elliptical_free_text_correction_replaces_query():
    assert nlu.extract_query("search for a desk under 240 actually a lamp instead") == "lamp"
    assert nlu.extract_query("find a monitor, actually a printer") == "printer"


def test_contrast_clause_is_not_returned_verbatim():
    # "not a laptop, a tablet" -> the cue scan resolves the query, not the raw tail
    assert nlu.extract_query("find a tablet for me, actually not a laptop, a tablet") == "tablet"


def test_slot_correction_is_not_a_query():
    # "make it X" targets a place/date slot, never the product query
    assert nlu.extract_query("Actually make it Tuesday.") is None
    assert nlu.extract_query("actually under 180") is None


def test_trailing_filler_repair_word_is_not_a_turn_boundary():
    # the trailing "instead" must not hide the real repair marker ("actually")
    assert nlu._repaired_tail("search for a desk actually a lamp instead").strip() == "a lamp instead"


def test_late_correction_fragment_issues_the_corrected_query():
    """Fragment 1 commits a search; a correction fragment must revise to the corrected value."""
    async def main():
        calls = []

        async def execute(cid, api, args):
            calls.append((api, dict(args)))
            await ad.on_tool_completed(cid, {"status": "success",
                                             "products": [{"product_id": "P1", "price": 30}]}
                                       if api == "search_products" else {"status": "success"}, status="ok")

        async def speak(kind, text):
            pass

        async def cancel(cid):
            pass

        from livekit_agent.adapter import TriageAdapter

        ad = TriageAdapter(tool_executor=execute, tool_canceller=cancel, speak=speak,
                           settle_s=0.0, max_settle_s=0.0)
        await ad.start(FDB_TOOLS)
        await ad.on_user_final("search for a desk under 240")
        await asyncio.sleep(0.3)
        # a barge-in correction carries the whole request (as the live adapter would after merging);
        # the free-text revise path must replace the query and keep the untouched max_price
        await ad.on_barge_in("search for a desk under 240 actually a lamp instead")
        await asyncio.sleep(0.3)
        await ad.wait_idle(timeout=5)
        await ad.stop()
        return calls

    calls = asyncio.run(main())
    assert any(c[0] == "search_products" and c[1].get("query") == "lamp" for c in calls), calls
