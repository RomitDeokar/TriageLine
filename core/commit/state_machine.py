"""CommitStateMachine: PROPOSED -> PENDING_CONFIRMATION -> FINALIZED / ABORTED.

The safety boundary this module exists to enforce (continuation spec
section 2/4): deliberation may *recommend* an action, but nothing here
ever finalizes one without an explicit `confirm()` call. High confidence,
passed constraints, a prior similar utterance, or the mere fact that an
action was proposed are never sufficient -- only an explicit confirmation
input reaches FINALIZED.

Depends only on core/events.py, core/event_bus.py, and (structurally, via
a Protocol -- no import) an optional persistence repository. No sqlite3,
FastAPI, or concrete provider import belongs here -- see
core/commit/strategies.py for the one pluggable behavioral seam this
module exposes.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Protocol

from core.commit.strategies import CommitStrategy, DeliberativeCommitStrategy
from core.event_bus import EventBus
from core.events import ActionAborted, ActionFinalized, ActionPending, ActionProposed


class CommitState(Enum):
    PROPOSED = "proposed"
    PENDING_CONFIRMATION = "pending_confirmation"
    FINALIZED = "finalized"
    ABORTED = "aborted"


#: The only transitions this state machine will ever perform. Anything not
#: listed here -- including every example enumerated in section 6 of the
#: continuation spec (PROPOSED -> FINALIZED, FINALIZED -> ABORTED, etc.) --
#: raises InvalidTransitionError. FINALIZED and ABORTED map to an empty
#: set: both are terminal.
ALLOWED_TRANSITIONS: dict[CommitState, set[CommitState]] = {
    CommitState.PROPOSED: {CommitState.PENDING_CONFIRMATION},
    CommitState.PENDING_CONFIRMATION: {CommitState.FINALIZED, CommitState.ABORTED},
    CommitState.FINALIZED: set(),
    CommitState.ABORTED: set(),
}

TERMINAL_STATES = frozenset({CommitState.FINALIZED, CommitState.ABORTED})


class InvalidTransitionError(Exception):
    """Raised when a transition is not in ALLOWED_TRANSITIONS."""

    def __init__(self, current: CommitState, target: CommitState) -> None:
        super().__init__(f"Cannot transition from {current.value!r} to {target.value!r}")
        self.current = current
        self.target = target


class UnknownActionError(KeyError):
    """Raised when an action_id has no corresponding CommitRecord."""


@dataclass
class CommitRecord:
    """The structured, persistable representation of one proposed action.

    Traceable back through `decision_id` to the DeliberationRecord that
    produced it, and through `call_id` to the call -- the
    Call -> DeliberationRecord -> ActionProposal -> Commit chain called
    for in section 7 of the continuation spec.
    """

    action_id: str
    call_id: str
    decision_id: str
    action_type: str
    action_payload: dict[str, Any] = field(default_factory=dict)
    current_state: CommitState = CommitState.PROPOSED
    created_at_ms: int = 0
    updated_at_ms: int = 0
    confirmed_by: Optional[str] = None
    confirmed_at_ms: Optional[int] = None
    aborted_reason: Optional[str] = None
    aborted_at_ms: Optional[int] = None

    @property
    def is_terminal(self) -> bool:
        return self.current_state in TERMINAL_STATES


class CommitRepositoryProtocol(Protocol):
    """Structural interface a persistence repository must satisfy.

    Deliberately not an import of persistence/repository.py -- core must
    stay testable and importable without a database (see docs/
    ARCHITECTURE.md's "everything must be testable ... with zero external
    providers" and continuation spec section 16). Any object with this one
    method works, including a plain in-memory fake in tests.
    """

    def save_action(self, record: CommitRecord) -> None: ...


def _default_clock() -> Callable[[], int]:
    start = time.perf_counter()
    return lambda: int((time.perf_counter() - start) * 1000)


def _default_id_factory() -> Callable[[], str]:
    return lambda: uuid.uuid4().hex[:8]


class CommitStateMachine:
    """Manages the lifecycle of every proposed action for every call.

    One instance can track many actions across many calls -- records are
    keyed by `action_id` and looked up by `call_id` for teardown/listing.
    """

    def __init__(
        self,
        bus: EventBus,
        strategy: Optional[CommitStrategy] = None,
        repository: Optional[CommitRepositoryProtocol] = None,
        clock_ms: Optional[Callable[[], int]] = None,
        id_factory: Optional[Callable[[], str]] = None,
    ) -> None:
        self._bus = bus
        self._strategy = strategy or DeliberativeCommitStrategy()
        self._repository = repository
        self._clock_ms = clock_ms or _default_clock()
        self._id_factory = id_factory or _default_id_factory()
        self._records: dict[str, CommitRecord] = {}

    # --- introspection ---------------------------------------------

    def get(self, action_id: str) -> Optional[CommitRecord]:
        return self._records.get(action_id)

    def list_for_call(self, call_id: str) -> list[CommitRecord]:
        return [r for r in self._records.values() if r.call_id == call_id]

    # --- lifecycle ----------------------------------------------------

    async def propose(
        self,
        *,
        call_id: str,
        decision_id: str,
        action_type: str,
        action_payload: Optional[dict[str, Any]] = None,
    ) -> CommitRecord:
        """Create a new action in PROPOSED state and emit ActionProposed.

        Proposing an action is never, by itself, sufficient to finalize it
        (section 4/8) -- it only ever reaches PENDING_CONFIRMATION here
        (immediately, if the strategy says so; see
        core/commit/strategies.py), never FINALIZED.
        """
        now = self._clock_ms()
        record = CommitRecord(
            action_id=self._id_factory(),
            call_id=call_id,
            decision_id=decision_id,
            action_type=action_type,
            action_payload=dict(action_payload or {}),
            current_state=CommitState.PROPOSED,
            created_at_ms=now,
            updated_at_ms=now,
        )
        self._records[record.action_id] = record
        self._persist(record)

        await self._bus.publish(
            ActionProposed(
                call_id=call_id, timestamp_ms=now, action_id=record.action_id, action_type=action_type
            )
        )

        if self._strategy.auto_request_confirmation():
            await self.request_confirmation(record.action_id)

        return record

    async def request_confirmation(self, action_id: str) -> CommitRecord:
        """PROPOSED -> PENDING_CONFIRMATION. Idempotent if already pending."""
        record = self._require(action_id)
        moved = self._transition(record, CommitState.PENDING_CONFIRMATION)
        if moved:
            self._persist(record)
            await self._bus.publish(
                ActionPending(call_id=record.call_id, timestamp_ms=record.updated_at_ms, action_id=action_id)
            )
        return record

    async def confirm(self, action_id: str, confirmed_by: Optional[str] = None) -> CommitRecord:
        """PENDING_CONFIRMATION -> FINALIZED. The only way to finalize.

        This is the sole path to FINALIZED anywhere in this module --
        confirmation must be an explicit input (section 8); nothing else
        (confidence, constraints, prior wording, a bare proposal) can
        reach this state. Calling this again on an already-FINALIZED
        record is a no-op: it returns the existing record without
        re-transitioning, re-persisting, or re-publishing (section 9).
        """
        record = self._require(action_id)
        moved = self._transition(record, CommitState.FINALIZED)
        if moved:
            record.confirmed_by = confirmed_by
            record.confirmed_at_ms = record.updated_at_ms
            self._persist(record)
            await self._bus.publish(
                ActionFinalized(call_id=record.call_id, timestamp_ms=record.updated_at_ms, action_id=action_id)
            )
        return record

    async def abort(self, action_id: str, reason: str) -> CommitRecord:
        """PENDING_CONFIRMATION -> ABORTED. Idempotent if already aborted."""
        record = self._require(action_id)
        moved = self._transition(record, CommitState.ABORTED)
        if moved:
            record.aborted_reason = reason
            record.aborted_at_ms = record.updated_at_ms
            self._persist(record)
            await self._bus.publish(
                ActionAborted(
                    call_id=record.call_id, timestamp_ms=record.updated_at_ms, action_id=action_id, reason=reason
                )
            )
        return record

    async def force_resolve_pending(
        self, call_id: str, reason: str = "session_teardown"
    ) -> list[CommitRecord]:
        """FR-3.4: never leave an action non-terminal after a call ends.

        Not required by the Phase 5 test checklist directly, but is the
        natural, small extension of the same state machine that the
        Requirements doc calls for -- included here rather than deferred,
        since it needs no new state or transition, only driving existing
        ones for every non-terminal action on a call.
        """
        resolved = []
        for record in self.list_for_call(call_id):
            if record.is_terminal:
                continue
            if record.current_state == CommitState.PROPOSED:
                await self.request_confirmation(record.action_id)
            await self.abort(record.action_id, reason)
            resolved.append(record)
        return resolved

    # --- internals ------------------------------------------------------

    def _require(self, action_id: str) -> CommitRecord:
        record = self._records.get(action_id)
        if record is None:
            raise UnknownActionError(action_id)
        return record

    def _transition(self, record: CommitRecord, target: CommitState) -> bool:
        """Apply a transition if allowed.

        Returns True if a real transition happened, False if this was an
        idempotent no-op (already in `target`). Raises InvalidTransitionError
        for anything not in ALLOWED_TRANSITIONS.
        """
        if record.current_state == target:
            return False
        if target not in ALLOWED_TRANSITIONS.get(record.current_state, set()):
            raise InvalidTransitionError(record.current_state, target)
        record.current_state = target
        record.updated_at_ms = self._clock_ms()
        return True

    def _persist(self, record: CommitRecord) -> None:
        if self._repository is not None:
            self._repository.save_action(record)
