"""IntentRegistry and built-in intent handlers.

An IntentHandler recognizes one kind of situation from the conversation
context and proposes its "natural" candidate action for it. Handlers are
intentionally optimistic: they are not responsible for validating that
their candidate is *safe* to act on (missing fields, severity mismatches,
out-of-radius dispatch) -- that is the self-critique job of
core/deliberation/constraints.py, run independently by the engine. This
keeps each side simple and means a new intent never has to duplicate
safety logic, and a new constraint never has to know about every intent.

Adding a new intent is a registration (`IntentRegistry.register`), not an
edit to the deliberation engine -- see NFR-2 and the Architecture doc's
extension-points table. No `if/elif` chain over intents lives here or in
the engine; `IntentRegistry.best_match` scores every registered handler
and the highest-confidence match wins.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from core.deliberation.record import Option
from providers.interfaces import ConversationTurn

_LOCATION_HINTS = (
    "highway",
    "hwy",
    "street",
    "st.",
    "mile",
    "road",
    "rd.",
    "avenue",
    "ave",
    "route",
    "rt.",
)
_EMERGENCY_HINTS = ("fire", "explosion", "injured", "injury", "unconscious", "smoke")
_BREAKDOWN_HINTS = ("flat tire", "broke down", "breakdown", "won't start", "engine")
_CASE_CLOSURE_HINTS = (
    "that's all",
    "that's it",
    "nothing else",
    "all set",
    "close the case",
    "we're good",
    "no further help",
)
_VEHICLE_HINTS = ("sedan", "suv", "truck", "van", "motorcycle", "pickup", "coupe")
_MILE_RE = re.compile(r"mile\s*(\d+(?:\.\d+)?)")


def _caller_text(context: list[ConversationTurn]) -> str:
    """Full caller-only transcript, lowercased, re-joined fresh each call.

    Re-evaluating the whole context (rather than remembering a prior
    decision) is what makes contradiction-handling correct: a later
    "actually, there's a fire" is present in this string right alongside
    the earlier "my car broke down", so a handler naturally sees both.
    """
    return " ".join(turn.text for turn in context if turn.speaker == "caller")


def _has_any(text: str, hints: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in hints)


def _has_location(text: str) -> bool:
    lowered = text.lower()
    return _has_any(text, _LOCATION_HINTS) or any(ch.isdigit() for ch in lowered)


def _extract_location_snippet(raw_text: str) -> Optional[str]:
    """Best-effort human-readable location snippet, e.g. 'Highway 9 mile 12'.

    Returns the LAST matching snippet in the text, not the first. Callers
    of this function (e.g. BreakdownIntent.deliberate) always pass the
    full, freshly-rejoined caller transcript so that later corrections
    are visible -- see `_caller_text`'s docstring. Scanning for the last
    match, rather than the first, is what makes that recency-awareness
    actually take effect: a caller who says "near Highway 9" and then
    later corrects with "actually, I'm at Highway 12" must resolve to
    Highway 12, since a correction always supersedes what it corrects.
    """
    if not _has_location(raw_text):
        return None
    words = raw_text.split()
    lowered_words = [w.lower() for w in words]
    last_snippet: Optional[str] = None
    for i, word in enumerate(lowered_words):
        if any(hint in word for hint in _LOCATION_HINTS) or any(ch.isdigit() for ch in word):
            start = max(0, i - 1)
            end = min(len(words), i + 3)
            snippet = " ".join(words[start:end]).strip(" .,")
            if snippet:
                last_snippet = snippet
    return last_snippet


def _extract_distance_miles(text: str) -> Optional[float]:
    match = _MILE_RE.search(text.lower())
    return float(match.group(1)) if match else None


def _extract_vehicle(text: str) -> Optional[str]:
    lowered = text.lower()
    for hint in _VEHICLE_HINTS:
        if hint in lowered:
            return hint
    return None


# A hazard word directly governed by a negator ("no fire", "nobody is injured",
# "there isn't any smoke", "not injured") is a statement that the hazard is ABSENT.
_NEGATOR = r"(?:no|not|n't|never|without|nobody|no one|none|isn't|aren't|wasn't|weren't)"


def _hazard_mentions(text: str) -> list[tuple[int, str, bool]]:
    """(position, hazard, negated) for every emergency hint, word-bounded (audit E-05)."""
    lowered = text.lower()
    out = []
    for hint in _EMERGENCY_HINTS:
        for m in re.finditer(r"\b" + re.escape(hint) + r"\w*", lowered):
            # negator within the same clause, at most 4 words before the hazard
            window = re.split(r"[.;!?]|\bbut\b", lowered[max(0, m.start() - 40):m.start()])[-1]
            negated = bool(re.search(r"\b" + _NEGATOR + r"(?:\s+\S+){0,4}\s*$", window))
            out.append((m.start(), hint, negated))
    out.sort()
    return out


def _find_emergency_reason(text: str) -> Optional[str]:
    """First hazard that is actually asserted (not negated), else None."""
    for _pos, hint, negated in _hazard_mentions(text):
        if not negated:
            return hint
    return None


@dataclass
class IntentDeliberationResult:
    """What an IntentHandler produces for one deliberation pass.

    Deliberately does not include a fallback action or a resolved flag --
    computing the safe fallback when something is wrong is the engine's
    job (informed by the constraint layer), not the handler's, so a
    handler never has to duplicate that logic.
    """

    known_facts: dict[str, Any] = field(default_factory=dict)
    uncertainties: list[str] = field(default_factory=list)
    options_considered: list[Option] = field(default_factory=list)
    chosen_option: Optional[str] = None
    rationale: str = ""
    confidence: float = 0.5


class IntentHandler(ABC):
    """One recognizable kind of situation and its natural candidate action."""

    name: str = "intent"
    #: Intents in the same decision_group are mutually reconsiderable --
    #: a later deliberation pass in the same group supersedes the previous
    #: one for that call (see core/deliberation/engine.py). Intents in
    #: different groups represent unrelated decisions and never supersede
    #: each other.
    decision_group: str = "default"

    @abstractmethod
    def detect(self, context: list[ConversationTurn]) -> Optional[float]:
        """Confidence in [0, 1] that this intent applies, or None if it doesn't."""

    @abstractmethod
    def deliberate(self, context: list[ConversationTurn]) -> IntentDeliberationResult:
        """Produce facts/options/rationale for a context this handler matched."""


