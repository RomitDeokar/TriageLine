"""Regression tests for audit findings R06–R15, R17, R21 (docs/audit/AUDIT_R01-R22.md), Phase 2.

    python -m pytest -q tests/test_audit_r2.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_regressions import RENTAL, Rig, flights, run  # noqa: E402
from harness.mock_env import TOOL_REGISTRY  # noqa: E402
from agent import nlu  # noqa: E402

HOTEL = {"book_hotel": {"kind": "state_modifying", "description": "Book a hotel room in a city.",
                        "args": {"city": {"type": "string", "required": True},
                                 "nights": {"type": "integer", "required": True, "minimum": 1}}}}


def test_r06_clarified_passenger_name_wins():
    async def go():
        async with Rig() as r:
            await r.say("Find a flight to Boston and book it.")
            await r.result(r.calls("flight_search")[0], **flights("BOS"))
            await r.say("priya sharma")
            b = r.calls("book_flight")
            assert b and b[-1]["args"]["passenger_name"] == "Priya Sharma"
    run(go())


def test_r06_time_clarification_selects_without_loop():
    async def go():
        async with Rig() as r:
            await r.say("Find a flight to Boston at 2:45 pm and book it for Alice.")
            await r.result(r.calls("flight_search")[0], **flights("BOS"))
            n = len(r.calls("flight_search"))
            await r.say("2 PM")
            assert len(r.calls("flight_search")) == n          # no re-search loop
            b = r.calls("book_flight")
            assert b and b[-1]["args"]["flight_id"] == "FL-BOS-2PM"
    run(go())


def test_r07_buffer_before_correction_cannot_restore_stale_intent():
    async def go():
        async with Rig() as r:
            await r.send("user_speech_chunk", text="Book a flight to Boston for Alice.", end_of_turn=False)
            await r.send("interruption", text="Actually make it Seattle.")
            await r.send("user_speech_chunk", text="please.", end_of_turn=True)
            dests = [c["args"].get("destination") for c in r.calls("flight_search")]
            assert "Boston" not in dests
    run(go())


def test_r09_origin_correction():
    async def go():
        async with Rig() as r:
            await r.say("Find flights from Seattle to Boston.")
            await r.send("interruption", text="Actually from Chicago.")
            assert r.agent.state["slots"].get("origin") == "Chicago"
    run(go())


def test_r09_enum_correction_on_state_change():
    async def go():
        async with Rig(tools={**TOOL_REGISTRY, **RENTAL}) as r:
            await r.say("Reserve a rental car in Denver, compact class.")
            c1 = r.calls("reserve_rental_car")[0]
            await r.send("interruption", text="Actually make it SUV.")
            assert c1["call_id"] in r.cancels()
            assert r.calls("reserve_rental_car")[-1]["args"]["car_class"] == "suv"
    run(go())


def test_r10_lowercase_name_never_reuses_old_passenger():
    async def go():
        async with Rig() as r:
            await r.say("Find a flight to Boston and book it for Alice.")
            await r.result(r.calls("flight_search")[0], **flights("BOS"))
            await r.result(r.calls("book_flight")[0], booking_id="BK-0001", flight_id="FL-BOS-8AM")
            await r.say("Please find and book a flight to Seattle for bob smith.")
            await r.result(r.calls("flight_search")[-1], **flights("SEA"))
            b = r.calls("book_flight")
            assert b[-1]["args"]["passenger_name"] == "Bob Smith"
    run(go())


def test_r11_new_booking_request_after_search():
    async def go():
        async with Rig() as r:
            await r.say("Find flights to Boston.")
            await r.result(r.calls("flight_search")[0], **flights("BOS"))
            await r.say("Book a flight to Seattle for Alice.")
            await r.result(r.calls("flight_search")[-1], **flights("SEA"))
            assert r.calls("book_flight")
    run(go())


def test_r12_cheapest_and_ordinal_selection():
    async def go():
        async with Rig() as r:
            await r.say("Find and book the cheapest flight to Boston for Alice.")
            await r.result(r.calls("flight_search")[0], **flights("BOS"))
            assert r.calls("book_flight")[-1]["args"]["flight_id"] == "FL-BOS-2PM"
        async with Rig() as r:
            await r.say("Find flights to Boston.")
            await r.result(r.calls("flight_search")[0], **flights("BOS"))
            await r.say("Book the second one for Alice.")
            b = r.calls("book_flight")
            assert b and b[-1]["args"]["flight_id"] == "FL-BOS-2PM"
    run(go())


def test_r13_city_extraction():
    assert nlu.extract_city("Flights from Boston to Kochi") == "Kochi"
    assert nlu.extract_origin("Flights from Kochi to Boston") == "Kochi"
    assert nlu.extract_city("Find flights to san jose") == "San Jose"


def test_r14_enum_self_correction():
    args, _ = nlu.build_args(RENTAL["reserve_rental_car"], "Reserve compact, actually SUV in Denver.", {})
    assert args["car_class"] == "suv"


def test_r15_schema_validation_rejects_fraction():
    args, missing = nlu.build_args(HOTEL["book_hotel"], "Book a hotel in Denver for 0.5 nights", {})
    assert "nights" not in args and "nights" in missing
    ok, _ = nlu.build_args(HOTEL["book_hotel"], "Book a hotel in Denver for 2 nights", {})
    assert ok["nights"] == 2


def test_r21_live_repeat_request_gets_ack():
    async def go():
        async with Rig(live=True) as r:
            await r.say("Find flights to Boston.")
            await r.result(r.calls("flight_search")[0], **flights("BOS"))
            n = len(r.spoken("filler_speech"))
            await r.say("Find flights to Boston.")
            # C3: an identical read-only call is never logged twice in a session (strict scorer);
            # the repeat still gets an immediate acknowledgement and the cached answer
            assert len(r.calls("flight_search")) == 1
            assert len(r.spoken("filler_speech")) == n + 1
    run(go())


# ====================================================================== Phase 3: live / perception
def test_r17_lookup_uses_frame_at_question_time():
    async def go():
        async with Rig() as r:
            r.agent.frame = {"image_ref": "x"}
            r.agent.frame_seq = 3
            r.agent.vision = {"label": "HDMI port", "confidence": 0.9, "frame_seq": 1}   # stale cached result
            r.agent.waiting_vision = {"turn": "what is this port", "visual": True, "version": r.agent.version,
                                      "frame_seq": 3, "token": 99}
            await r.send("_internal", kind="vision_done", frame_seq=3,
                         vis={"label": "Ethernet port", "confidence": 0.9, "embedding": None, "source": "ocr"})
            q = r.calls("lookup_manual")[-1]["args"]["query"]
            assert "Ethernet" in q and "HDMI" not in q
    run(go())


def test_r19_clarification_answer_during_speech():
    async def go():
        async with Rig() as r:
            await r.say("Find a flight to Boston and book it.")
            await r.result(r.calls("flight_search")[0], **flights("BOS"))
            await r.send("interruption", text="priya sharma")      # barge-in while the question plays
            b = r.calls("book_flight")
            assert b and b[-1]["args"]["passenger_name"] == "Priya Sharma"
    run(go())


def test_r20_provider_exception_reaches_terminal_state():
    import asyncio
    from ui import live as L

    async def go():
        sess = L.LiveSession.__new__(L.LiveSession)
        sess.pending, sess.events = {"c1": None}, []
        sess.in_q = asyncio.Queue()
        sess.emit = lambda kind, **kw: sess.events.append((kind, kw))

        class Boom:
            mode = "mock"

            async def execute(self, api, args):
                raise RuntimeError("secret-token-xyz")
        sess.tools = Boom()
        await sess._exec("c1", "book_flight", {"flight_id": "FL-1", "passenger_name": "A"})
        assert "c1" not in sess.pending
        ev = sess.in_q.get_nowait()
        assert ev["payload"]["status"] == "error" and ev["payload"]["result"]["error"] == "provider_exception"
        assert "secret" not in str(sess.events)
    asyncio.run(go())


def test_r20_provider_exception_on_booking_is_unknown():
    async def go():
        async with Rig() as r:
            await r.say("Find a flight to Boston and book it for Alice.")
            await r.result(r.calls("flight_search")[0], **flights("BOS"))
            b = r.calls("book_flight")[0]
            await r.result(b, status="error", error="provider_exception")
            assert r.agent.ledger.for_call(b["call_id"])["status"] == "unknown"
    run(go())


def test_r18_live_audio_goes_through_uncertainty_gate():
    from ui import live as L
    src = open(L.__file__).read()
    assert '"user_audio_chunk"' in src and "sess.user_text(r[\"text\"]" not in src
