"""Tests for the offline mock provider layer (Phase 2).

All tests run fully offline: no network, no API keys, no audio hardware.
Async provider methods are driven with asyncio.run() directly rather than
a pytest-asyncio plugin, so the test suite has no extra dependency beyond
pytest itself.
"""

import asyncio

from providers.interfaces import (
    AudioIO,
    ConversationTurn,
    LLMProvider,
    STTProvider,
    TranscriptEvent,
    TTSProvider,
)
from providers.mock.mock_audio_io import AudioEvent, MockAudioIO
from providers.mock.mock_llm import MockLLM
from providers.mock.mock_stt import MockSTT
from providers.mock.mock_tts import MockTTS


def run(coro):
    return asyncio.run(coro)


# --- interface compatibility ---


def test_mock_stt_implements_interface():
    assert isinstance(MockSTT(), STTProvider)


def test_mock_llm_implements_interface():
    assert isinstance(MockLLM(), LLMProvider)


def test_mock_tts_implements_interface():
    assert isinstance(MockTTS(), TTSProvider)


def test_mock_audio_io_implements_interface():
    assert isinstance(MockAudioIO(), AudioIO)


# --- MockSTT ---


def test_stt_emits_partial_then_final_in_order():
    script = [
        TranscriptEvent("caller", "my car...", False, 0),
        TranscriptEvent("caller", "my car broke...", False, 200),
        TranscriptEvent("caller", "my car broke down", True, 400),
    ]
    stt = MockSTT(script)

    async def scenario():
        await stt.start()
        events = []
        while (event := await stt.next_transcript()) is not None:
            events.append(event)
        return events

    events = run(scenario())
    assert [e.text for e in events] == [s.text for s in script]
    assert events[0].is_final is False
    assert events[-1].is_final is True


def test_stt_returns_none_before_start_and_after_exhaustion():
    stt = MockSTT([TranscriptEvent("caller", "hi", True, 0)])

    async def scenario():
        before = await stt.next_transcript()
        await stt.start()
        first = await stt.next_transcript()
        second = await stt.next_transcript()
        return before, first, second

    before, first, second = run(scenario())
    assert before is None
    assert first is not None
    assert second is None


def test_stt_is_deterministic_across_runs():
    def emit_first():
        stt = MockSTT([TranscriptEvent("caller", "hello", True, 0)])

        async def scenario():
            await stt.start()
            return await stt.next_transcript()

        return run(scenario())

    assert emit_first() == emit_first()


# --- MockLLM ---


def test_llm_normal_response():
    llm = MockLLM()
    context = [ConversationTurn("caller", "hello there")]
    response = run(llm.respond(context))
    assert response.action_proposal is None
    assert response.needs_more_info is False


def test_llm_requests_missing_information():
    llm = MockLLM()
    context = [ConversationTurn("caller", "my car broke down")]
    response = run(llm.respond(context))
    assert response.needs_more_info is True
    assert response.action_proposal is None


def test_llm_proposes_action_once_location_known():
    llm = MockLLM()
    context = [ConversationTurn("caller", "my car broke down on Highway 9")]
    response = run(llm.respond(context))
    assert response.needs_more_info is False
    assert response.action_proposal is not None
    assert response.action_proposal.action_type == "dispatch_tow"


def test_llm_changes_decision_on_contradicting_input():
    llm = MockLLM()
    breakdown_context = [ConversationTurn("caller", "my car broke down on Highway 9")]
    first = run(llm.respond(breakdown_context))

    contradicting_context = breakdown_context + [
        ConversationTurn("caller", "wait, it's on fire actually")
    ]
    second = run(llm.respond(contradicting_context))

    assert first.action_proposal.action_type == "dispatch_tow"
    assert second.action_proposal.action_type == "escalate_emergency"


def test_llm_is_deterministic_across_repeated_calls():
    llm = MockLLM()
    context = [ConversationTurn("caller", "my car broke down on Highway 9")]
    assert run(llm.respond(context)) == run(llm.respond(context))


# --- MockTTS ---


def test_tts_starts_speaking():
    tts = MockTTS()
    run(tts.start_speaking("hello there friend"))
    assert tts.is_speaking() is True
    assert tts.progress() == 0.0


def test_tts_progress_advances_deterministically():
    tts = MockTTS()
    run(tts.start_speaking("one two three four"))
    tts.advance(300)
    first_progress = tts.progress()
    tts.advance(300)
    second_progress = tts.progress()
    assert 0.0 < first_progress < second_progress


def test_tts_completes_without_interruption():
    tts = MockTTS()
    run(tts.start_speaking("short"))
    tts.advance(10_000)
    assert tts.is_speaking() is False
    assert tts.progress() == 1.0
    assert tts.was_interrupted() is False


def test_tts_can_be_stopped_and_marks_interruption():
    tts = MockTTS()
    run(tts.start_speaking("one two three four five six"))
    tts.advance(300)
    run(tts.stop())
    assert tts.is_speaking() is False
    assert tts.was_interrupted() is True


def test_tts_stop_after_completion_is_not_an_interruption():
    tts = MockTTS()
    run(tts.start_speaking("short"))
    tts.advance(10_000)
    run(tts.stop())
    assert tts.was_interrupted() is False


# --- MockAudioIO ---


def test_audio_io_caller_speech_events():
    script = [
        AudioEvent("caller_speech_onset", 0),
        AudioEvent("caller_speech_continue", 100),
        AudioEvent("caller_speech_end", 500),
    ]
    audio = MockAudioIO(script)

    async def scenario():
        await audio.start()
        states = []
        for _ in range(len(script)):
            await audio.next_event()
            states.append(audio.is_caller_speaking())
        return states

    assert run(scenario()) == [True, True, False]


def test_audio_io_agent_playback_lifecycle():
    script = [
        AudioEvent("agent_playback_start", 0),
        AudioEvent("agent_playback_stop", 500),
    ]
    audio = MockAudioIO(script)

    async def scenario():
        await audio.start()
        await audio.next_event()
        mid = audio.is_agent_playing()
        await audio.next_event()
        end = audio.is_agent_playing()
        return mid, end

    mid, end = run(scenario())
    assert mid is True
    assert end is False


def test_audio_io_interruption_stops_agent_playback():
    script = [
        AudioEvent("agent_playback_start", 0),
        AudioEvent("caller_interrupt", 100),
    ]
    audio = MockAudioIO(script)

    async def scenario():
        await audio.start()
        await audio.next_event()
        await audio.next_event()

    run(scenario())
    assert audio.is_caller_speaking() is True
    assert audio.is_agent_playing() is False


def test_audio_io_explicit_stop_playback():
    script = [AudioEvent("agent_playback_start", 0)]
    audio = MockAudioIO(script)

    async def scenario():
        await audio.start()
        await audio.next_event()
        await audio.stop_agent_playback()

    run(scenario())
    assert audio.is_agent_playing() is False


def test_audio_io_no_events_before_start():
    audio = MockAudioIO([AudioEvent("caller_speech_onset", 0)])

    async def scenario():
        return await audio.next_event()

    assert run(scenario()) is None
