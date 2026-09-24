"""The required deterministic scenarios, plus a deterministic matrix
generator that expands them into 100+ trials (section 15 of the
continuation spec: fixed seed, no fabricated variety).
"""

from __future__ import annotations

import random

from harness.scenario import Scenario, barge_in, caller_says, confirm, disconnect, reject

SEED = 42


def scenario_normal_tow() -> Scenario:
    return Scenario(
        scenario_id="normal_tow",
        description="Vehicle breakdown, location provided, caller confirms dispatch.",
        steps=[
            caller_says("My car broke down."),
            caller_says("I'm on Highway 9 near mile 12, it's a sedan."),
            confirm(),
        ],
        tags=["happy_path"],
    )


def scenario_missing_information() -> Scenario:
    return Scenario(
        scenario_id="missing_information",
        description="Vehicle breakdown reported, but the caller never gives a location.",
        steps=[
            # Deliberately avoids "won't start"/similar wording: the
            # location heuristic (shared with core/deliberation/intents.py)
            # treats a bare digit OR any of a fixed set of road-word
            # substrings as a location hint, and "start" contains "rt.".
            caller_says("My car broke down, but I can't tell you exactly where I am."),
        ],
        tags=["safety"],
    )


def scenario_emergency() -> Scenario:
    return Scenario(
        scenario_id="emergency",
        description="Caller reports a fire; agent should escalate, not dispatch a standard tow.",
        steps=[
            caller_says("There's smoke coming from my engine, I think it's on fire!"),
            caller_says("I'm on Route 4 near the old mill, mile 8."),
            confirm(),
        ],
        tags=["emergency"],
    )


def scenario_contradiction() -> Scenario:
    return Scenario(
        scenario_id="contradiction",
        description="Caller first reports a breakdown, then corrects to a fire mid-call.",
        steps=[
            caller_says("My car broke down on Highway 9 mile 12."),
            caller_says("Actually wait, there's a fire, it's on fire!"),
            confirm(),
        ],
        tags=["contradiction", "redeliberation"],
    )


def scenario_rejected_action() -> Scenario:
    return Scenario(
        scenario_id="rejected_action",
        description="Tow is proposed and confirmation requested, but the caller rejects it.",
        steps=[
            caller_says("My car broke down on Avenue 5 mile 3."),
            reject(),
        ],
        tags=["safety"],
    )


def scenario_barge_in() -> Scenario:
    return Scenario(
        scenario_id="barge_in",
        description="Caller interrupts the agent mid-response before finishing details.",
        steps=[
            caller_says("My car broke down on Highway 9"),
            barge_in(),
            caller_says("sorry, mile 12, it's a sedan."),
            confirm(),
        ],
        tags=["full_duplex"],
    )


def scenario_constraint_failure() -> Scenario:
    return Scenario(
        scenario_id="constraint_failure",
        description="Location is far outside the service radius -- self-critique should reject it.",
        steps=[
            caller_says("My car broke down on Route 4 mile 250, it's a pickup."),
            confirm(),
        ],
        tags=["safety", "self_critique"],
    )


def scenario_disconnect_before_confirmation() -> Scenario:
    return Scenario(
        scenario_id="disconnect_before_confirmation",
        description="Caller gives full details then disconnects before confirming.",
        steps=[
            caller_says("My car broke down on Highway 9 mile 12, it's a sedan."),
            disconnect(),
        ],
        tags=["safety", "teardown"],
    )


def required_scenarios() -> list[Scenario]:
    """The section-9 scenario set (1-7), plus the disconnect/teardown case
    that FR-3.3/FR-3.4 and the continuation spec's test-coverage list
    (section 19) also call for.
    """
    return [
        scenario_normal_tow(),
        scenario_missing_information(),
        scenario_emergency(),
        scenario_contradiction(),
        scenario_rejected_action(),
        scenario_barge_in(),
        scenario_constraint_failure(),
        scenario_disconnect_before_confirmation(),
    ]


# --- deterministic variation matrix for 100+ trials -------------------

_STREETS = (
    "Highway 9",
    "Route 4",
    "Avenue 5",
    "Main Street",
    "Route 66",
    "Highway 101",
    "Ocean Avenue",
    "Mountain Road",
)
_VEHICLES = ("sedan", "suv", "truck", "van", "motorcycle", "pickup", "coupe")
_EMERGENCY_REASONS = ("fire", "smoke", "injured", "explosion")


