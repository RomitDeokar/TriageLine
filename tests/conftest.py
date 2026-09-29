"""Hermetic test environment: no ambient provider keys or deployment settings leak into tests.

Individual tests opt in to providers with monkeypatch.setenv(...).
"""
import os

import pytest

_SCRUB = (
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY",
    "OPENROUTER_API_KEY", "MISTRAL_API_KEY", "DEEPGRAM_API_KEY", "TRIAGELINE_LLM_API_KEY",
    "TRIAGELINE_LLM_BASE_URL", "TRIAGELINE_LLM_CHAIN", "TRIAGELINE_LLM_PROVIDER", "TRIAGELINE_LLM_MODEL",
    "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "TRIAGELINE_ENV", "TRIAGELINE_API_KEY",
    "TRIAGELINE_ACCESS_CODE", "TRIAGELINE_SESSION_SECRET", "TRIAGELINE_MODE",
    "TRIAGELINE_AGENT_NAME", "TRIAGELINE_TRIAGE_AGENT_NAME",
)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    for name in _SCRUB:
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith("TRIAGELINE_LLM_MODEL_") or name.startswith("TRIAGELINE_LLM_BASE_URL_"):
            monkeypatch.delenv(name, raising=False)
    try:
        from agent import providers
        providers.reset_state()
    except Exception:  # noqa: BLE001
        pass
    yield
