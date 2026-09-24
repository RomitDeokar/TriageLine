"""ConstraintRegistry: pluggable self-critique rules.

These are the deliberation engine's self-critique layer (FR-2.2, section 12
of the continuation spec). A constraint is deliberately independent of how
an intent handler arrived at its candidate option -- handlers are allowed
to propose their "natural" best action optimistically (e.g. "dispatch a
tow truck" even when the location isn't confirmed yet); it is this layer's
job to catch that independently, the way a human reviewer double-checks a
decision rather than just trusting whoever proposed it.

Adding a new constraint is a registration (`ConstraintRegistry.register`),
not an edit to the deliberation engine -- see NFR-2 and the Architecture
doc's extension-points table.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from core.deliberation.record import ConstraintResult


class ConstraintCheck(ABC):
    """One self-critique rule.

    `check` receives the full context a rule might plausibly need
    (intent name, the option the handler chose, the known facts, and the
    open uncertainties) as keyword arguments, so new constraints can be
    added without changing this call signature.
    """

    name: str = "constraint"

    @abstractmethod
    def check(
        self,
        *,
        intent: str,
        chosen_option: Optional[str],
        known_facts: dict[str, Any],
        uncertainties: list[str],
    ) -> ConstraintResult:
        """Evaluate this constraint against a candidate decision."""


class RequiredFieldsConstraint(ConstraintCheck):
    """Required-field completeness (section 12): e.g. tow dispatch requires location.

    Keyed by the *option* (the concrete action being considered), not the
    intent -- two intents can propose the same action type with different
    requirements, and this keeps the mapping explicit and easy to extend.
    """

    name = "required_fields"

    def __init__(self, required_fields_by_option: dict[str, list[str]]) -> None:
        self._required_fields_by_option = required_fields_by_option

    def check(
        self,
        *,
        intent: str,
        chosen_option: Optional[str],
        known_facts: dict[str, Any],
        uncertainties: list[str],
    ) -> ConstraintResult:
        required = self._required_fields_by_option.get(chosen_option or "", [])
        if not required:
            return ConstraintResult(
                name=self.name,
                passed=True,
                reason=f"No required fields configured for '{chosen_option}'.",
                evidence={"chosen_option": chosen_option},
            )

        missing = [field for field in required if field not in known_facts]
        if missing:
            return ConstraintResult(
                name=self.name,
                passed=False,
                reason=f"Missing required field(s) for '{chosen_option}': {', '.join(missing)}.",
                evidence={"chosen_option": chosen_option, "missing_fields": missing},
            )
        return ConstraintResult(
            name=self.name,
            passed=True,
            reason=f"All required fields present for '{chosen_option}'.",
            evidence={"chosen_option": chosen_option, "required_fields": required},
        )


class SeverityThresholdConstraint(ConstraintCheck):
    """Severity threshold (section 12): emergencies must not be under- or over-classified.

    Two failure modes, both unsafe in opposite directions:
    - proposing a standard dispatch when known severity is "emergency"
      (under-reacting to a fire), and
    - proposing an emergency escalation when nothing in known_facts
      actually justifies it (crying wolf / hallucinated severity).
    """

    name = "severity_threshold"

    STANDARD_OPTION = "dispatch_tow"
    EMERGENCY_OPTION = "escalate_emergency"

    def check(
        self,
        *,
        intent: str,
        chosen_option: Optional[str],
        known_facts: dict[str, Any],
        uncertainties: list[str],
    ) -> ConstraintResult:
        severity = known_facts.get("severity")

        if chosen_option == self.STANDARD_OPTION and severity == "emergency":
            return ConstraintResult(
                name=self.name,
                passed=False,
                reason="Known severity is 'emergency'; a standard dispatch is not sufficient.",
                evidence={"severity": severity, "chosen_option": chosen_option},
            )

        if chosen_option == self.EMERGENCY_OPTION and severity != "emergency":
            return ConstraintResult(
                name=self.name,
                passed=False,
                reason="Emergency escalation chosen without evidence of emergency severity.",
                evidence={"severity": severity, "chosen_option": chosen_option},
            )

        return ConstraintResult(
            name=self.name,
            passed=True,
            reason="Severity classification is consistent with the chosen option.",
            evidence={"severity": severity, "chosen_option": chosen_option},
        )


class ServiceRadiusConstraint(ConstraintCheck):
    """Service radius (section 12): a dispatch must be within the configured radius.

    Only applies to options that actually send a physical unit
    (dispatch_tow). When no distance evidence is available, the check is
    non-blocking -- it is not this constraint's job to demand a fact
    RequiredFieldsConstraint didn't already require; it only fails when it
    has positive evidence the location is out of range.
    """

    name = "service_radius"

    GATED_OPTIONS = ("dispatch_tow",)

    def __init__(self, max_radius_miles: float) -> None:
        self._max_radius_miles = max_radius_miles

    def check(
        self,
        *,
        intent: str,
        chosen_option: Optional[str],
        known_facts: dict[str, Any],
        uncertainties: list[str],
    ) -> ConstraintResult:
        if chosen_option not in self.GATED_OPTIONS:
            return ConstraintResult(
                name=self.name,
                passed=True,
                reason=f"Service radius does not apply to '{chosen_option}'.",
                evidence={"chosen_option": chosen_option},
            )

        distance = known_facts.get("distance_miles")
        if distance is None:
            return ConstraintResult(
                name=self.name,
                passed=True,
                reason="No distance evidence available; service radius check skipped.",
                evidence={"chosen_option": chosen_option},
            )

        if distance > self._max_radius_miles:
            return ConstraintResult(
                name=self.name,
                passed=False,
                reason=(
                    f"{distance} mi exceeds the {self._max_radius_miles} mi service radius."
                ),
                evidence={"distance_miles": distance, "max_radius_miles": self._max_radius_miles},
            )

        return ConstraintResult(
            name=self.name,
            passed=True,
            reason=f"{distance} mi is within the {self._max_radius_miles} mi service radius.",
            evidence={"distance_miles": distance, "max_radius_miles": self._max_radius_miles},
        )


class ConstraintRegistry:
    """Pluggable collection of ConstraintCheck instances.

    Registration order is preserved and is the order constraints run in;
    `run_all` returns every result rather than short-circuiting so a UI or
    audit view can show the full self-critique picture (see UI_SPEC.md
    section 1.3), not just the first failure.
    """

    def __init__(self) -> None:
        self._constraints: list[ConstraintCheck] = []

    def register(self, constraint: ConstraintCheck) -> None:
        self._constraints.append(constraint)

    def unregister(self, name: str) -> None:
        self._constraints = [c for c in self._constraints if c.name != name]

    def constraints(self) -> list[ConstraintCheck]:
        return list(self._constraints)

    def run_all(
        self,
        *,
        intent: str,
        chosen_option: Optional[str],
        known_facts: dict[str, Any],
        uncertainties: list[str],
    ) -> list[ConstraintResult]:
        return [
            constraint.check(
                intent=intent,
                chosen_option=chosen_option,
                known_facts=known_facts,
                uncertainties=uncertainties,
            )
            for constraint in self._constraints
        ]


def default_constraint_registry(*, service_radius_miles: float = 25.0) -> ConstraintRegistry:
    """The minimum constraint set required by section 12 of the continuation spec."""
    registry = ConstraintRegistry()
    registry.register(
        RequiredFieldsConstraint(
            required_fields_by_option={
                "dispatch_tow": ["location"],
                "escalate_emergency": ["location"],
            }
        )
    )
    registry.register(SeverityThresholdConstraint())
    registry.register(ServiceRadiusConstraint(max_radius_miles=service_radius_miles))
    return registry
