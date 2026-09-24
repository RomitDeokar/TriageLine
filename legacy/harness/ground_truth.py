"""Independent ground-truth oracle for replay scenarios.

Deliberately does NOT import anything from core/deliberation -- FR-5.2 and
the Requirements glossary both call for a ground truth "computed without
using the agent's own decision logic," so evaluation against it isn't
circular. This module re-implements a small, separate slice of the same
keyword heuristics core/deliberation/intents.py uses; duplication here is
intentional and acceptable (see docs/BUILD_PROMPT.md section 9's note that
the oracle must be independent).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from harness.scenario import Scenario, StepType

# Mirrors config/settings.py's default service_radius_miles=25 -- an
# independently-stated constant, not an import of core's constraint
# registry, so this stays a genuinely separate oracle.
MAX_SERVICE_RADIUS_MILES = 25.0
_MILE_RE = re.compile(r"mile\s*(\d+(?:\.\d+)?)")

_EMERGENCY_HINTS = ("fire", "explosion", "injured", "injury", "unconscious", "smoke")
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
_BREAKDOWN_HINTS = ("flat tire", "broke down", "breakdown", "won't start", "engine")


def _has_any(text: str, hints: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in hints)


def _has_location(text: str) -> bool:
    lowered = text.lower()
    return _has_any(text, _LOCATION_HINTS) or any(ch.isdigit() for ch in lowered)


@dataclass
class GroundTruth:
    """The independently computed "correct" outcome for one scenario.

    `expected_action_type` is None when the correct outcome is to NOT
    finalize any dispatch action (missing information, or the caller
    rejected what was proposed).
    """

    is_emergency: bool
    has_location: bool
    out_of_service_radius: bool
    caller_confirmed: bool
    caller_rejected: bool
    expected_action_type: Optional[str]
    expected_finalization: bool


def compute_ground_truth(scenario: Scenario) -> GroundTruth:
    text = scenario.full_caller_text()
    is_emergency = _has_any(text, _EMERGENCY_HINTS)
    has_location = _has_location(text)
    caller_confirmed = any(s.type == StepType.CONFIRM for s in scenario.steps)
    caller_rejected = any(s.type == StepType.REJECT for s in scenario.steps)

    mile_match = _MILE_RE.search(text.lower())
    distance = float(mile_match.group(1)) if mile_match else None
    out_of_service_radius = distance is not None and distance > MAX_SERVICE_RADIUS_MILES

    if not has_location:
        # No location ever provided: neither a tow nor an emergency
        # escalation can safely be finalized, regardless of confirmation.
        expected_action_type = None
    elif caller_rejected:
        expected_action_type = None
    elif is_emergency:
        expected_action_type = "escalate_emergency"
    elif out_of_service_radius:
        # A standard tow outside the service radius is not a safe
        # dispatch, independent of what the agent under test decides.
        expected_action_type = None
    elif _has_any(text, _BREAKDOWN_HINTS):
        expected_action_type = "dispatch_tow"
    else:
        expected_action_type = None

    expected_finalization = expected_action_type is not None and caller_confirmed and not caller_rejected

    return GroundTruth(
        is_emergency=is_emergency,
        has_location=has_location,
        out_of_service_radius=out_of_service_radius,
        caller_confirmed=caller_confirmed,
        caller_rejected=caller_rejected,
        expected_action_type=expected_action_type,
        expected_finalization=expected_finalization,
    )
