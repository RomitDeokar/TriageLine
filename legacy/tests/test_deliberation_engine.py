"""Tests for the deliberation engine (Phase 4): intents, constraints,
records, contradiction/supersession, self-critique, registry extensibility,
events, and the deliberation-backed interruption strategy.

Mirrors the structure of tests/test_dialogue_engine.py: plain asyncio.run
helpers, no pytest fixtures, since this environment cannot install pytest
offline (see docs/BUILD_PROMPT.md's testing-environment fallback and
tests/_run_all.py).
"""

import asyncio
import inspect

from core.deliberation.constraints import (
    ConstraintCheck,
    ConstraintRegistry,
    RequiredFieldsConstraint,
    SeverityThresholdConstraint,
    ServiceRadiusConstraint,
    default_constraint_registry,
)
from core.deliberation.engine import (
    DeliberationBackedInterruptionStrategy,
    DeliberationEngine,
    FALLBACK_ESCALATE_TO_HUMAN,
    FALLBACK_REQUEST_MORE_INFO,
)
from core.deliberation.intents import (
    IntentDeliberationResult,
    IntentHandler,
    IntentRegistry,
    default_intent_registry,
)
from core.deliberation.record import ConstraintResult, Option
from core.dialogue.engine import InterruptionResolution
from core.event_bus import EventBus
from core.events import (
    DeliberationResolved,
    DeliberationStarted,
    ReDeliberationTriggered,
    SelfCritiqueFailed,
)
from providers.interfaces import ConversationTurn


def run(coro):
    return asyncio.run(coro)


def make_engine():
    bus = EventBus()
    received = []

    async def record(event):
        received.append(event)

    run(bus.subscribe(record))
    engine = DeliberationEngine(bus=bus)
    return engine, received


def caller_turn(text: str) -> ConversationTurn:
    return ConversationTurn(speaker="caller", text=text)


# --- provider independence (section 20) ---


def test_deliberation_modules_have_no_concrete_provider_imports():
    import core.deliberation.engine as engine_module
    import core.deliberation.intents as intents_module
    import core.deliberation.constraints as constraints_module
    import core.deliberation.record as record_module

    forbidden = ("MockSTT", "MockLLM", "MockTTS", "MockAudioIO", "LiveKit", "Deepgram", "Groq", "Rime")
    for module in (engine_module, intents_module, constraints_module, record_module):
        source = inspect.getsource(module)
        for name in forbidden:
            assert name not in source, f"{module.__name__} references {name}"


def test_deliberation_engine_does_not_emit_commit_or_action_events():
    """Phase 4 must not implement ActionPending -> ActionFinalized -> ActionAborted (section 21)."""
    engine, received = make_engine()
    run(engine.deliberate("call-1", [caller_turn("my car broke down on Highway 9")]))
    run(engine.deliberate("call-1", [caller_turn("actually there's a fire")]))

    forbidden_event_names = {"ActionProposed", "ActionPending", "ActionFinalized", "ActionAborted"}
    emitted_names = {type(e).__name__ for e in received}
    assert forbidden_event_names.isdisjoint(emitted_names)


# --- basic deliberation (section 19: "Basic deliberation") ---


def test_standard_tow_resolves_when_location_known():
    engine, _ = make_engine()
    record = run(engine.deliberate("call-1", [caller_turn("my car broke down on Highway 9")]))

    assert record.intent == "breakdown"
    assert record.resolved is True
    assert record.chosen_option == "dispatch_tow"
    assert record.known_facts["location"]
    assert 0.0 <= record.confidence <= 1.0


def test_emergency_escalation_resolves_and_rejects_standard_tow():
    engine, _ = make_engine()
    record = run(engine.deliberate("call-1", [caller_turn("there's a fire on Highway 9")]))

    assert record.intent == "emergency"
    assert record.resolved is True
    assert record.chosen_option == "escalate_emergency"
    rejected = [o for o in record.options_considered if o.action_type == "dispatch_tow"]
    assert len(rejected) == 1
    assert rejected[0].rejected_reason is not None


def test_case_closure_resolves():
    engine, _ = make_engine()
    record = run(engine.deliberate("call-1", [caller_turn("that's all, thanks")]))

    assert record.intent == "case_closure"
    assert record.resolved is True
    assert record.chosen_option == "close_case"


def test_no_matching_intent_produces_unresolved_record_not_a_crash():
    engine, _ = make_engine()
    record = run(engine.deliberate("call-1", [caller_turn("hello, how are you")]))

    assert record.resolved is False
    assert record.intent == "unknown"
    assert record.fallback_action == FALLBACK_REQUEST_MORE_INFO


# --- missing information / constraint failures (section 19) ---


