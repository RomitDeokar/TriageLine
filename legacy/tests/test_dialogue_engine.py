"""Tests for the DialogueEngine (Phase 3).

The engine depends only on the provider interfaces from Phase 2. These
tests inject the Phase 2 mock providers as one valid implementation of
those interfaces — see test_dialogue_engine_module_has_no_concrete_provider_imports
for the check that the engine module itself never imports them directly.
"""

import asyncio
import inspect

from core.dialogue.engine import DialogueEngine, InterruptionResolution
from core.dialogue.turn_state import TurnState
from core.event_bus import EventBus
from core.events import BackchannelSent, BargeIn, FinalTranscript, PartialTranscript, TurnStarted
from providers.interfaces import TranscriptEvent
from providers.mock.mock_audio_io import AudioEvent, MockAudioIO
from providers.mock.mock_llm import MockLLM
from providers.mock.mock_stt import MockSTT
from providers.mock.mock_tts import MockTTS


def run(coro):
    return asyncio.run(coro)


def make_engine(audio_io=None):
    bus = EventBus()
    received = []

    async def record(event):
        received.append(event)

    run(bus.subscribe(record))

    providers = {
        "stt": MockSTT(),
        "llm": MockLLM(),
        "tts": MockTTS(),
        "audio_io": audio_io or MockAudioIO(),
    }
    engine = DialogueEngine(call_id="call-1", bus=bus, **providers)
    return engine, received, providers


def _caller_speaking_audio_io() -> MockAudioIO:
    """A MockAudioIO already advanced to report the caller speaking."""
    audio_io = MockAudioIO([AudioEvent("caller_speech_onset", 0)])

    async def setup():
        await audio_io.start()
        await audio_io.next_event()

    run(setup())
    return audio_io


# --- provider independence ---


def test_dialogue_engine_module_has_no_concrete_provider_imports():
    import core.dialogue.engine as engine_module

    source = inspect.getsource(engine_module)
    for forbidden in ("MockSTT", "MockLLM", "MockTTS", "MockAudioIO"):
        assert forbidden not in source


def test_providers_are_injectable_not_hardcoded():
    engine_a, _, providers_a = make_engine()
    engine_b, _, providers_b = make_engine()
    assert engine_a is not engine_b
    assert providers_a["stt"] is not providers_b["stt"]


# --- normal turn handling ---


def test_partial_transcript_is_published_and_does_not_finalize_turn():
    engine, received, _ = make_engine()
    run(engine.handle_transcript(TranscriptEvent("caller", "my car...", False, 0)))

    partials = [e for e in received if isinstance(e, PartialTranscript)]
    assert len(partials) == 1
    assert partials[0].text == "my car..."
    assert engine.turn_state.state == TurnState.CALLER_SPEAKING


def test_final_transcript_updates_context_and_publishes_event():
    engine, received, _ = make_engine()
    run(engine.handle_transcript(TranscriptEvent("caller", "my car broke down", True, 0)))

    finals = [e for e in received if isinstance(e, FinalTranscript)]
    assert len(finals) == 1
    assert finals[0].text == "my car broke down"
    assert engine.context[0].speaker == "caller"
    assert engine.context[0].text == "my car broke down"


def test_final_transcript_triggers_agent_response_and_playback():
    engine, received, providers = make_engine()
    run(
        engine.handle_transcript(
            TranscriptEvent("caller", "my car broke down on Highway 9", True, 0)
        )
    )

    assert engine.turn_state.state == TurnState.AGENT_SPEAKING
    assert providers["tts"].is_speaking() is True
    agent_turns = [t for t in engine.context if t.speaker == "agent"]
    assert len(agent_turns) == 1
    turn_started = [e for e in received if isinstance(e, TurnStarted) and e.speaker == "agent"]
    assert len(turn_started) == 1


def test_normal_turn_sequence_partial_partial_final_in_order():
    engine, received, _ = make_engine()
    run(engine.handle_transcript(TranscriptEvent("caller", "my car...", False, 0)))
    run(engine.handle_transcript(TranscriptEvent("caller", "my car broke...", False, 100)))
    run(engine.handle_transcript(TranscriptEvent("caller", "my car broke down", True, 200)))

    transcript_events = [e for e in received if isinstance(e, (PartialTranscript, FinalTranscript))]
    kinds = [type(e).__name__ for e in transcript_events]
    assert kinds == ["PartialTranscript", "PartialTranscript", "FinalTranscript"]


