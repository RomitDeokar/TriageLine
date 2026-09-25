"""Regression test for audit B-05/B-09: a SLOW tool must never block the conversation.

Before the fix, TriageAdapter._pump_outputs awaited the tool executor inline, so while a
1 s mock call was running no filler, no cancel_tool and no new tool call could be emitted.
The interruption at +100 ms only surfaced at ~+1000 ms. This test uses an executor that
genuinely sleeps (like MockAPIRegistry under the "normal"/"slow" latency profiles).

Also checks utterance settling: two finals within the settle window are merged into ONE turn
instead of the second half being treated as a barge-in on the first.

Run: python3 livekit_agent/adapter_tests/test_7_nonblocking_slow_tool.py
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from livekit_agent.adapter import TriageAdapter  # noqa: E402
from livekit_agent.fdb_tools import FDB_TOOLS  # noqa: E402

SLOW_S = 1.0


async def slow_tool_does_not_block():
    t0 = time.monotonic()
    events = []
    adapter = None

    async def executor(cid, api, args):
        events.append(("call", cid, api, dict(args), time.monotonic() - t0))
        await asyncio.sleep(SLOW_S)                       # genuinely slow backend
        await adapter.on_tool_completed(cid, {"status": "success", "flights": [
            {"flight_id": "FL123", "destination": args.get("destination"), "date": args.get("date"), "price": 450.0}]})

    async def canceller(cid):
        events.append(("cancel", cid, time.monotonic() - t0))

    async def speak(kind, text):
        events.append(("speak", kind, text, time.monotonic() - t0))

    adapter = TriageAdapter(tool_executor=executor, tool_canceller=canceller, speak=speak)
    await adapter.start(FDB_TOOLS)
    await adapter.on_user_final("Find flights to Chicago on July 15.")
    await asyncio.sleep(0.1)
    await adapter.on_user_final("Actually, make it Denver instead.")
    await asyncio.sleep(0.3)                              # well before the slow call would finish

    cancels = [e for e in events if e[0] == "cancel"]
    calls = [e for e in events if e[0] == "call"]
    switch = [e for e in events if e[0] == "speak" and "Denver" in e[2]]
    assert cancels, f"stale call was not cancelled while the slow tool ran: {events}"
    assert cancels[0][2] < 0.6, f"cancel emitted only after the slow tool finished ({cancels[0][2]:.2f}s)"
    assert switch and switch[0][3] < 0.6, f"'switching' speech was blocked by the slow tool: {events}"
    assert len(calls) == 2 and calls[1][3]["destination"] == "Denver", f"re-issued call missing: {calls}"
    assert calls[1][4] < 0.6, "the replacement call was blocked behind the stale one"
    await asyncio.sleep(SLOW_S + 0.3)
    finals = [e for e in events if e[0] == "speak" and e[1] == "final_response"]
    assert finals and all("Chicago" not in e[2] for e in finals), f"stale result was grounded on: {finals}"
    await adapter.stop()
    print(f"  cancel at {cancels[0][2]*1000:.0f} ms, switch speech at {switch[0][3]*1000:.0f} ms (tool = {SLOW_S*1000:.0f} ms)")


async def settle_merges_split_finals():
    calls = []

    async def executor(cid, api, args):
        calls.append((api, dict(args)))

    async def noop(*_a):
        pass

    adapter = TriageAdapter(tool_executor=executor, tool_canceller=noop, speak=noop, settle_s=0.3)
    await adapter.start(FDB_TOOLS)
    await adapter.on_user_final("I need to fly to Seattle, um...")
    await asyncio.sleep(0.1)
    await adapter.on_user_final("on November 1st.")
    await asyncio.sleep(0.6)
    await adapter.stop()
    assert len(calls) == 1, f"split finals were not merged into one request: {calls}"
    assert calls[0][1].get("destination") == "Seattle" and "November 1" in calls[0][1].get("date", ""), calls


async def main():
    await slow_tool_does_not_block()
    await settle_merges_split_finals()
    print("PASS: test_7_nonblocking_slow_tool")


if __name__ == "__main__":
    asyncio.run(main())