class BreakdownIntent(IntentHandler):
    """Standard tow / vehicle breakdown (section 11's minimum intent #1)."""

    name = "breakdown"
    decision_group = "incident_response"

    def detect(self, context: list[ConversationTurn]) -> Optional[float]:
        text = _caller_text(context)
        if _has_any(text, _BREAKDOWN_HINTS):
            return 0.7
        return None

    def deliberate(self, context: list[ConversationTurn]) -> IntentDeliberationResult:
        text = _caller_text(context)
        location = _extract_location_snippet(text)
        distance = _extract_distance_miles(text)
        vehicle = _extract_vehicle(text)

        known_facts: dict[str, Any] = {"severity": "standard"}
        uncertainties: list[str] = []

        if location:
            known_facts["location"] = location
        else:
            uncertainties.append("location")

        if distance is not None:
            known_facts["distance_miles"] = distance
        if vehicle:
            known_facts["vehicle"] = vehicle

        confidence = 0.85 if location else 0.5
        rationale = (
            "Standard breakdown; dispatching a tow truck to the caller's location."
            if location
            else "Standard breakdown reported, but the caller's location is not yet confirmed."
        )

        return IntentDeliberationResult(
            known_facts=known_facts,
            uncertainties=uncertainties,
            options_considered=[
                Option(
                    action_type="dispatch_tow",
                    description="Dispatch a tow truck to the caller's location",
                )
            ],
            chosen_option="dispatch_tow",
            rationale=rationale,
            confidence=confidence,
        )


