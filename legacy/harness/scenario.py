"""Deterministic scripted call scenarios for the replay harness.

A Scenario is an ordered sequence of ScenarioSteps -- no real-time sleeps,
no audio, no network. Each step is one thing that "happens" during a call:
the caller says something, the caller confirms/rejects a pending action,
a barge-in occurs, or the call disconnects. The replay harness (harness/
replay.py) feeds these steps into the existing core (deliberation engine +
commit state machine) directly, bypassing the transport layer entirely
(see docs/ARCHITECTURE.md section 3, "the replay harness is a first-class
consumer of core, not a hack").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class StepType(Enum):
    CALLER_SAYS = "caller_says"
    CONFIRM = "confirm"
    REJECT = "reject"
    BARGE_IN = "barge_in"
    DISCONNECT = "disconnect"


@dataclass
class ScenarioStep:
    type: StepType
    text: str = ""


def caller_says(text: str) -> ScenarioStep:
    return ScenarioStep(type=StepType.CALLER_SAYS, text=text)


def confirm() -> ScenarioStep:
    return ScenarioStep(type=StepType.CONFIRM)


def reject() -> ScenarioStep:
    return ScenarioStep(type=StepType.REJECT)


def barge_in() -> ScenarioStep:
    return ScenarioStep(type=StepType.BARGE_IN)


def disconnect() -> ScenarioStep:
    return ScenarioStep(type=StepType.DISCONNECT)


@dataclass
class Scenario:
    """One deterministic scripted call.

    `scenario_id` must be unique within a single replay run (the replay
    harness uses it, plus the agent type, to build each call's call_id so
    naive and deliberative runs of the same scenario never share state).
    """

    scenario_id: str
    description: str
    steps: list[ScenarioStep] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def full_caller_text(self) -> str:
        return " ".join(s.text for s in self.steps if s.type == StepType.CALLER_SAYS)
