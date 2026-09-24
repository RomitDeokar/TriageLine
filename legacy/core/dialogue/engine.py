"""Dialogue engine: turn-taking, barge-in, and backchanneling.

Depends only on the provider interfaces from providers/interfaces.py —
never on a concrete provider implementation, offline or live. All
provider instances are injected through the constructor.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Callable, Optional, Protocol

from core.dialogue.turn_state import TurnState, TurnStateMachine
from core.event_bus import EventBus
from core.events import (
    BackchannelSent,
    BargeIn,
    FinalTranscript,
    MetricsTick,
    PartialTranscript,
    TurnStarted,
)
from providers.interfaces import (
    AudioIO,
    ConversationTurn,
    LLMProvider,
    LLMResponse,
    STTProvider,
    TTSProvider,
    TranscriptEvent,
)


class InterruptionResolution(Enum):
    """What the engine should do after a barge-in.

    Deciding *which* of these applies is the deliberation engine's job
    (Phase 4) — this enum and the seam below just give the dialogue
    engine something to call in the meantime.
    """

    RESUME = "resume"
    TARGETED_FOLLOW_UP = "targeted_follow_up"
    REPLAN = "replan"


class InterruptionStrategy(Protocol):
    def decide(self, context: list[ConversationTurn]) -> InterruptionResolution: ...


class DefaultInterruptionStrategy:
    """Temporary placeholder strategy — NOT real deliberation.

    Always re-plans from whatever the caller said instead. This is a
    safe, simple default that keeps the engine functional; it is meant
    to be replaced by the Phase 4 deliberation engine, which will decide
    this based on actual reasoning about what was interrupted and what
    the caller said.
    """

    def decide(self, context: list[ConversationTurn]) -> InterruptionResolution:
        return InterruptionResolution.REPLAN


def _default_clock() -> Callable[[], int]:
    start = time.perf_counter()
    return lambda: int((time.perf_counter() - start) * 1000)


class DialogueEngine:
    BACKCHANNEL_WORDS = ("mm-hm", "okay, go on", "got it")

    def __init__(
        self,
        call_id: str,
        stt: STTProvider,
        llm: LLMProvider,
        tts: TTSProvider,
        audio_io: AudioIO,
        bus: EventBus,
        interruption_strategy: Optional[InterruptionStrategy] = None,
        clock_ms: Optional[Callable[[], int]] = None,
    ) -> None:
        self._call_id = call_id
        self._stt = stt
        self._llm = llm
        self._tts = tts
        self._audio_io = audio_io
        self._bus = bus
        self._interruption_strategy = interruption_strategy or DefaultInterruptionStrategy()
        self._clock_ms = clock_ms or _default_clock()

        self.turn_state = TurnStateMachine()
        self.context: list[ConversationTurn] = []
        self.last_interruption_resolution: Optional[InterruptionResolution] = None

        self._caller_turn_active = False
        self._partial_count = 0

    # --- transcript handling ---

    async def handle_transcript(self, transcript: TranscriptEvent) -> None:
        if transcript.is_final:
            await self._handle_final_transcript(transcript)
        else:
            await self._handle_partial_transcript(transcript)

    async def _handle_partial_transcript(self, transcript: TranscriptEvent) -> None:
        if not self._caller_turn_active:
            self._caller_turn_active = True
            self._partial_count = 0
            await self._bus.publish(
                TurnStarted(call_id=self._call_id, timestamp_ms=self._clock_ms(), speaker="caller")
            )
        self.turn_state.caller_starts_speaking()

        await self._bus.publish(
            PartialTranscript(
                call_id=self._call_id,
                timestamp_ms=self._clock_ms(),
                speaker=transcript.speaker,
                text=transcript.text,
            )
        )
        self._partial_count += 1
        await self._maybe_backchannel()

    async def _handle_final_transcript(self, transcript: TranscriptEvent) -> None:
        await self._bus.publish(
            FinalTranscript(
                call_id=self._call_id,
                timestamp_ms=self._clock_ms(),
                speaker=transcript.speaker,
                text=transcript.text,
            )
        )
        self.context.append(ConversationTurn(speaker=transcript.speaker, text=transcript.text))
        self._caller_turn_active = False
        self._partial_count = 0
        self.turn_state.caller_stops_speaking()
        await self._produce_agent_response()

    async def _maybe_backchannel(self) -> None:
        """Deterministic placeholder strategy: backchannel every other partial.

        Kept behind this one small method so a smarter (e.g. confidence-
        based) strategy can replace it later without touching callers.
        """
        if self._partial_count % 2 != 0:
            return
        word_index = (self._partial_count // 2 - 1) % len(self.BACKCHANNEL_WORDS)
        word = self.BACKCHANNEL_WORDS[word_index]
        await self._bus.publish(
            BackchannelSent(call_id=self._call_id, timestamp_ms=self._clock_ms(), text=word)
        )

    async def _produce_agent_response(self) -> LLMResponse:
        response = await self._llm.respond(self.context)
        await self._bus.publish(
            TurnStarted(call_id=self._call_id, timestamp_ms=self._clock_ms(), speaker="agent")
        )
        self.turn_state.agent_starts_speaking()
        await self._tts.start_speaking(response.text)
        self.context.append(ConversationTurn(speaker="agent", text=response.text))
        return response

    # --- barge-in ---

    async def check_for_barge_in(self) -> Optional[BargeIn]:
        """Check the AudioIO signal for caller speech onset during agent playback.

        Only uses AudioIO's abstract interface (is_caller_speaking,
        is_agent_playing-adjacent state, stop_agent_playback) — advancing
        what a concrete AudioIO reports is the caller's/harness's job,
        not this method's.
        """
        if self.turn_state.state != TurnState.AGENT_SPEAKING:
            return None
        if not self._audio_io.is_caller_speaking():
            return None

        detection_started = time.perf_counter()
        position_ms = int(self._tts.progress() * 1000)  # proxy: progress fraction, not true ms

        self.turn_state.caller_starts_speaking()  # AGENT_SPEAKING -> INTERRUPTED
        await self._tts.stop()
        await self._audio_io.stop_agent_playback()
        latency_ms = int((time.perf_counter() - detection_started) * 1000)

        event = BargeIn(
            call_id=self._call_id,
            timestamp_ms=self._clock_ms(),
            position_ms=position_ms,
            latency_ms=latency_ms,
        )
        await self._bus.publish(event)
        await self._bus.publish(
            MetricsTick(
                call_id=self._call_id,
                timestamp_ms=self._clock_ms(),
                barge_in_latency_ms=latency_ms,
            )
        )

        self.turn_state.acknowledge_interruption()  # INTERRUPTED -> CALLER_SPEAKING
        self.last_interruption_resolution = self._interruption_strategy.decide(self.context)
        return event

    # --- convenience driver for offline scripted runs ---

    async def run_call(self) -> None:
        """Pull transcripts from the injected STT until exhausted.

        A barge-in opportunity is checked before each transcript via the
        abstract AudioIO interface; advancing what a mock AudioIO
        reports is external to this method (a test or harness's job).
        """
        await self._stt.start()
        while True:
            await self.check_for_barge_in()
            transcript = await self._stt.next_transcript()
            if transcript is None:
                break
            await self.handle_transcript(transcript)
        await self._stt.stop()
