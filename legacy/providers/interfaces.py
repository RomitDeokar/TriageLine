"""Provider interfaces: STTProvider, LLMProvider, TTSProvider, AudioIO.

These describe the capabilities the dialogue engine will eventually need
from a speech-to-text, language-model, text-to-speech, or audio-transport
provider, without committing to any concrete implementation. Concrete
adapters live in providers/mock/ (offline, deterministic) and
providers/live/ (real services, added later).

No LiveKit/Deepgram/Groq/Rime/FastAPI/React/database imports belong here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TranscriptEvent:
    """A single partial or final transcript emitted by an STT provider."""

    speaker: str
    text: str
    is_final: bool
    timestamp_ms: int


@dataclass
class ActionProposal:
    """A consequential action an LLM response may propose.

    This only describes what was proposed. Deciding whether to actually
    take the action is the deliberation engine's job (a later phase), not
    the LLM provider's.
    """

    action_type: str
    details: dict = field(default_factory=dict)


@dataclass
class ConversationTurn:
    """One turn of conversation passed to an LLM provider as context."""

    speaker: str
    text: str


@dataclass
class LLMResponse:
    """Structured output of an LLM provider's response to a conversation."""

    text: str
    intent: Optional[str] = None
    action_proposal: Optional[ActionProposal] = None
    needs_more_info: bool = False


class STTProvider(ABC):
    """Speech-to-text: turns caller audio (or scripted input) into transcripts."""

    @abstractmethod
    async def start(self) -> None:
        """Begin producing transcript events."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop producing transcript events."""

    @abstractmethod
    async def next_transcript(self) -> Optional[TranscriptEvent]:
        """Return the next transcript event, or None if none remain."""


class LLMProvider(ABC):
    """Language model: turns conversation context into a structured response."""

    @abstractmethod
    async def respond(self, context: list[ConversationTurn]) -> LLMResponse:
        """Produce a response given the conversation so far."""


class TTSProvider(ABC):
    """Text-to-speech: manages the lifecycle of speaking a piece of text."""

    @abstractmethod
    async def start_speaking(self, text: str) -> None:
        """Begin speaking `text`."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop speaking, if currently speaking."""

    @abstractmethod
    def is_speaking(self) -> bool:
        """Whether playback is currently in progress."""

    @abstractmethod
    def progress(self) -> float:
        """Playback progress from 0.0 (just started) to 1.0 (finished)."""

    @abstractmethod
    def was_interrupted(self) -> bool:
        """Whether the most recent playback was stopped before finishing."""


class AudioIO(ABC):
    """Transport-level audio: caller speech detection and agent playback control."""

    @abstractmethod
    async def start(self) -> None:
        """Begin the audio transport session."""

    @abstractmethod
    async def stop(self) -> None:
        """End the audio transport session."""

    @abstractmethod
    def is_caller_speaking(self) -> bool:
        """Whether the caller is currently detected as speaking."""

    @abstractmethod
    def is_agent_playing(self) -> bool:
        """Whether agent audio is currently playing."""

    @abstractmethod
    async def stop_agent_playback(self) -> None:
        """Stop agent audio playback immediately (used for barge-in)."""