def test_missing_location_is_not_resolved_and_requests_more_info():
    engine, _ = make_engine()
    record = run(engine.deliberate("call-1", [caller_turn("my car broke down")]))

    assert record.resolved is False
    assert "location" in record.uncertainties
    assert record.chosen_option is None
    assert record.fallback_action == FALLBACK_REQUEST_MORE_INFO
    failed_names = [c.name for c in record.failed_constraints]
    assert "required_fields" in failed_names


def test_service_radius_failure_falls_back_to_escalate_to_human():
    engine, _ = make_engine()
    # location known (mile marker present) but far outside the default 25mi radius
    record = run(engine.deliberate("call-1", [caller_turn("my car broke down on Highway 9 mile 42")]))

    assert record.resolved is False
    assert record.uncertainties == []  # location itself was known; only radius failed
    assert record.fallback_action == FALLBACK_ESCALATE_TO_HUMAN
    failed_names = [c.name for c in record.failed_constraints]
    assert "service_radius" in failed_names


def test_service_radius_within_limit_passes():
    engine, _ = make_engine()
    record = run(engine.deliberate("call-1", [caller_turn("my car broke down on Highway 9 mile 5")]))

    assert record.resolved is True
    assert record.chosen_option == "dispatch_tow"


def test_severity_threshold_constraint_rejects_standard_dispatch_for_emergency_severity():
    constraint = SeverityThresholdConstraint()
    result = constraint.check(
        intent="breakdown",
        chosen_option="dispatch_tow",
        known_facts={"severity": "emergency"},
        uncertainties=[],
    )
    assert result.passed is False
    assert isinstance(result, ConstraintResult)


def test_severity_threshold_constraint_rejects_unjustified_emergency_escalation():
    constraint = SeverityThresholdConstraint()
    result = constraint.check(
        intent="emergency",
        chosen_option="escalate_emergency",
        known_facts={"severity": "standard"},
        uncertainties=[],
    )
    assert result.passed is False


def test_severity_threshold_constraint_passes_consistent_cases():
    constraint = SeverityThresholdConstraint()
    ok_tow = constraint.check(
        intent="breakdown", chosen_option="dispatch_tow", known_facts={"severity": "standard"}, uncertainties=[]
    )
    ok_emergency = constraint.check(
        intent="emergency",
        chosen_option="escalate_emergency",
        known_facts={"severity": "emergency"},
        uncertainties=[],
    )
    assert ok_tow.passed is True
    assert ok_emergency.passed is True


def test_required_fields_constraint_structured_result_has_evidence():
    constraint = RequiredFieldsConstraint(required_fields_by_option={"dispatch_tow": ["location"]})
    result = constraint.check(
        intent="breakdown", chosen_option="dispatch_tow", known_facts={}, uncertainties=["location"]
    )
    assert result.passed is False
    assert result.name == "required_fields"
    assert "missing_fields" in result.evidence
    assert result.evidence["missing_fields"] == ["location"]


def test_service_radius_constraint_skips_when_no_distance_evidence():
    constraint = ServiceRadiusConstraint(max_radius_miles=25)
    result = constraint.check(
        intent="breakdown", chosen_option="dispatch_tow", known_facts={"location": "Hwy 9"}, uncertainties=[]
    )
    assert result.passed is True


def test_failed_constraints_do_not_produce_unsafe_resolved_decision():
    """Safe fallback (section 17): a failed constraint must never leave resolved=True."""
    engine, _ = make_engine()
    missing_location = run(engine.deliberate("call-1", [caller_turn("my car broke down")]))
    too_far = run(engine.deliberate("call-2", [caller_turn("my car broke down on Highway 9 mile 99")]))

    assert missing_location.resolved is False
    assert too_far.resolved is False
    for record in (missing_location, too_far):
        assert record.passed_all_constraints is False
        assert record.chosen_option is None


# --- self-correction & contradiction / supersession (section 16, 19) ---


def test_self_correction_when_missing_info_is_later_provided():
    engine, _ = make_engine()
    first = run(engine.deliberate("call-1", [caller_turn("my car broke down")]))
    assert first.resolved is False

    context = [caller_turn("my car broke down"), caller_turn("I'm on Highway 9")]
    second = run(engine.deliberate("call-1", context))

    assert second.resolved is True
    assert second.chosen_option == "dispatch_tow"
    assert second.supersedes == first.decision_id
    assert first.superseded_by == second.decision_id
    # the old record's own content must remain untouched
    assert first.resolved is False
    assert first.chosen_option is None


