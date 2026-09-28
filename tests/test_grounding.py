"""Result-to-speech grounding uses the call-time context, never the current global slots (audit #9)."""
from __future__ import annotations

import asyncio

from agent.agent import ParticipantAgent

TOOLS = {
    "get_weather": {"kind": "read_only", "description": "Weather for a city.",
                    "args": {"city": {"type": "string", "required": True}}},
    "get_exchange_rate": {"kind": "read_only", "description": "FX rate.",
                          "args": {"base": {"type": "string", "required": True},
                                   "target": {"type": "string", "required": True}}},
}


def _agent():
    a = ParticipantAgent(asyncio.Queue(), asyncio.Queue(), live=True)
    a.tools = dict(TOOLS)
    return a


def _spoken(a):
    out = []
    while not a.out_q.empty():
        x = a.out_q.get_nowait()
        if x["action"] in ("final_response", "filler_speech", "clarification_request"):
            out.append(x["payload"]["text"])
    return out


def test_read_result_grounded_on_call_args_not_current_slots():
    async def go():
        a = _agent()
        a.state["slots"]["destination"] = "Boston"
        cid = await a.call("get_weather", {"city": "Denver"})
        a.state["slots"]["destination"] = "Seattle"      # user corrected mid-flight
        await a.on_tool_result({"call_id": cid, "status": "success",
                                "result": {"city": "Denver", "temp_f": 60, "condition": "sunny"}})
        return " ".join(_spoken(a))
    said = asyncio.run(go())
    assert "Seattle" not in said


def test_unrelated_read_never_inherits_current_city():
    async def go():
        a = _agent()
        cid = await a.call("get_exchange_rate", {"base": "USD", "target": "EUR"})
        a.state["slots"]["destination"] = "Chicago"      # set by a later, unrelated turn
        await a.on_tool_result({"call_id": cid, "status": "success",
                                "result": {"base": "USD", "target": "EUR", "rate": 0.9}})
        return " ".join(_spoken(a))
    said = asyncio.run(go())
    assert "Chicago" not in said


def test_placeless_read_never_attaches_a_stale_or_current_city():
    TOOLS["get_traffic"] = {"kind": "read_only", "description": "Traffic on a route.",
                            "args": {"route": {"type": "string", "required": True}}}

    async def go():
        a = _agent()
        a.state["slots"]["destination"] = "Boston"
        cid = await a.call("get_traffic", {"route": "I-5"})
        a.state["slots"]["destination"] = "Seattle"
        await a.on_tool_result({"call_id": cid, "status": "success",
                                "result": {"route": "I-5", "delay_min": 12}})
        return " ".join(_spoken(a))
    said = asyncio.run(go())
    assert "Seattle" not in said and "Boston" not in said
