"""Deliberation engine: orchestrates a deliberation pass.

Sits between the dialogue engine and the future commit state machine
(see docs/ARCHITECTURE.md section 2). It:

  1. Picks the best-matching IntentHandler for the current conversation
     context (IntentRegistry).
  2. Asks that handler for its candidate facts/options/decision
     (IntentHandler.deliberate).
  3. Runs every registered self-critique ConstraintCheck against that
     candidate (ConstraintRegistry).
  4. Resolves the decision if every constraint passed, or computes a safe
     fallback if not.
  5. Builds an immutable DeliberationRecord, links it to whatever it
     supersedes in the same decision_group for this call, and publishes
     the appropriate events on the existing event bus.

Depends only on core/events.py, core/event_bus.py, and the provider
*interfaces* (ConversationTurn) -- never a concrete provider. No FastAPI,
database, or UI import belongs here (those are later phases).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

from core.deliberation.constraints import ConstraintRegistry, default_constraint_registry
from core.deliberation.intents import IntentRegistry, default_intent_registry
from core.deliberation.record import DeliberationRecord
from core.event_bus import EventBus
from core.events import (
    DeliberationResolved,
    DeliberationStarted,
    ReDeliberationTriggered,
    SelfCritiqueFailed,
)
from providers.interfaces import ConversationTurn

# Safe fallback action types the engine can choose when a candidate decision
# fails self-critique. "request_more_info" is used when more information
# could plausibly be asked of the caller (FR-2.3's first option);
# "escalate_to_human" is the safe default otherwise (FR-2.3's second option).
FALLBACK_REQUEST_MORE_INFO = "request_more_info"
FALLBACK_ESCALATE_TO_HUMAN = "escalate_to_human"


def _default_clock() -> Callable[[], int]:
    start = time.perf_counter()
    return lambda: int((time.perf_counter() - start) * 1000)


def _default_id_factory() -> Callable[[], str]:
    return lambda: uuid.uuid4().hex[:8]


@dataclass
class Assessment:
    """A lightweight, synchronous read of "what would deliberation conclude
    right now" -- used by DeliberationBackedInterruptionStrategy, which must
    answer synchronously inside DialogueEngine.check_for_barge_in and so
    cannot await the full event-publishing `deliberate()` pass.

    This intentionally mirrors IntentDeliberationResult rather than
    DeliberationRecord: it is not a persisted decision, has no decision_id,
    and never appears in an audit trail -- it exists purely to drive the
    interruption-resolution decision.
    """

    intent_name: str
    known_facts: dict[str, Any]
    uncertainties: list[str]
    chosen_option: Optional[str]
    confidence: float


class DeliberationRepositoryProtocol(Protocol):
    """Structural interface a persistence repository must satisfy.

    Phase 5 addition: deliberately a Protocol, not an import of
    persistence/repository.py, so this module -- and every existing Phase 4
    test that doesn't pass one -- remains completely unaffected; `None` is
    still the default and behavior without a repository is byte-for-byte
    what Phase 4 shipped. See the docstring on `deliberate()` for why this
    was added here rather than left to a separate event-bus subscriber.
    """

    def save_deliberation(self, record: DeliberationRecord) -> None: ...


class DeliberationEngine:
    def __init__(
        self,
        bus: EventBus,
        intent_registry: Optional[IntentRegistry] = None,
        constraint_registry: Optional[ConstraintRegistry] = None,
        clock_ms: Optional[Callable[[], int]] = None,
        id_factory: Optional[Callable[[], str]] = None,
        repository: Optional[DeliberationRepositoryProtocol] = None,
    ) -> None:
        self._bus = bus
        self._intents = intent_registry or default_intent_registry()
        self._constraints = constraint_registry or default_constraint_registry()
        self._clock_ms = clock_ms or _default_clock()
        self._id_factory = id_factory or _default_id_factory()
        # Phase 5 addition (see DeliberationRepositoryProtocol above):
        # optional, defaults to None, changes nothing for existing callers.
        self._repository = repository

        # (call_id, decision_group) -> latest record in that group for that
        # call. This is the supersession chain: a later pass in the same
        # group for the same call replaces the previous entry here (and
        # only here -- the old record object itself is never mutated except
        # for having its `superseded_by` pointer set).
        self._latest_by_group: dict[tuple[str, str], DeliberationRecord] = {}
        self._records_by_id: dict[str, DeliberationRecord] = {}

    # --- introspection -------------------------------------------------

    def record(self, decision_id: str) -> Optional[DeliberationRecord]:
        return self._records_by_id.get(decision_id)

    def latest_for(self, call_id: str, decision_group: str) -> Optional[DeliberationRecord]:
        return self._latest_by_group.get((call_id, decision_group))

    # --- synchronous assessment (used by the interruption strategy) ----

    def assess(self, context: list[ConversationTurn]) -> Optional[Assessment]:
        """Pure, synchronous read of the current best intent match.

        No event bus interaction and no persisted record -- safe to call
        from a synchronous context such as InterruptionStrategy.decide.
        """
        handler = self._intents.best_match(context)
        if handler is None:
            return None
        result = handler.deliberate(context)
        return Assessment(
            intent_name=handler.name,
            known_facts=result.known_facts,
            uncertainties=result.uncertainties,
            chosen_option=result.chosen_option,
            confidence=result.confidence,
        )

    # --- the full, persisted, event-emitting deliberation pass ---------

    async def deliberate(
        self, call_id: str, context: list[ConversationTurn]
    ) -> DeliberationRecord:
        """Run one full deliberation pass and persist it if a repository was
        injected (Phase 5).

        Persistence lives here, in the engine, rather than as a separate
        event-bus subscriber on DeliberationResolved: that event is
        deliberately lightweight for the UI (see docs/UI_SPEC.md's event
        contract table) and omits rationale, structured constraint_results,
        and supersession pointers -- exactly the fields the audit view
        (UI_SPEC.md section 1.3) and NFR-5 auditability require to be
        persisted. The engine already builds the full DeliberationRecord;
        reconstructing it from the trimmed event payload would be lossy.
        """
        handler = self._intents.best_match(context)
        decision_id = self._id_factory()
        intent_name = handler.name if handler else "unknown"

        await self._bus.publish(
            DeliberationStarted(
                call_id=call_id,
                timestamp_ms=self._clock_ms(),
                decision_id=decision_id,
                intent=intent_name,
            )
        )

        if handler is None:
            record = DeliberationRecord(
                decision_id=decision_id,
                call_id=call_id,
                intent=intent_name,
                rationale="No matching intent for the current conversation state.",
                confidence=0.0,
                resolved=False,
                fallback_action=FALLBACK_REQUEST_MORE_INFO,
            )
            self._records_by_id[decision_id] = record
            self._persist(record)
            await self._publish_resolved(record)
            return record

        result = handler.deliberate(context)

        constraint_results = self._constraints.run_all(
            intent=handler.name,
            chosen_option=result.chosen_option,
            known_facts=result.known_facts,
            uncertainties=result.uncertainties,
        )
        constraints_ok = all(r.passed for r in constraint_results)
        is_actionable = result.chosen_option is not None
        resolved = constraints_ok and is_actionable

        fallback_action = None
        if not resolved:
            fallback_action = (
                FALLBACK_REQUEST_MORE_INFO if result.uncertainties else FALLBACK_ESCALATE_TO_HUMAN
            )

        record = DeliberationRecord(
            decision_id=decision_id,
            call_id=call_id,
            intent=handler.name,
            known_facts=result.known_facts,
            uncertainties=result.uncertainties,
            options_considered=result.options_considered,
            chosen_option=result.chosen_option if resolved else None,
            rationale=result.rationale,
            confidence=result.confidence,
            constraint_results=constraint_results,
            resolved=resolved,
            fallback_action=fallback_action,
        )

        group_key = (call_id, handler.decision_group)
        previous = self._latest_by_group.get(group_key)
        if previous is not None:
            record.supersedes = previous.decision_id
            previous.mark_superseded_by(record.decision_id)
            # Re-persist the old record so its stored row picks up the new
            # superseded_by pointer. The repository (persistence/
            # repository.py) enforces at the SQL layer that this is the
            # *only* field a re-save can change for an existing decision_id
            # -- the old record's reasoning content is never overwritten.
            self._persist(previous)
            await self._bus.publish(
                ReDeliberationTriggered(
                    call_id=call_id,
                    timestamp_ms=self._clock_ms(),
                    decision_id=record.decision_id,
                    supersedes_id=previous.decision_id,
                    reason=self._supersession_reason(previous, record),
                )
            )

        self._latest_by_group[group_key] = record
        self._records_by_id[decision_id] = record
        self._persist(record)

        if not constraints_ok:
            failed = record.failed_constraints[0]
            await self._bus.publish(
                SelfCritiqueFailed(
                    call_id=call_id,
                    timestamp_ms=self._clock_ms(),
                    decision_id=decision_id,
                    failed_constraint=failed.name,
                    fallback_action=record.fallback_action or FALLBACK_ESCALATE_TO_HUMAN,
                )
            )

        await self._publish_resolved(record)
        return record

    def _persist(self, record: DeliberationRecord) -> None:
        if self._repository is not None:
            self._repository.save_deliberation(record)

    async def _publish_resolved(self, record: DeliberationRecord) -> None:
        await self._bus.publish(
            DeliberationResolved(
                call_id=record.call_id,
                timestamp_ms=self._clock_ms(),
                decision_id=record.decision_id,
                known=record.known_facts,
                uncertain=record.uncertainties,
                options=[option.action_type for option in record.options_considered],
                chosen=record.chosen_option,
                confidence=record.confidence,
            )
        )

    @staticmethod
    def _supersession_reason(previous: DeliberationRecord, current: DeliberationRecord) -> str:
        if previous.intent != current.intent:
            return (
                f"contradicting information: reclassified from '{previous.intent}' "
                f"to '{current.intent}'"
            )
        return f"new information for '{current.intent}': reconsidered with updated facts"


class DeliberationBackedInterruptionStrategy:
    """The real interruption-resolution decision (section 18), replacing
    DialogueEngine.DefaultInterruptionStrategy.

    Implements the same `InterruptionStrategy` protocol
    (core/dialogue/engine.py) via structural typing -- no import of that
    module is needed here, keeping the dependency direction one-way
    (dialogue -> deliberation is what the seam expects; deliberation does
    not need to know about DialogueEngine internals).

    Uses DeliberationEngine.assess -- the synchronous read -- because
    InterruptionStrategy.decide is itself synchronous (it is called
    mid-barge-in, inline in DialogueEngine.check_for_barge_in).
    """

    def __init__(self, engine: DeliberationEngine) -> None:
        self._engine = engine

    def decide(self, context: list[ConversationTurn]):
        # Imported lazily to avoid a hard import-time dependency from
        # core/deliberation -> core/dialogue; both are core modules, but
        # this keeps the module importable standalone (e.g. in tests) and
        # keeps the direction of the dependency the caller's choice.
        from core.dialogue.engine import InterruptionResolution

        assessment = self._engine.assess(context)

        if assessment is None:
            # No recognizable intent in the interruption -- e.g. an
            # irrelevant clarification. Nothing to act on, so resume
            # whatever the agent was already doing.
            return InterruptionResolution.RESUME

        if assessment.intent_name == "emergency":
            # Contradictory/emergency information always warrants a full
            # re-plan, regardless of what was previously known.
            return InterruptionResolution.REPLAN

        if assessment.chosen_option is None or assessment.uncertainties:
            # A recognizable intent that's missing required information --
            # ask a targeted follow-up rather than resuming blindly or
            # replanning from scratch.
            return InterruptionResolution.TARGETED_FOLLOW_UP

        # A recognizable, fully-resolvable intent with nothing new or
        # contradictory about it -- safe to resume.
        return InterruptionResolution.RESUME