class EmergencyIntent(IntentHandler):
    """Emergency escalation (section 11's minimum intent #2).

    Shares the "incident_response" decision_group with BreakdownIntent:
    a fire reported after an earlier breakdown report is a reclassification
    of the *same* underlying decision (what to do about this call), so the
    engine treats a later emergency deliberation as superseding an earlier
    breakdown one for the same call, per section 16.
    """

    name = "emergency"
    decision_group = "incident_response"

    def detect(self, context: list[ConversationTurn]) -> Optional[float]:
        text = _caller_text(context)
        if _find_emergency_reason(text) is not None:
            return 0.95
        return None

    def deliberate(self, context: list[ConversationTurn]) -> IntentDeliberationResult:
        text = _caller_text(context)
        location = _extract_location_snippet(text)
        reason = _find_emergency_reason(text) or "reported emergency"

        known_facts: dict[str, Any] = {"severity": "emergency", "reason": reason}
        uncertainties: list[str] = []
        if location:
            known_facts["location"] = location
        else:
            uncertainties.append("location")

        return IntentDeliberationResult(
            known_facts=known_facts,
            uncertainties=uncertainties,
            options_considered=[
                Option(
                    action_type="dispatch_tow",
                    description="Dispatch a tow truck to the caller's location",
                    rejected_reason=f"severity indicates emergency ({reason}); standard dispatch is insufficient",
                ),
                Option(
                    action_type="escalate_emergency",
                    description="Escalate to emergency services",
                ),
            ],
            chosen_option="escalate_emergency",
            rationale=f"Caller reported an emergency ({reason}); escalating instead of standard dispatch.",
            confidence=0.9,
        )


class CaseClosureIntent(IntentHandler):
    """Case closure (section 11's minimum intent #3)."""

    name = "case_closure"
    decision_group = "case_management"

    def detect(self, context: list[ConversationTurn]) -> Optional[float]:
        text = _caller_text(context)
        if _has_any(text, _CASE_CLOSURE_HINTS):
            # A deliberate closing phrase is an unambiguous signal and should
            # dominate incidental situational wording earlier in the same
            # transcript (e.g. an earlier "broke down" mention) -- closing
            # the case is a distinct decision_group from incident response,
            # so it must not lose to a stale, already-handled description of
            # what happened.
            return 0.99
        return None

    def deliberate(self, context: list[ConversationTurn]) -> IntentDeliberationResult:
        return IntentDeliberationResult(
            known_facts={},
            uncertainties=[],
            options_considered=[
                Option(action_type="close_case", description="Close out the case")
            ],
            chosen_option="close_case",
            rationale="Caller confirmed no further assistance is needed.",
            confidence=0.95,
        )


class IntentRegistry:
    """Pluggable collection of IntentHandler instances.

    `best_match` scores every registered handler via `detect` and returns
    the highest-confidence match (or None if no handler applies). This is
    the mechanism that lets "fire" outrank "breakdown" when both hint sets
    are present in the same call, without any special-casing.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, IntentHandler] = {}

    def register(self, handler: IntentHandler) -> None:
        self._handlers[handler.name] = handler

    def unregister(self, name: str) -> None:
        self._handlers.pop(name, None)

    def handlers(self) -> list[IntentHandler]:
        return list(self._handlers.values())

    def best_match(self, context: list[ConversationTurn]) -> Optional[IntentHandler]:
        scored: list[tuple[float, IntentHandler]] = []
        for handler in self._handlers.values():
            score = handler.detect(context)
            if score is not None:
                scored.append((score, handler))
        if not scored:
            return None
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[0][1]


def default_intent_registry() -> IntentRegistry:
    """The minimum intent set required by section 11 of the continuation spec."""
    registry = IntentRegistry()
    registry.register(BreakdownIntent())
    registry.register(EmergencyIntent())
    registry.register(CaseClosureIntent())
    return registry
