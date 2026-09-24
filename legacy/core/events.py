"""Typed event definitions for the event bus.

These are the events the dialogue, deliberation, and commit layers emit,
and the only thing the eventual UI depends on (see UI_SPEC.md's event
contract table). All fields are JSON-serializable primitives so events
can cross a WebSocket without extra translation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class Event:
    """Common base: every event belongs to one call and has a timestamp."""

    call_id: str
    timestamp_ms: int

    @property
    def event_type(self) -> str:
        return type(self).__name__

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["event_type"] = self.event_type
        return data


@dataclass
class TurnStarted(Event):
    speaker: str


@dataclass
class PartialTranscript(Event):
    speaker: str
    text: str


@dataclass
class FinalTranscript(Event):
    speaker: str
    text: str


@dataclass
class BargeIn(Event):
    position_ms: int
    latency_ms: Optional[int] = None


@dataclass
class BackchannelSent(Event):
    text: str


@dataclass
class DeliberationStarted(Event):
    decision_id: str
    intent: str


@dataclass
class DeliberationResolved(Event):
    decision_id: str
    known: dict = field(default_factory=dict)
    uncertain: list = field(default_factory=list)
    options: list = field(default_factory=list)
    chosen: Optional[str] = None
    confidence: Optional[float] = None


@dataclass
class SelfCritiqueFailed(Event):
    decision_id: str
    failed_constraint: str
    fallback_action: str


@dataclass
class ReDeliberationTriggered(Event):
    decision_id: str
    supersedes_id: str
    reason: str


@dataclass
class ActionProposed(Event):
    action_id: str
    action_type: str


@dataclass
class ActionPending(Event):
    action_id: str


@dataclass
class ActionFinalized(Event):
    action_id: str


@dataclass
class ActionAborted(Event):
    action_id: str
    reason: str


@dataclass
class MetricsTick(Event):
    barge_in_latency_ms: Optional[int] = None
    time_to_decision_ms: Optional[int] = None