def test_contradiction_fire_after_breakdown_supersedes_without_mutating_old_record():
    engine, received = make_engine()
    context_v1 = [caller_turn("my car broke down on Highway 9")]
    first = run(engine.deliberate("call-1", context_v1))
    assert first.intent == "breakdown"
    assert first.chosen_option == "dispatch_tow"

    context_v2 = context_v1 + [caller_turn("wait, actually there's a fire")]
    second = run(engine.deliberate("call-1", context_v2))

    assert second.intent == "emergency"
    assert second.chosen_option == "escalate_emergency"
    assert second.supersedes == first.decision_id
    assert first.superseded_by == second.decision_id

    # old record's reasoning content is completely untouched
    assert first.intent == "breakdown"
    assert first.chosen_option == "dispatch_tow"
    assert first.rationale != second.rationale

    re_deliberations = [e for e in received if isinstance(e, ReDeliberationTriggered)]
    assert len(re_deliberations) == 1
    assert re_deliberations[0].supersedes_id == first.decision_id
    assert re_deliberations[0].decision_id == second.decision_id
    assert "breakdown" in re_deliberations[0].reason and "emergency" in re_deliberations[0].reason


def test_unrelated_case_closure_does_not_supersede_incident_decision():
    """Case closure is a different decision_group -- it must not be treated
    as a contradiction of a prior breakdown/emergency decision."""
    engine, received = make_engine()
    run(engine.deliberate("call-1", [caller_turn("my car broke down on Highway 9")]))
    run(engine.deliberate("call-1", [caller_turn("my car broke down on Highway 9"), caller_turn("that's all, thanks")]))

    re_deliberations = [e for e in received if isinstance(e, ReDeliberationTriggered)]
    assert len(re_deliberations) == 0


def test_engine_record_and_latest_for_lookup():
    engine, _ = make_engine()
    record = run(engine.deliberate("call-1", [caller_turn("my car broke down on Highway 9")]))

    assert engine.record(record.decision_id) is record
    assert engine.latest_for("call-1", "incident_response") is record
    assert engine.latest_for("call-1", "case_management") is None


# --- registry extensibility (section 19) ---


class _MedicalEmergencyIntent(IntentHandler):
    name = "medical_emergency"
    decision_group = "incident_response"

    def detect(self, context):
        text = " ".join(t.text.lower() for t in context if t.speaker == "caller")
        return 0.99 if "chest pain" in text else None

    def deliberate(self, context):
        return IntentDeliberationResult(
            known_facts={"severity": "emergency", "reason": "chest pain", "location": "Hwy 9"},
            uncertainties=[],
            options_considered=[Option(action_type="escalate_emergency", description="Send an ambulance")],
            chosen_option="escalate_emergency",
            rationale="Caller reports chest pain; escalating.",
            confidence=0.95,
        )


def test_new_intent_can_be_registered_without_modifying_the_engine():
    registry = default_intent_registry()
    registry.register(_MedicalEmergencyIntent())

    bus = EventBus()
    engine = DeliberationEngine(bus=bus, intent_registry=registry)
    record = run(engine.deliberate("call-1", [caller_turn("caller reports chest pain")]))

    assert record.intent == "medical_emergency"
    assert record.chosen_option == "escalate_emergency"


class _NightShiftOnlyConstraint(ConstraintCheck):
    name = "night_shift_only"

    def check(self, *, intent, chosen_option, known_facts, uncertainties):
        return ConstraintResult(name=self.name, passed=False, reason="no crew available right now")


def test_new_constraint_can_be_registered_without_modifying_the_engine():
    registry = default_constraint_registry()
    registry.register(_NightShiftOnlyConstraint())

    bus = EventBus()
    engine = DeliberationEngine(bus=bus, constraint_registry=registry)
    record = run(engine.deliberate("call-1", [caller_turn("my car broke down on Highway 9")]))

    assert record.resolved is False
    failed_names = [c.name for c in record.failed_constraints]
    assert "night_shift_only" in failed_names


def test_intent_registry_best_match_prefers_higher_confidence():
    registry = IntentRegistry()

    class Low(IntentHandler):
        name = "low"
        decision_group = "g"

        def detect(self, context):
            return 0.1

        def deliberate(self, context):
            return IntentDeliberationResult(chosen_option="low_action", confidence=0.5)

    class High(IntentHandler):
        name = "high"
        decision_group = "g"

        def detect(self, context):
            return 0.9

        def deliberate(self, context):
            return IntentDeliberationResult(chosen_option="high_action", confidence=0.5)

    registry.register(Low())
    registry.register(High())
    best = registry.best_match([caller_turn("anything")])
    assert best.name == "high"


# --- confidence bounds (section 19) ---


def test_all_produced_records_have_confidence_in_bounds():
    engine, _ = make_engine()
    scenarios = [
        [caller_turn("my car broke down on Highway 9")],
        [caller_turn("my car broke down")],
        [caller_turn("there's a fire")],
        [caller_turn("that's all, thanks")],
        [caller_turn("hello")],
    ]
    for i, context in enumerate(scenarios):
        record = run(engine.deliberate(f"call-{i}", context))
        assert 0.0 <= record.confidence <= 1.0


