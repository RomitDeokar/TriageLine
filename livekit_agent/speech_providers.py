"""livekit_agent/speech_providers.py — one place that builds VAD + STT + TTS for both LiveKit agents.

Language understanding is NOT here: it is the rule-based ParticipantAgent (FDB-v3 agent) or the
legacy triage brain (extension). No LLM is involved. Only the hosted speech endpoints are swappable,
so the agent can run on free tiers (see docs/FREE_API_KEYS.md):

    TRIAGELINE_STT_PROVIDER = auto (default) | openai | groq | deepgram
        auto = deepgram nova-3 if DEEPGRAM_API_KEY is set, else groq whisper-large-v3-turbo if
        GROQ_API_KEY is set, else openai whisper-1. The stronger models are the documented default (C1).
    TRIAGELINE_TTS_PROVIDER = openai (default) | deepgram

    provider   STT model (override: TRIAGELINE_STT_MODEL)   TTS model/voice (TRIAGELINE_TTS_MODEL / _VOICE)
    openai     whisper-1                                     tts-1 / nova
    groq       whisper-large-v3-turbo (OpenAI-compatible)    -  (Groq TTS is WAV-only, 200-char cap: unsupported)
    deepgram   nova-3 (streaming)                            aura-2-andromeda-en

STT is biased with vocabulary taken from the tool manifest (C1): Whisper `prompt`, Deepgram `keyterm`.
Whisper runs at temperature 0 (B10). Disable biasing with TRIAGELINE_STT_BIAS=0.

`describe()` returns the exact models in use, so the start-up log is a truthful provider declaration.
"""
from __future__ import annotations

import os

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

STT_DEFAULTS = {"openai": "whisper-1", "groq": "whisper-large-v3-turbo", "deepgram": "nova-3"}
TTS_DEFAULTS = {"openai": ("tts-1", "nova"), "deepgram": ("aura-2-andromeda-en", None)}


class ProviderConfigError(RuntimeError):
    pass


def selected() -> dict:
    """Resolve the provider/model choice from the environment (pure function: unit-testable)."""
    stt = os.environ.get("TRIAGELINE_STT_PROVIDER", "auto").strip().lower()
    if stt == "auto":
        stt = "deepgram" if os.environ.get("DEEPGRAM_API_KEY") else \
            "groq" if os.environ.get("GROQ_API_KEY") else "openai"
    tts = os.environ.get("TRIAGELINE_TTS_PROVIDER", "openai").strip().lower()
    if stt not in STT_DEFAULTS:
        raise ProviderConfigError(f"TRIAGELINE_STT_PROVIDER={stt!r}; use one of {sorted(STT_DEFAULTS)}")
    if tts not in TTS_DEFAULTS:
        raise ProviderConfigError(f"TRIAGELINE_TTS_PROVIDER={tts!r}; use one of {sorted(TTS_DEFAULTS)}")
    need = {"openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY", "deepgram": "DEEPGRAM_API_KEY"}
    missing = sorted({need[p] for p in (stt, tts) if not os.environ.get(need[p])})
    tts_model, tts_voice = TTS_DEFAULTS[tts]
    return {
        "stt_provider": stt,
        "stt_model": os.environ.get("TRIAGELINE_STT_MODEL", STT_DEFAULTS[stt]),
        "tts_provider": tts,
        "tts_model": os.environ.get("TRIAGELINE_TTS_MODEL", tts_model),
        "tts_voice": os.environ.get("TRIAGELINE_TTS_VOICE", tts_voice or ""),
        "missing_keys": missing,
    }


def describe(cfg: dict | None = None) -> str:
    c = cfg or selected()
    voice = f"/{c['tts_voice']}" if c["tts_voice"] else ""
    brain = "rule-based agent (no LLM)"
    try:
        from agent import llm_planner
        if llm_planner.enabled():
            lc = llm_planner.config()
            brain = f"hybrid agent (rules + {lc['provider']}:{lc['model']} planner, T=0, seed={llm_planner.SEED})"
    except Exception:  # noqa: BLE001
        pass
    return (f"Silero VAD (local) -> {c['stt_provider']}:{c['stt_model']} STT -> {brain} "
            f"-> {c['tts_provider']}:{c['tts_model']}{voice} TTS")


def load_vad():
    from livekit.plugins import silero
    return silero.VAD.load(min_speech_duration=0.05, min_silence_duration=0.55)


def bias_terms(tools: dict | None = None) -> list:
    """Tool-manifest vocabulary for STT biasing (C1)."""
    if os.environ.get("TRIAGELINE_STT_BIAS", "1") != "1":
        return []
    if tools is None:
        try:
            from livekit_agent.fdb_tools import FDB_TOOLS as tools
        except Exception:  # noqa: BLE001
            return []
    from agent.nlu import tool_vocabulary
    return tool_vocabulary(tools)


def whisper_prompt(terms: list) -> str:
    return ("Customer support call. The caller may spell IDs letter by letter, e.g. order ID ABC123, "
            "item P52, flight DL555. Vocabulary: " + ", ".join(terms)) if terms else ""


def build_stt(cfg: dict, tools: dict | None = None):
    p, model = cfg["stt_provider"], cfg["stt_model"]
    terms = bias_terms(tools)
    if p == "deepgram":
        from livekit.plugins import deepgram
        kw = {"keyterm": terms[:50]} if terms and model.startswith("nova-3") else {}
        return deepgram.STT(model=model, language="en-US", interim_results=True, filler_words=True, **kw)
    from livekit.plugins import openai
    kw = {"temperature": 0.0}
    if terms:
        kw["prompt"] = whisper_prompt(terms)
    if p == "groq":  # OpenAI-compatible transcription endpoint, non-streaming (VAD-segmented)
        return openai.STT(model=model, language="en", base_url=GROQ_BASE_URL,
                          api_key=os.environ["GROQ_API_KEY"], use_realtime=False, **kw)
    return openai.STT(model=model, language="en", use_realtime=False, **kw)


def build_tts(cfg: dict):
    p = cfg["tts_provider"]
    if p == "deepgram":
        from livekit.plugins import deepgram
        return deepgram.TTS(model=cfg["tts_model"])
    from livekit.plugins import openai
    return openai.TTS(model=cfg["tts_model"], voice=cfg["tts_voice"] or "nova")


def build_pipeline(vad=None):
    """Return (vad, stt, tts, cfg). Raises ProviderConfigError with the exact missing key names."""
    cfg = selected()
    if cfg["missing_keys"]:
        raise ProviderConfigError("missing API key(s) for the selected speech providers: "
                                  + ", ".join(cfg["missing_keys"]) + " (see docs/FREE_API_KEYS.md)")
    return vad or load_vad(), build_stt(cfg), build_tts(cfg), cfg
