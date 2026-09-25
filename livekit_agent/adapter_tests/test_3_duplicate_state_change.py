"""Manual/assert script: the same state-modifying call (book_flight with the
same flight_id + passenger_name) is requested twice — once while the first
attempt is still pending, and once after it has succeeded.

Asserts:
  - the second request while status=="pending" is blocked (no second
    tool_call issued) — ParticipantAgent.call()'s op-ledger check
    (agent/agent.py:205-221)
  - once the first booking succeeds, a third identical request is also
    blocked and answered from the cached result, not re-issued
  - only ONE actual book_flight tool call ever reaches the executor

Run: python3 livekit_agent/adapter_tests/test_3_duplicate_state_change.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from livekit_agent.adapter import TriageAdapter  # noqa: E402
from harness.mock_env import TOOL_REGISTRY  # noqa: E402

TOOLS = {k: TOOL_REGISTRY[k] for k in ("flight_search", "book_flight")}


async def main():
    issued = []
    spoken = []

    async def tool_executor(cid, api, args):
        issued.append((cid, api, args))
        # book_flight resolves only when the test explicitly completes it
        # (via adapter.on_tool_completed) — simulates a real in-flight call.

    async def tool_canceller(cid):
        pass

    async def speak(kind, text):
        spoken.append((kind, text))

    adapter = TriageAdapter(tool_executor=tool_executor, tool_canceller=tool_canceller, speak=speak)
    await adapter.start(TOOLS)

    # Seed slots directly (bypassing flight_search) so we can call book_flight
    # for the exact same args twice, deterministically.
    adapter.agent.state["slots"]["flight_id"] = "FL-42"
    adapter.agent.state["slots"]["passenger_name"] = "Ada Lovelace"
    adapter.agent.tools = TOOLS
    adapter.agent.last_turn = "book it"

    async def request_booking():
        await adapter.agent.call("book_flight", {"flight_id": "FL-42", "passenger_name": "Ada Lovelace"},
                                  deps={"flight_id": "FL-42"})

    # 1) First booking request — should issue a real tool_call.
    await request_booking()
    await asyncio.sleep(0.05)
    book_calls_after_first = [c for c in issued if c[1] == "book_flight"]
    assert len(book_calls_after_first) == 1, "first booking request did not issue a tool call"
    first_cid = book_calls_after_first[0][0]

    # 2) Identical request while the first is still pending (no result yet).
    spoken_before_dup1 = list(spoken)
    await request_booking()
    await asyncio.sleep(0.05)
    assert len([c for c in issued if c[1] == "book_flight"]) == 1, \
        "a duplicate book_flight call was issued while the first was still pending"
    assert any("already working on that" in t for _, t in spoken[len(spoken_before_dup1):]), \
        "expected a 'still pending, not repeating' filler for the in-flight duplicate"

    # 3) Complete the first booking.
    await adapter.on_tool_completed(first_cid, {"booking_id": "BK-100"}, status="ok")
    await asyncio.sleep(0.05)

    # 4) Identical request again, now that it has succeeded.
    spoken_before_dup2 = list(spoken)
    await request_booking()
    await asyncio.sleep(0.05)

    await adapter.stop()

    book_calls_final = [c for c in issued if c[1] == "book_flight"]
    print("all book_flight calls issued:", book_calls_final)
    print("spoken:", spoken)

    assert len(book_calls_final) == 1, \
        f"book_flight was re-issued for an already-succeeded identical request: {book_calls_final}"
    assert any("already done" in t for _, t in spoken[len(spoken_before_dup2):]), \
        "expected an 'already done' response for the post-success duplicate"

    print("PASS: test_3_duplicate_state_change")


if __name__ == "__main__":
    asyncio.run(main())
