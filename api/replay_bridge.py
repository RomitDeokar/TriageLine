"""Demo/replay integration: drive the existing replay harness live.

docs/UI_SPEC.md section 3 gives the UI two WebSocket endpoints: one for a
live call, one for harness runs (`/ws/replay/{run_id}`). Phase 7A has no
real call pipeline yet (that's transport/dialogue, still to be wired to
LiveKit/Deepgram/Groq/Rime), so the only thing that can drive a live event
stream right now is `harness.replay.run_scenario` -- the *exact* decision
code (DeliberationEngine + CommitStateMachine) the eventual live agent
will use, per docs/ARCHITECTURE.md's "replay harness is a first-class
consumer of core, not a hack."

This module does not reimplement or fork that harness. It only:
  1. looks a scenario up by id from the existing scenario library, and
  2. runs it via the existing `run_scenario(..., on_event=...)` hook,
     relaying each event to WebSocket clients as it happens instead of
     only after the run completes.

`run_scenario` is synchronous (it wraps its own `asyncio.run`), so it is
run in a worker thread via `asyncio.to_thread` -- calling it directly from
an async FastAPI request handler would raise ("cannot run the event loop
while another loop is running"). Events produced on that worker thread are
handed back to the request handler's event loop with
`asyncio.run_coroutine_threadsafe`, which is the standard, minimal way to
bridge a background thread back into a specific asyncio loop.
"""

from __future__ import annotations

import asyncio
from typing import Any

from api.websocket import ConnectionManager
from harness.replay import ReplayResult, run_scenario
from harness.scenario import Scenario
from harness.scenarios.definitions import required_scenarios

VALID_AGENT_TYPES = ("deliberative", "naive")


def available_demo_scenarios() -> dict[str, Scenario]:
    """The Phase 6B/6C required scenario set, keyed by scenario_id.

    Reuses `harness.scenarios.definitions.required_scenarios()` directly --
    no second scenario set is invented for the demo endpoint.
    """

    return {scenario.scenario_id: scenario for scenario in required_scenarios()}


class UnknownScenarioError(ValueError):
    pass


class UnknownAgentTypeError(ValueError):
    pass


async def run_demo_scenario(
    manager: ConnectionManager,
    channel: str,
    scenario_id: str,
    agent_type: str = "deliberative",
) -> ReplayResult:
    """Run one existing scenario through one existing agent strategy,
    relaying its real event trace to every WebSocket client on `channel`
    as it is produced.

    Returns the same `ReplayResult` `run_scenario` always returns (also
    already available afterward via `result.event_trace`), so a client
    that connects late, or not at all, can still be served the full trace
    over the demo run's HTTP response.
    """

    scenarios = available_demo_scenarios()
    scenario = scenarios.get(scenario_id)
    if scenario is None:
        raise UnknownScenarioError(
            f"Unknown scenario_id {scenario_id!r}; available: {sorted(scenarios)}"
        )
    if agent_type not in VALID_AGENT_TYPES:
        raise UnknownAgentTypeError(
            f"Unknown agent_type {agent_type!r}; must be one of {VALID_AGENT_TYPES}"
        )

    loop = asyncio.get_running_loop()

    def on_event(payload: dict[str, Any]) -> None:
        # Called from the worker thread run_scenario executes on -- hand
        # the broadcast back to *this* request's event loop rather than
        # awaiting it directly (this function is sync and off-thread).
        asyncio.run_coroutine_threadsafe(manager.broadcast(channel, payload), loop)

    result = await asyncio.to_thread(run_scenario, scenario, agent_type, on_event=on_event)
    return result
