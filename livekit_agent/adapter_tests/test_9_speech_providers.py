"""Speech-provider selection (free-tier switch) — livekit_agent/speech_providers.py.

Checks the env-driven choice, the exact missing-key message, and (when the livekit plugins are
installed) that every supported provider actually constructs with a dummy key. No network calls:
the plugins only open connections when audio flows.

Run: python3 livekit_agent/adapter_tests/test_9_speech_providers.py
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from livekit_agent import speech_providers as sp  # noqa: E402

KEYS = ("OPENAI_API_KEY", "GROQ_API_KEY", "DEEPGRAM_API_KEY",
        "TRIAGELINE_STT_PROVIDER", "TRIAGELINE_TTS_PROVIDER", "TRIAGELINE_STT_MODEL",
        "TRIAGELINE_TTS_MODEL", "TRIAGELINE_TTS_VOICE")


def with_env(**env):
    saved = {k: os.environ.pop(k, None) for k in KEYS}
    os.environ.update(env)
    return saved


def restore(saved):
    for k in KEYS:
        os.environ.pop(k, None)
        if saved[k] is not None:
            os.environ[k] = saved[k]


def test_defaults_are_openai():
    s = with_env()
    try:
        c = sp.selected()
        assert (c["stt_provider"], c["stt_model"], c["tts_provider"], c["tts_model"]) == \
            ("openai", "whisper-1", "openai", "tts-1"), c
        assert c["missing_keys"] == ["OPENAI_API_KEY"], c
        assert "no LLM" in sp.describe(c)
    finally:
        restore(s)


def test_free_combo_groq_plus_deepgram():
    s = with_env(TRIAGELINE_STT_PROVIDER="groq", TRIAGELINE_TTS_PROVIDER="deepgram")
    try:
        c = sp.selected()
        assert c["stt_model"] == "whisper-large-v3-turbo" and c["tts_model"].startswith("aura-2"), c
        assert c["missing_keys"] == ["DEEPGRAM_API_KEY", "GROQ_API_KEY"], c
        try:
            sp.build_pipeline(vad=object())
            raise AssertionError("expected ProviderConfigError for missing keys")
        except sp.ProviderConfigError as e:
            assert "GROQ_API_KEY" in str(e) and "DEEPGRAM_API_KEY" in str(e)
    finally:
        restore(s)


def test_unknown_provider_rejected():
    for var in ("TRIAGELINE_STT_PROVIDER", "TRIAGELINE_TTS_PROVIDER"):
        s = with_env(**{var: "nope"})
        try:
            sp.selected()
            raise AssertionError(f"{var}=nope accepted")
        except sp.ProviderConfigError:
            pass
        finally:
            restore(s)
    s = with_env(TRIAGELINE_TTS_PROVIDER="groq")   # Groq TTS is deliberately unsupported
    try:
        sp.selected()
        raise AssertionError("groq TTS accepted")
    except sp.ProviderConfigError:
        pass
    finally:
        restore(s)


def test_plugins_construct_with_dummy_keys():
    try:
        import livekit.plugins.openai  # noqa: F401
    except ImportError:
        print("  skip plugin construction (livekit plugins not installed)")
        return
    combos = [("openai", "openai"), ("groq", "openai")]
    try:
        import livekit.plugins.deepgram  # noqa: F401
        combos += [("deepgram", "deepgram"), ("groq", "deepgram")]
    except ImportError:
        print("  skip deepgram (plugin not installed)")
    for stt, tts in combos:
        s = with_env(TRIAGELINE_STT_PROVIDER=stt, TRIAGELINE_TTS_PROVIDER=tts,
                     OPENAI_API_KEY="x", GROQ_API_KEY="x", DEEPGRAM_API_KEY="x")
        try:
            _vad, st, tt, cfg = sp.build_pipeline(vad=object())
            assert st is not None and tt is not None, (stt, tts)
            if stt == "groq":
                assert "groq.com" in str(getattr(st, "_client").base_url), "groq STT must hit api.groq.com"
        finally:
            restore(s)


if __name__ == "__main__":
    for t in (test_defaults_are_openai, test_free_combo_groq_plus_deepgram,
              test_unknown_provider_rejected, test_plugins_construct_with_dummy_keys):
        t()
        print(f"  ok  {t.__name__}")
    print("PASS: test_9_speech_providers")
