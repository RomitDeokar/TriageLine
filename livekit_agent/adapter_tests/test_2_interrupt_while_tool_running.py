"""Manual/assert script: the flight_search call is genuinely "running" (its
result has not come back yet) when the user interrupts and switches intent
entirely (an intent SWITCH, not a slot revision). The stale call's result
then arrives late, after the epoch has moved on.

Asserts:
  - epoch increases across the interruption
  - cancel_tool is sent for the stale in-flight call
  - when the stale result is fed back via on_tool_completed AFTER the switch,
    ParticipantAgent drops it silently (never speaks about Boston flights,
    never contaminates the new state) — this is the epoch-guard: a call
    popped by cancel_where() is gone from `inflight`, so on_tool_result()
    returns immediately (agent/agent.py:692-696)
  - the new (support-ticket) intent proceeds independently

Run: python3 livekit_agent/adapter_tests/test_2_interrupt_while_tool_running.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from livekit_agent.adapter import TriageAdapter  # noqa: E402
from harness.mock_env import TOOL_REGISTRY  # noqa: E402

TOOLS = {k: TOOL_REGISTRY[k] for k in ("flight_search", "book_flight", "create_support_ticket")}


async def main():
    issued = []
    cancelled = []
    spoken = []

    async def tool_executor(cid, api, args):
        issued.append((cid, api, args))

    async def tool_canceller(cid):
        cancelled.append(cid)

    async def speak(kind, text):
        spoken.append((kind, text))

    adapter = TriageAdapter(tool_executor=tool_executor, tool_canceller=tool_canceller, speak=speak)
    await adapter.start(TOOLS)

    epoch_0 = adapter.epoch
    await adapter.on_user_final("book a flight to Boston")
    await asyncio.sleep(0.05)
    boston_call_id = issued[0][0]
    assert issued[0][1] == "flight_search"

    # Tool is genuinely still running (no result yet) when the user switches
    # intent entirely — a different domain, not a slot tweak.
    await adapter.on_barge_in("forget the flight, open a support ticket for my TV instead")
    await asyncio.sleep(0.2)

    epoch_1 = adapter.epoch
    assert boston_call_id in cancelled, "in-flight flight_search was not cancelled on switch"

    spoken_before_late_result = list(spoken)

    # The stale flight_search result finally lands, AFTER the switch/cancel.
    await adapter.on_tool_completed(
        boston_call_id,
        {"flights": [{"flight_id": "FL-1", "depart": "10:00", "price_usd": 200}]},
        status="ok",
    )
    await asyncio.sleep(0.1)

    await adapter.stop()

    print("epoch before:", epoch_0, "epoch after switch:", epoch_1)
    print("issued:", issued)
    print("cancelled:", cancelled)
    print("spoken:", spoken)

    assert epoch_1 > epoch_0, "epoch did not increase on intent switch"
    # The stale result must not have produced any NEW speech about it.
    assert spoken == spoken_before_late_result, \
        f"stale flight_search result was not silently dropped: {spoken[len(spoken_before_late_result):]}"
    new_speech_after_switch = spoken[len(spoken_before_late_result):]
    assert not any("Boston" in t or "FL-1" in t for _, t in new_speech_after_switch), \
        "stale Boston/FL-1 content leaked into agent speech after the switch"
    ticket_calls = [c for c in issued if c[1] == "create_support_ticket"]
    assert ticket_calls, "expected create_support_ticket to have been issued after the switch"

    print("PASS: test_2_interrupt_while_tool_running")


if __name__ == "__main__":
    asyncio.run(main())
