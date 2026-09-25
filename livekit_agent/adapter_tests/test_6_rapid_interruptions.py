"""Manual/assert script: RAPID SUCCESSIVE INTERRUPTIONS with genuinely slow,
concurrently running async tools.

    Request A  ->  tool A starts (slow, asyncio.sleep)
    Correction B (before A finishes)  ->  tool B starts
    Correction C (before A/B finish)  ->  tool C starts

The tool executor is a real async backend: every issued call spawns a task
that sleeps and then feeds its result back through
TriageAdapter.on_tool_completed(), exactly like a live LiveKit function-tool.
Latencies are chosen so the stale results of A and B arrive AFTER C has
started (A is the slowest of all), while the adapter's tool_canceller is
deliberately "best-effort" — it records the cancel but does NOT stop the
backend task, so the stale results really do land on the agent.

Asserts:
  1. epoch/version strictly increases on every interruption (e0 < e1 < e2)
  2./3. A and B are stale: both are cancelled and no longer in-flight
  4. C is issued under the current epoch and is the only in-flight search
  5./6. A's and B's late results produce no speech, no slot contamination
        and no follow-up tool call (epoch guard)
  7. C's result is grounded on and drives the chained step (book_flight)
  8. only the final intent (Chicago) produces the state-changing action:
     exactly one book_flight, with C's flight_id
  9. duplicate state-changing actions are still prevented: re-requesting
     the identical booking (while pending and after success) issues nothing

Everything runs through TriageAdapter -> ParticipantAgent (no direct tool
registry calls). Tool schemas: the real 12-tool FDB-v3 manifest.

Run: python3 livekit_agent/adapter_tests/test_6_rapid_interruptions.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from livekit_agent.adapter import TriageAdapter  # noqa: E402
from livekit_agent.fdb_tools import FDB_TOOLS  # noqa: E402

# per-destination latency (seconds): A slowest so it lands last, after C.
LATENCY = {"Denver": 0.60, "Miami": 0.40, "Chicago": 0.15}
BOOK_LATENCY = 0.10


async def main():
    issued, cancelled, spoken, delivered = [], [], [], []
    backend_tasks = []
    adapter = None

    async def slow_backend(cid, api, args):
        """Artificial slow async tool. Ignores cancellation on purpose (a real
        remote API may already be executing), so stale results DO arrive."""
        if api == "search_flights":
            dest = args.get("destination")
            await asyncio.sleep(LATENCY.get(dest, 0.2))
            result = {"flights": [{"flight_id": f"FL-{dest.upper()}", "depart": "10:00", "price_usd": 300}]}
        else:
            await asyncio.sleep(BOOK_LATENCY)
            result = {"booking_id": f"BK-{args.get('flight_id')}", "flight_id": args.get("flight_id")}
        delivered.append((cid, api, args.get("destination") or args.get("flight_id"),
                          adapter.epoch, asyncio.get_running_loop().time()))
        await adapter.on_tool_completed(cid, result, status="ok")

    async def tool_executor(cid, api, args):
        issued.append((cid, api, dict(args), adapter.epoch, asyncio.get_running_loop().time()))
        backend_tasks.append(asyncio.create_task(slow_backend(cid, api, args)))

    async def tool_canceller(cid):
        cancelled.append(cid)  # best-effort only: backend keeps running

    async def speak(kind, text):
        spoken.append((kind, text))

    adapter = TriageAdapter(tool_executor=tool_executor, tool_canceller=tool_canceller, speak=speak)
    await adapter.start(FDB_TOOLS)
    agent = adapter.agent

    def searches():
        return [c for c in issued if c[1] == "search_flights"]

    # ---- Request A
    e0 = adapter.epoch
    await adapter.on_user_final("book a flight to Denver tomorrow for Ada Lovelace")
    await asyncio.sleep(0.05)
    assert len(searches()) == 1 and searches()[0][2]["destination"] == "Denver", f"A not issued: {issued}"
    cid_a = searches()[0][0]

    # ---- Correction B (A still running)
    await adapter.on_barge_in("actually make it Miami")
    await asyncio.sleep(0.05)
    e1 = adapter.epoch
    assert len(searches()) == 2 and searches()[1][2]["destination"] == "Miami", f"B not issued: {issued}"
    cid_b = searches()[1][0]

    # ---- Correction C (A and B still running)
    await adapter.on_barge_in("no wait, Chicago")
    await asyncio.sleep(0.02)
    e2 = adapter.epoch
    assert len(searches()) == 3 and searches()[2][2]["destination"] == "Chicago", f"C not issued: {issued}"
    cid_c = searches()[2][0]
    t_c_started = searches()[2][4]

    # 1. epoch strictly increases on every interruption
    assert e0 < e1 < e2, f"epoch did not increase per interruption: {e0}, {e1}, {e2}"
    # 2./3. A and B stale: cancelled, no longer in flight
    assert cid_a in cancelled and cid_b in cancelled, f"A/B not cancelled: {cancelled}"
    assert cid_a not in agent.inflight and cid_b not in agent.inflight
    # 4. C is current: only in-flight search, issued under the current epoch
    assert cid_c in agent.inflight and agent.inflight[cid_c]["version"] == adapter.epoch
    assert cid_c not in cancelled
    assert list(agent.inflight) == [cid_c], f"unexpected in-flight calls: {agent.inflight}"

    # let every backend task (A, B, C and the chained booking) finish
    await asyncio.sleep(max(LATENCY.values()) + BOOK_LATENCY + 0.3)
    await asyncio.gather(*backend_tasks, return_exceptions=True)
    await asyncio.sleep(0.05)

    by_cid = {d[0]: d for d in delivered}
    # stale results really arrived AFTER C started (not a vacuous test)
    assert cid_a in by_cid and cid_b in by_cid, f"stale results never delivered: {delivered}"
    assert by_cid[cid_a][4] > t_c_started and by_cid[cid_b][4] > t_c_started
    # ...and A (the slowest) arrived even after C's own result
    assert by_cid[cid_a][4] > by_cid[cid_c][4]

    all_text = " ".join(t for _, t in spoken)
    # 5./6. stale A/B produced no usable result
    assert "FL-DENVER" not in all_text and "FL-MIAMI" not in all_text, f"stale flight leaked: {spoken}"
    assert agent.state["slots"].get("destination") == "Chicago", agent.state["slots"]
    assert agent.state["slots"].get("flight_id") in (None, "FL-CHICAGO")

    books = [c for c in issued if c[1] == "book_flight"]
    # 7./8. only the final intent produced the state-changing action
    assert len(books) == 1, f"expected exactly one booking, got {books}"
    assert books[0][2]["flight_id"] == "FL-CHICAGO", f"booked a stale flight: {books}"
    assert books[0][2]["passenger_name"] == "Ada Lovelace", books
    assert books[0][3] == adapter.epoch, "booking not issued under the current epoch"
    assert "FL-CHICAGO" in all_text, f"C's result never grounded: {spoken}"

    # 9. duplicate state-changing actions still prevented (op ledger)
    n_before = len(issued)
    await agent.call("book_flight", dict(books[0][2]))
    await asyncio.sleep(0.05)
    assert len(issued) == n_before, f"duplicate booking issued after success: {issued[n_before:]}"

    # 9b. rapid duplicate WHILE pending (fresh op, never completed)
    adapter2_issued = []

    async def hold_exec(cid, api, args):
        adapter2_issued.append((cid, api))

    async def noop(*a):
        pass

    a2 = TriageAdapter(tool_executor=hold_exec, tool_canceller=noop, speak=noop)
    await a2.start(FDB_TOOLS)
    await asyncio.sleep(0.02)  # let ParticipantAgent consume the tool_manifest event
    for _ in range(3):
        await a2.agent.call("book_flight", {"flight_id": "FL-CHICAGO", "passenger_name": "Ada Lovelace"})
    await asyncio.sleep(0.05)
    assert len(adapter2_issued) == 1, f"pending duplicate not blocked: {adapter2_issued}"
    await a2.stop()

    await adapter.stop()

    print("epochs:", e0, e1, e2)
    print("issued:", [(c, a, x.get("destination") or x.get("flight_id"), ep) for c, a, x, ep, _ in issued])
    print("cancelled:", cancelled)
    print("delivered order:", [(c, a, d) for c, a, d, _, _ in sorted(delivered, key=lambda d: d[4])])
    print("spoken:", spoken)
    print("PASS: test_6_rapid_interruptions")


if __name__ == "__main__":
    asyncio.run(main())
