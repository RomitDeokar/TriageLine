"""Manual/assert script: user barges in with a REVISED destination right after
the agent's fast path has issued its first flight_search (ParticipantAgent's
"reasoning" — turn parsing -> tool dispatch — is synchronous, so this is the
earliest point an interruption can land: before the tool executor has done
any real work on the first call).

Asserts:
  - epoch (adapter.epoch / agent.version) increases across the interruption
  - the stale (Boston) call is cancelled via out_q "cancel_tool"
  - the tool call that follows carries the NEW destination (updated args)
  - no stale tool call for the old destination is reissued after revision

Run: python3 livekit_agent/adapter_tests/test_1_interrupt_while_reasoning.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from livekit_agent.adapter import TriageAdapter  # noqa: E402
from harness.mock_env import TOOL_REGISTRY  # noqa: E402

FLIGHT_TOOLS = {k: TOOL_REGISTRY[k] for k in ("flight_search", "book_flight")}


async def main():
    issued = []     # (call_id, api, args)
    cancelled = []  # call_id
    spoken = []

    async def tool_executor(cid, api, args):
        issued.append((cid, api, args))
        # Never resolves in this test — we only care that it was issued (with
        # what args) and, for the stale one, that it gets cancelled.

    async def tool_canceller(cid):
        cancelled.append(cid)

    async def speak(kind, text):
        spoken.append((kind, text))

    adapter = TriageAdapter(tool_executor=tool_executor, tool_canceller=tool_canceller, speak=speak)
    await adapter.start(FLIGHT_TOOLS)

    epoch_0 = adapter.epoch
    await adapter.on_user_final("book a flight to Boston")
    await asyncio.sleep(0.05)  # let the first flight_search dispatch land

    boston_call_id = issued[0][0] if issued else None

    # Interrupt with a revised destination while that first call is still "in flight"
    await adapter.on_barge_in("actually make it Chicago")
    await asyncio.sleep(0.2)  # allow cancel_where + redo issuance to flush

    epoch_1 = adapter.epoch

    await adapter.stop()

    print("epoch before:", epoch_0, "epoch after:", epoch_1)
    print("issued calls:", issued)
    print("cancelled:", cancelled)
    print("spoken:", spoken)

    assert epoch_1 > epoch_0, f"epoch did not increase on interruption: {epoch_0} -> {epoch_1}"
    assert boston_call_id in cancelled, "the stale Boston call was never cancelled"
    flight_calls = [c for c in issued if c[1] == "flight_search"]
    assert len(flight_calls) >= 2, "expected a second flight_search after revision"
    last_dest = flight_calls[-1][2].get("destination")
    assert last_dest == "Chicago", f"expected updated destination 'Chicago', got {last_dest!r}"
    reissued_boston = [c for c in flight_calls[1:] if c[2].get("destination") == "Boston"]
    assert not reissued_boston, "a stale Boston call was reissued after the revision"

    print("PASS: test_1_interrupt_while_reasoning")


if __name__ == "__main__":
    asyncio.run(main())
