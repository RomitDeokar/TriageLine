"""Deterministic, offline mock STT provider.

Emits a pre-scripted sequence of transcript events in order. Intended for
tests and the replay harness — no microphone, no network.
"""

from __future__ import annotations

from typing import Optional

from providers.interfaces import STTProvider, TranscriptEvent


class MockSTT(STTProvider):
    def __init__(self, script: Optional[list[TranscriptEvent]] = None) -> None:
        self._script = list(script or [])
        self._index = 0
        self._started = False

    def load_script(self, script: list[TranscriptEvent]) -> None:
        self._script = list(script)
        self._index = 0

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    async def next_transcript(self) -> Optional[TranscriptEvent]:
        if not self._started or self._index >= len(self._script):
            return None
        event = self._script[self._index]
        self._index += 1
        return event