# --- events (section 19) ---


def test_deliberation_started_and_resolved_are_emitted_for_every_pass():
    engine, received = make_engine()
    run(engine.deliberate("call-1", [caller_turn("my car broke down on Highway 9")]))

    started = [e for e in received if isinstance(e, DeliberationStarted)]
    resolved = [e for e in received if isinstance(e, DeliberationResolved)]
    assert len(started) == 1
    assert len(resolved) == 1
    assert started[0].intent == "breakdown"
    assert resolved[0].chosen == "dispatch_tow"


def test_self_critique_failed_is_emitted_when_a_constraint_fails():
    engine, received = make_engine()
    run(engine.deliberate("call-1", [caller_turn("my car broke down")]))

    failures = [e for e in received if isinstance(e, SelfCritiqueFailed)]
    assert len(failures) == 1
    assert failures[0].failed_constraint == "required_fields"
    assert failures[0].fallback_action == FALLBACK_REQUEST_MORE_INFO


def test_self_critique_failed_is_not_emitted_when_everything_passes():
    engine, received = make_engine()
    run(engine.deliberate("call-1", [caller_turn("my car broke down on Highway 9")]))

    failures = [e for e in received if isinstance(e, SelfCritiqueFailed)]
    assert len(failures) == 0


def test_re_deliberation_triggered_is_emitted_on_supersession_only():
    engine, received = make_engine()
    run(engine.deliberate("call-1", [caller_turn("my car broke down on Highway 9")]))
    assert not any(isinstance(e, ReDeliberationTriggered) for e in received)

    run(
        engine.deliberate(
            "call-1",
            [caller_turn("my car broke down on Highway 9"), caller_turn("actually there's a fire")],
        )
    )
    re_deliberations = [e for e in received if isinstance(e, ReDeliberationTriggered)]
    assert len(re_deliberations) == 1


def test_events_are_json_serializable():
    import json

    engine, received = make_engine()
    run(engine.deliberate("call-1", [caller_turn("my car broke down")]))
    for event in received:
        json.dumps(event.to_dict())


# --- interruption strategy (section 18, 19) ---


def test_interruption_strategy_resumes_on_irrelevant_clarification():
    engine, _ = make_engine()
    strategy = DeliberationBackedInterruptionStrategy(engine)
    resolution = strategy.decide([caller_turn("um, sorry, what?")])
    assert resolution == InterruptionResolution.RESUME


def test_interruption_strategy_targeted_follow_up_when_info_missing():
    engine, _ = make_engine()
    strategy = DeliberationBackedInterruptionStrategy(engine)
    resolution = strategy.decide([caller_turn("my car broke down")])
    assert resolution == InterruptionResolution.TARGETED_FOLLOW_UP


def test_interruption_strategy_replans_on_emergency():
    engine, _ = make_engine()
    strategy = DeliberationBackedInterruptionStrategy(engine)
    resolution = strategy.decide(
        [caller_turn("my car broke down on Highway 9"), caller_turn("wait, there's a fire")]
    )
    assert resolution == InterruptionResolution.REPLAN


def test_interruption_strategy_resumes_when_fully_resolved():
    engine, _ = make_engine()
    strategy = DeliberationBackedInterruptionStrategy(engine)
    resolution = strategy.decide([caller_turn("my car broke down on Highway 9")])
    assert resolution == InterruptionResolution.RESUME


def test_interruption_strategy_is_injectable_into_dialogue_engine():
    """The Phase 3 seam (core/dialogue/engine.py InterruptionStrategy) accepts
    this Phase 4 strategy without any change to DialogueEngine itself."""
    from core.dialogue.engine import DialogueEngine
    from providers.mock.mock_audio_io import AudioEvent, MockAudioIO
    from providers.mock.mock_llm import MockLLM
    from providers.mock.mock_stt import MockSTT
    from providers.mock.mock_tts import MockTTS
    from providers.interfaces import TranscriptEvent

    audio_io = MockAudioIO([AudioEvent("caller_speech_onset", 0)])

    async def scenario():
        await audio_io.start()
        await audio_io.next_event()

        bus = EventBus()
        engine = DeliberationEngine(bus=bus)
        dialogue_engine = DialogueEngine(
            call_id="call-1",
            stt=MockSTT(),
            llm=MockLLM(),
            tts=MockTTS(),
            audio_io=audio_io,
            bus=bus,
            interruption_strategy=DeliberationBackedInterruptionStrategy(engine),
        )
        await dialogue_engine.handle_transcript(
            TranscriptEvent("caller", "my car broke down", True, 0)
        )
        return await dialogue_engine.check_for_barge_in()

    barge_in_event = run(scenario())
    assert barge_in_event is not None