# --- backchanneling ---


def test_backchannel_emitted_while_caller_still_speaking():
    engine, received, _ = make_engine()
    run(engine.handle_transcript(TranscriptEvent("caller", "my car...", False, 0)))
    run(engine.handle_transcript(TranscriptEvent("caller", "my car broke...", False, 100)))

    backchannels = [e for e in received if isinstance(e, BackchannelSent)]
    assert len(backchannels) >= 1


def test_backchannel_does_not_end_caller_turn():
    engine, _, _ = make_engine()
    run(engine.handle_transcript(TranscriptEvent("caller", "my car...", False, 0)))
    run(engine.handle_transcript(TranscriptEvent("caller", "my car broke...", False, 100)))

    assert engine.turn_state.state == TurnState.CALLER_SPEAKING


# --- barge-in ---


def test_barge_in_stops_playback_and_is_emitted():
    audio_io = _caller_speaking_audio_io()
    engine, received, providers = make_engine(audio_io=audio_io)
    run(
        engine.handle_transcript(
            TranscriptEvent("caller", "my car broke down on Highway 9", True, 0)
        )
    )
    assert engine.turn_state.state == TurnState.AGENT_SPEAKING

    barge_in_event = run(engine.check_for_barge_in())

    assert barge_in_event is not None
    assert providers["tts"].is_speaking() is False
    assert providers["tts"].was_interrupted() is True
    assert engine.turn_state.state == TurnState.CALLER_SPEAKING
    published = [e for e in received if isinstance(e, BargeIn)]
    assert len(published) == 1


def test_barge_in_latency_is_measured():
    audio_io = _caller_speaking_audio_io()
    engine, _, _ = make_engine(audio_io=audio_io)
    run(
        engine.handle_transcript(
            TranscriptEvent("caller", "my car broke down on Highway 9", True, 0)
        )
    )
    barge_in_event = run(engine.check_for_barge_in())

    assert isinstance(barge_in_event.latency_ms, int)
    assert barge_in_event.latency_ms >= 0


def test_no_barge_in_when_caller_not_speaking():
    engine, _, _ = make_engine()
    run(
        engine.handle_transcript(
            TranscriptEvent("caller", "my car broke down on Highway 9", True, 0)
        )
    )
    assert run(engine.check_for_barge_in()) is None


def test_no_barge_in_when_agent_not_speaking():
    audio_io = _caller_speaking_audio_io()
    engine, _, _ = make_engine(audio_io=audio_io)
    assert run(engine.check_for_barge_in()) is None


def test_caller_input_continues_after_interruption():
    audio_io = _caller_speaking_audio_io()
    engine, _, _ = make_engine(audio_io=audio_io)
    run(
        engine.handle_transcript(
            TranscriptEvent("caller", "my car broke down on Highway 9", True, 0)
        )
    )
    run(engine.check_for_barge_in())

    run(engine.handle_transcript(TranscriptEvent("caller", "wait, it's on fire actually", True, 500)))

    caller_turns = [t for t in engine.context if t.speaker == "caller"]
    assert caller_turns[-1].text == "wait, it's on fire actually"
    assert engine.last_interruption_resolution == InterruptionResolution.REPLAN


def test_interruption_strategy_seam_is_injectable():
    class AlwaysResume:
        def decide(self, context):
            return InterruptionResolution.RESUME

    audio_io = _caller_speaking_audio_io()
    bus = EventBus()
    engine = DialogueEngine(
        call_id="call-2",
        stt=MockSTT(),
        llm=MockLLM(),
        tts=MockTTS(),
        audio_io=audio_io,
        bus=bus,
        interruption_strategy=AlwaysResume(),
    )
    run(
        engine.handle_transcript(
            TranscriptEvent("caller", "my car broke down on Highway 9", True, 0)
        )
    )
    run(engine.check_for_barge_in())

    assert engine.last_interruption_resolution == InterruptionResolution.RESUME
