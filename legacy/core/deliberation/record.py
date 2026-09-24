"""DeliberationRecord model.

The structured artifact a deliberation pass produces (FR-2.1, FR-2.5).
Plain dataclasses are used here rather than pydantic -- the rest of this
codebase (core/events.py, providers/interfaces.py) is dataclass-based, and
pydantic is not available in this offline environment. Validation that
matters (the confidence bound) is enforced in __post_init__ instead of a
schema library.

Records are immutable artifacts: once built, a DeliberationRecord is never
mutated in place. A re-deliberation produces a *new* record that points
back at the one it supersedes (`supersedes`) -- the old record's
`superseded_by` is filled in by the engine when that happens, but nothing
about the old record's reasoning content is ever rewritten. This is what
makes FR-2.4 ("old record retained, not overwritten") and NFR-5
(auditability) hold.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class ConstraintResult:
    """Structured outcome of one self-critique constraint check.

    Deliberately not just a bool or a string -- FR-2.2/section 12 of the
    continuation spec requires a constraint result to communicate the
    constraint's name, whether it passed, why, and the evidence it looked
    at, so a UI/audit view can render something meaningful (see
    docs/UI_SPEC.md section 1.3 Deliberation Audit View).
    """

    name: str
    passed: bool
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Option:
    """One option the engine considered before choosing.

    `rejected_reason` is None for the option that was ultimately chosen.
    """

    action_type: str
    description: str
    rejected_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DeliberationRecord:
    """The full reasoning artifact for one deliberation pass.

    Fields mirror section 13 of the continuation spec and FR-2.1/FR-2.5:
    decision_id, call_id, intent, known_facts, uncertainties,
    options_considered, chosen_option, rationale, confidence,
    constraint_results, supersedes/superseded_by.
    """

    decision_id: str
    call_id: str
    intent: str
    known_facts: dict[str, Any] = field(default_factory=dict)
    uncertainties: list[str] = field(default_factory=list)
    options_considered: list[Option] = field(default_factory=list)
    chosen_option: Optional[str] = None
    rationale: str = ""
    confidence: float = 0.0
    constraint_results: list[ConstraintResult] = field(default_factory=list)
    resolved: bool = False
    fallback_action: Optional[str] = None
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must satisfy 0.0 <= confidence <= 1.0, got {self.confidence!r}"
            )

    @property
    def passed_all_constraints(self) -> bool:
        return all(result.passed for result in self.constraint_results)

    @property
    def failed_constraints(self) -> list[ConstraintResult]:
        return [result for result in self.constraint_results if not result.passed]

    def mark_superseded_by(self, decision_id: str) -> None:
        """Record that a newer decision replaces this one.

        This is the one deliberate, narrow exception to "records are
        immutable": the *old* record's `superseded_by` pointer is set once,
        after the fact, by the engine -- the old record's own reasoning
        content (known_facts, options, rationale, confidence,
        constraint_results) is never touched. Without this pointer the
        audit trail would have no way to walk forward from an old record
        to the decision that replaced it.
        """
        self.superseded_by = decision_id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
