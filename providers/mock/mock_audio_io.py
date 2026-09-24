"""Deterministic, offline mock AudioIO.

Drives caller-speech / agent-playback state from a scripted sequence of
events instead of real audio hardware or a network transport.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from providers.interfaces import AudioIO

_CALLER_SPEECH_EVENTS = {"caller_speech_onset", "caller_speech_continue"}


@dataclass
class AudioEvent:
    type: str
    timestamp_ms: int


class MockAudioIO(AudioIO):
    def __init__(self, script: Optional[list[AudioEvent]] = None) -> None:
        self._script = list(script or [])
        self._index = 0
        self._caller_speaking = False
        self._agent_playing = False
        self._started = False

    def load_script(self, script: list[AudioEvent]) -> None:
        self._script = list(script)
        self._index = 0

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False
        self._caller_speaking = False
        self._agent_playing = False

    def is_caller_speaking(self) -> bool:
        return self._caller_speaking

    def is_agent_playing(self) -> bool:
        return self._agent_playing

    async def stop_agent_playback(self) -> None:
        self._agent_playing = False

    async def next_event(self) -> Optional[AudioEvent]:
        """Advance to and return the next scripted event, updating state."""
        if not self._started or self._index >= len(self._script):
            return None
        event = self._script[self._index]
        self._index += 1
        self._apply(event)
        return event

    def _apply(self, event: AudioEvent) -> None:
        if event.type in _CALLER_SPEECH_EVENTS:
            self._caller_speaking = True
        elif event.type == "caller_speech_end":
            self._caller_speaking = False
        elif event.type == "agent_playback_start":
            self._agent_playing = True
        elif event.type == "agent_playback_stop":
            self._agent_playing = False
        elif event.type == "caller_interrupt":
            self._caller_speaking = True
            self._agent_playing = False