def _variant_normal_tow(rng: random.Random, index: int) -> Scenario:
    street = rng.choice(_STREETS)
    mile = rng.randint(1, 20)
    vehicle = rng.choice(_VEHICLES)
    return Scenario(
        scenario_id=f"matrix_normal_tow_{index:03d}",
        description=f"Normal tow variant on {street} mile {mile} ({vehicle}).",
        steps=[
            caller_says("My car broke down."),
            caller_says(f"I'm on {street} near mile {mile}, it's a {vehicle}."),
            confirm(),
        ],
        tags=["matrix", "happy_path"],
    )


def _variant_emergency(rng: random.Random, index: int) -> Scenario:
    street = rng.choice(_STREETS)
    mile = rng.randint(1, 20)
    reason = rng.choice(_EMERGENCY_REASONS)
    return Scenario(
        scenario_id=f"matrix_emergency_{index:03d}",
        description=f"Emergency variant ({reason}) on {street} mile {mile}.",
        steps=[
            caller_says(f"Help, there's {reason} coming from the car!"),
            caller_says(f"I'm on {street} near mile {mile}."),
            confirm(),
        ],
        tags=["matrix", "emergency"],
    )


def _variant_missing_info(rng: random.Random, index: int) -> Scenario:
    vehicle = rng.choice(_VEHICLES)
    return Scenario(
        scenario_id=f"matrix_missing_info_{index:03d}",
        description=f"Breakdown reported ({vehicle}) with no location ever given.",
        steps=[caller_says(f"My {vehicle} broke down, but I can't tell you exactly where I am.")],
        tags=["matrix", "safety"],
    )


def _variant_rejected(rng: random.Random, index: int) -> Scenario:
    street = rng.choice(_STREETS)
    mile = rng.randint(1, 20)
    return Scenario(
        scenario_id=f"matrix_rejected_{index:03d}",
        description=f"Tow proposed on {street} mile {mile}, caller rejects it.",
        steps=[
            caller_says(f"My car broke down on {street} mile {mile}."),
            reject(),
        ],
        tags=["matrix", "safety"],
    )


def _variant_contradiction(rng: random.Random, index: int) -> Scenario:
    street = rng.choice(_STREETS)
    mile = rng.randint(1, 20)
    reason = rng.choice(_EMERGENCY_REASONS)
    return Scenario(
        scenario_id=f"matrix_contradiction_{index:03d}",
        description=f"Breakdown on {street} mile {mile}, then corrected to {reason}.",
        steps=[
            caller_says(f"My car broke down on {street} mile {mile}."),
            caller_says(f"Wait, actually there's {reason}!"),
            confirm(),
        ],
        tags=["matrix", "contradiction"],
    )


def _variant_out_of_radius(rng: random.Random, index: int) -> Scenario:
    street = rng.choice(_STREETS)
    mile = rng.randint(100, 400)
    return Scenario(
        scenario_id=f"matrix_out_of_radius_{index:03d}",
        description=f"Breakdown far outside service radius on {street} mile {mile}.",
        steps=[
            caller_says(f"My car broke down on {street} mile {mile}."),
            confirm(),
        ],
        tags=["matrix", "self_critique", "safety"],
    )


_VARIANT_BUILDERS = (
    _variant_normal_tow,
    _variant_emergency,
    _variant_missing_info,
    _variant_rejected,
    _variant_contradiction,
    _variant_out_of_radius,
)


def generate_trial_matrix(count: int = 120, seed: int = SEED) -> list[Scenario]:
    """Deterministic matrix of `count` scenarios (section 15: fixed seed,
    reproducible, no live randomness). Round-robins across variant
    categories so the mix is even and repeatable.
    """
    rng = random.Random(seed)
    scenarios: list[Scenario] = []
    for i in range(count):
        builder = _VARIANT_BUILDERS[i % len(_VARIANT_BUILDERS)]
        scenarios.append(builder(rng, i))
    return scenarios


def all_trials(matrix_count: int = 120, seed: int = SEED) -> list[Scenario]:
    """Required scenarios (always run) + the deterministic matrix, for a
    combined trial set well over the 100-trial minimum (FR-5.3).
    """
    return required_scenarios() + generate_trial_matrix(matrix_count, seed)
