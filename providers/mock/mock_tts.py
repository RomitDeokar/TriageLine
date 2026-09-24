"""Deterministic, offline mock TTS provider.

Playback progress advances via an explicit `advance()` call rather than
real wall-clock time, so tests stay fast and reproducible instead of
relying on sleep().
"""

from __future__ import annotations

from providers.interfaces import TTSProvider

_MS_PER_WORD = 300


class MockTTS(TTSProvider):
    def __init__(self) -> None:
        self._speaking = False
        self._interrupted = False
        self._elapsed_ms = 0
        self._duration_ms = 0

    async def start_speaking(self, text: str) -> None:
        word_count = max(1, len(text.split()))
        self._duration_ms = word_count * _MS_PER_WORD
        self._elapsed_ms = 0
        self._speaking = True
        self._interrupted = False

    async def stop(self) -> None:
        if self._speaking and self._elapsed_ms < self._duration_ms:
            self._interrupted = True
        self._speaking = False

    def advance(self, ms: int) -> None:
        """Advance simulated playback time. Test/harness-only helper."""
        if not self._speaking:
            return
        self._elapsed_ms = min(self._duration_ms, self._elapsed_ms + ms)
        if self._elapsed_ms >= self._duration_ms:
            self._speaking = False

    def is_speaking(self) -> bool:
        return self._speaking

    def progress(self) -> float:
        if self._duration_ms == 0:
            return 0.0
        return self._elapsed_ms / self._duration_ms

    def was_interrupted(self) -> bool:
        return self._interrupted
