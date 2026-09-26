"""Shared helper: drive TriageAdapter with finals (optionally split) against the official mocks."""
import asyncio, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "livekit_agent"))
from livekit_agent.adapter import TriageAdapter
from livekit_agent.fdb_tools import FDB_TOOLS


def _registry():
    import importlib
    m = importlib.import_module("mock_apis")
    return m.MockAPIRegistry() if hasattr(m, "MockAPIRegistry") else m


async def drive(fragments, gap=0.05, settle_s=0.0, tail=0.6, commit=False):
    """fragments: list of str (one final each) or (str, gap_after)."""
    reg = _registry()
    calls, spoken = [], []

    async def execute(cid, api, args):
        try:
            res = reg.call(api, **args)
        except Exception as e:  # noqa: BLE001
            res = {"status": "error", "error": "invalid_args", "message": str(e)}
        calls.append((api, dict(args)))
        if isinstance(res, dict) and "status" not in res:
            res = {"status": "success", **res}
        st = "error" if isinstance(res, dict) and res.get("status") == "error" else "ok"
        asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(ad.on_tool_completed(cid, res, status=st)))

    async def cancel(cid):
        pass

    async def speak(kind, text):
        spoken.append((kind, text))

    ad = TriageAdapter(tool_executor=execute, tool_canceller=cancel, speak=speak, settle_s=settle_s)
    await ad.start(FDB_TOOLS)
    for f in fragments:
        text, g = (f, gap) if isinstance(f, str) else f
        await ad.on_user_final(text)
        await asyncio.sleep(g)
    await ad.flush()
    await asyncio.sleep(tail)
    await ad.stop()
    return calls, spoken


def run(fragments, **kw):
    return asyncio.run(drive(fragments, **kw))


if __name__ == "__main__":
    c, s = run(sys.argv[1:])
    print(c); print(s)
