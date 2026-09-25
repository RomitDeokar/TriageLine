"""Regression tests for the independent audit, findings R01–R22 (docs/audit/AUDIT_R01-R22.md).

Every test drives the real ParticipantAgent through its two queues and injects tool results by hand,
so each race is deterministic. Each test is named after the finding it pins.

    python -m pytest -q tests/test_audit_r.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_regressions import RENTAL, Rig, flights, run  # noqa: E402
from harness.mock_env import TOOL_REGISTRY  # noqa: E402
from agent import nlu  # noqa: E402
from agent import perception as P  # noqa: E402

WEATHER = {"weather_lookup": {"kind": "read_only", "description": "Current weather for a city.",
                              "args": {"city": {"type": "string", "required": True}}}}


def joined(r):
    return " | ".join(r.spoken()).lower()


# ====================================================================== Phase 1: side-effect safety
def test_r01_revoking_booking_permission_stops_plan():
    async def go():
        async with Rig() as r:
            await r.say("Find flights to Boston and book for Alice.")
            s = r.calls("flight_search")[0]
            await r.send("interruption", text="Actually don't book, only show options.")
            await r.result(s, **flights("BOS"))
            assert not r.calls("book_flight")
            assert "fl-bos" in r.spoken("final_response")[-1].lower()     # options still shown
    run(go())


def test_r01_revocation_as_normal_turn_and_empty_plan_is_authoritative():
    async def go():
        async with Rig() as r:
            await r.say("Find flights to Boston and book for Alice.")
            s = r.calls("flight_search")[0]
            await r.say("Actually don't book anything, just show me the options.")
            assert r.agent.plan == []
            await r.result(s, **flights("BOS"))
            assert not r.calls("book_flight")
    run(go())


def test_r01_revocation_while_booking_is_running_cancels_it():
    async def go():
        async with Rig() as r:
            await r.say("Find flights to Boston and book for Alice.")
            await r.result(r.calls("flight_search")[0], **flights("BOS"))
            b = r.calls("book_flight")[0]
            await r.send("interruption", text="Wait, don't book it.")
            assert b["call_id"] in r.cancels()
    run(go())


def test_r02_negated_ticket_and_cancel_not_executed():
    async def go():
        for text, api in (("Do not open a support ticket for my broken TV.", "create_support_ticket"),
                          ("Do not cancel booking BK-0001.", "cancel_booking"),
                          ("Please don't cancel my booking BK-0001.", "cancel_booking")):
            async with Rig() as r:
                await r.say(text)
                assert not r.calls(api), text
                assert r.spoken(), text                                   # never silent
    run(go())


def test_r02_negated_dynamic_state_modifying_tool():
    async def go():
        async with Rig(tools={**TOOL_REGISTRY, **RENTAL}) as r:
            await r.say("Don't reserve a rental car in Denver, compact class.")
            assert not r.calls("reserve_rental_car")
    run(go())


def test_r02_positive_requests_still_work():
    async def go():
        async with Rig() as r:
            await r.say("Cancel booking BK-0001.")
            assert r.calls("cancel_booking")
        async with Rig() as r:
            await r.say("My TV is broken, please open a support ticket.")
            assert r.calls("create_support_ticket")
    run(go())


def test_r03_late_commit_after_cancel_is_reconciled_and_disclosed():
    async def go():
        async with Rig() as r:
            await r.say("Find a flight to Boston and book it for Alice.")
            await r.result(r.calls("flight_search")[0], **flights("BOS"))
            bos_book = r.calls("book_flight")[0]
            await r.send("interruption", text="Actually make it Seattle.")
            assert bos_book["call_id"] in r.cancels()
            # the provider had already committed: the late success must be recorded and disclosed
            await r.result(bos_book, booking_id="BK-0001", flight_id="FL-BOS-8AM")
            rec = r.agent.ledger.for_call(bos_book["call_id"])
            assert rec["status"] == "committed"
            assert "bk-0001" in joined(r)
            sea = r.calls("flight_search")[-1]
            await r.result(sea, **flights("SEA"))
            # no silent second active booking
            assert len(r.calls("book_flight")) == 1
            assert r.spoken("clarification_request")
            assert not any("stopped the earlier booking" in s.lower() for s in r.spoken())
            # the user confirms: cancel the old one first, then book the replacement
            await r.say("Yes, cancel it and book Seattle.")
            cb = r.calls("cancel_booking")
            assert cb and cb[-1]["args"]["booking_id"] == "BK-0001"
            await r.result(cb[-1], cancelled="BK-0001")
            b2 = r.calls("book_flight")
            assert len(b2) == 2 and b2[-1]["args"]["flight_id"] == "FL-SEA-8AM"
    run(go())


def test_r03_live_mode_language_is_truthful():
    async def go():
        async with Rig(live=True) as r:
            await r.say("Find a flight to Boston and book it for Alice.")
            await r.result(r.calls("flight_search")[0], **flights("BOS"))
            await r.send("interruption", text="Actually make it Seattle.")
            text = joined(r)
            assert "stopped the earlier booking" not in text
            # the provider never confirmed the cancel → the replacement booking is held
            await r.result(r.calls("flight_search")[-1], **flights("SEA"))
            assert len(r.calls("book_flight")) == 1
    run(go())


def test_r03_confirmed_cancel_allows_replacement_in_live_mode():
    async def go():
        async with Rig(live=True) as r:
            await r.say("Find a flight to Boston and book it for Alice.")
            await r.result(r.calls("flight_search")[0], **flights("BOS"))
            b = r.calls("book_flight")[0]
            await r.send("interruption", text="Actually make it Seattle.")
            await r.send("tool_cancelled", call_id=b["call_id"], confirmed=True)
            await r.result(r.calls("flight_search")[-1], **flights("SEA"))
            assert r.calls("book_flight")[-1]["args"]["flight_id"] == "FL-SEA-8AM"
    run(go())


def test_r04_unknown_outcome_not_retried_by_repetition():
    async def go():
        async with Rig(tools={**TOOL_REGISTRY, **RENTAL}, live=True) as r:
            req = "Reserve a rental car in Denver, compact class."
            await r.say(req)
            assert len(r.calls("reserve_rental_car")) == 1
            await r.say("Never mind.")
            await r.say(req)
            await r.say(req)
            await r.say(req)
            assert len(r.calls("reserve_rental_car")) == 1            # repetition never bypasses
            first = r.agent.ledger.history[0]
            assert first["status"] in ("unknown", "cancel_requested")
            await r.say("Yes, try again.")                            # explicit, confirmed decision
            calls = r.calls("reserve_rental_car")
            assert len(calls) == 2
            second = r.agent.ledger.for_call(calls[-1]["call_id"])
            assert second["supersedes"] == first["op_id"]              # original identity preserved
            assert first["status"] in ("unknown", "cancel_requested")
    run(go())


def test_r04_timeout_on_state_change_is_unknown_not_failed():
    async def go():
        async with Rig(tools={**TOOL_REGISTRY, **RENTAL}) as r:
            req = "Reserve a rental car in Denver, compact class."
            await r.say(req)
            await r.result(r.calls("reserve_rental_car")[0], status="error", error="timeout")
            assert r.agent.ledger.history[0]["status"] == "unknown"
            assert "unable to finish" not in joined(r)
            await r.say(req)
            assert len(r.calls("reserve_rental_car")) == 1
    run(go())


def test_r05_rebooking_after_confirmed_cancellation():
    async def go():
        async with Rig() as r:
            await r.say("Find a flight to Boston and book it for Alice.")
            await r.result(r.calls("flight_search")[0], **flights("BOS"))
            b1 = r.calls("book_flight")[0]
            await r.result(b1, booking_id="BK-0001", flight_id="FL-BOS-8AM")
            await r.say("Cancel booking BK-0001.")
            await r.result(r.calls("cancel_booking")[0], cancelled="BK-0001")
            assert r.agent.ledger.for_call(b1["call_id"])["status"] == "reversed"
            await r.say("Find a flight to Boston and book it for Alice.")
            await r.result(r.calls("flight_search")[-1], **flights("BOS"))
            assert len(r.calls("book_flight")) == 2
            assert "already" not in r.spoken()[-1].lower()
    run(go())


def test_r16_malformed_success_is_not_claimed_done():
    async def go():
        async with Rig() as r:
            await r.say("Find a flight to Boston and book it for Alice.")
            await r.result(r.calls("flight_search")[0], **flights("BOS"))
            b = r.calls("book_flight")[0]
            await r.result(b)                                          # "success" without a booking id
            fin = r.spoken()[-1].lower()
            assert "done" not in fin and "none" not in fin and "you're booked" not in fin
            assert r.agent.ledger.for_call(b["call_id"])["status"] == "unknown"
            await r.say("Find a flight to Boston and book it for Alice.")
            await r.result(r.calls("flight_search")[-1], **flights("BOS"))
            assert len(r.calls("book_flight")) == 1                    # no automatic duplicate
    run(go())


def test_r16_malformed_ticket_and_cancel():
    async def go():
        async with Rig() as r:
            await r.say("My TV is broken, please open a support ticket.")
            await r.result(r.calls("create_support_ticket")[0])
            assert "ticket none" not in joined(r) and "i've opened" not in joined(r)
        async with Rig() as r:
            await r.say("Cancel booking BK-0001.")
            await r.result(r.calls("cancel_booking")[0])
            assert "has been cancelled" not in joined(r)
    run(go())


def test_r22_completion_bound_to_originating_task():
    async def go():
        async with Rig(tools={**TOOL_REGISTRY, **WEATHER}) as r:
            await r.say("Find a flight to Boston and book it for Alice.")
            await r.result(r.calls("flight_search")[0], **flights("BOS"))
            b = r.calls("book_flight")[0]
            await r.say("What's the weather in Seattle?")
            w = r.calls("weather_lookup")
            assert w and w[0]["args"]["city"] == "Seattle"
            await r.result(b, booking_id="BK-0001", flight_id="FL-BOS-8AM")
            fin = [s for s in r.spoken("final_response") if "bk-0001" in s.lower()][-1]
            assert "Boston" in fin and "Seattle" not in fin
            await r.result(w[0], temp_f=61, conditions="cloudy")
            assert "Seattle" in r.spoken("final_response")[-1]
    run(go())
