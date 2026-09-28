#!/usr/bin/env python3
"""Make one tiny real request to every configured provider and print the outcome.

Usage:  python scripts/check_providers.py            # LLM chain + speech config
        python scripts/check_providers.py --tts      # also synthesise one short phrase (Gemini/Deepgram/OpenAI)

Never prints keys, request bodies or response bodies — only provider, model, HTTP status and latency.
Exit status is 0 when at least one LLM provider answered, 1 otherwise.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    from dotenv import load_dotenv
    for f in (".env", "livekit_agent/.env.local"):
        if os.path.exists(os.path.join(ROOT, f)):
            load_dotenv(os.path.join(ROOT, f), override=False)
except ImportError:
    pass

from agent import providers  # noqa: E402

TOOL = {"track_order": {"kind": "read_only", "description": "Track physical package status.",
                        "args": {"order_id": {"type": "string", "required": True}}}}


def check_llm(name: str, first: bool) -> bool:
    model = providers.model_for(name, first=first)
    if not providers.configured(name):
        print(f"  {name:<11} {model:<34} SKIP  (no key: {'/'.join(providers.PROVIDERS[name]['key_env'])})")
        return False
    t0 = time.monotonic()
    try:
        res = providers._call_one(name, model, system="Call the tool for the user's request.",
                                  user="Track order ABC123", tools=TOOL, timeout=20, temperature=0, max_tokens=256)
        ms = (time.monotonic() - t0) * 1000
        ok = bool(res.calls) and res.calls[0].get("name") == "track_order"
        print(f"  {name:<11} {model:<34} {'OK  ' if ok else 'WARN'}  {ms:6.0f} ms  "
              f"{'tool call ' + str(res.calls[0]) if res.calls else 'no tool call (text answer)'}")
        return True
    except providers.ProviderError as exc:
        ms = (time.monotonic() - t0) * 1000
        hint = {401: "bad key", 403: "key lacks access / region", 404: "model name not available to this key",
                429: "quota / rate limit", 400: "request rejected (model may not support tools)"}.get(exc.status, "")
        print(f"  {name:<11} {model:<34} FAIL  {ms:6.0f} ms  {exc.reason} {exc.status or ''} {hint}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tts", action="store_true", help="also test TTS synthesis")
    args = ap.parse_args()
    try:
        chain = providers.chain()
    except ValueError as exc:
        print("Configuration error:", exc)
        return 1
    print("LLM chain:", " -> ".join(chain))
    ok = [check_llm(n, i == 0) for i, n in enumerate(chain)]
    try:
        sys.path.insert(0, os.path.join(ROOT, "livekit_agent"))
        from livekit_agent import speech_providers as sp
        cfg = sp.selected()
        print(f"Speech: STT {cfg['stt_provider']}:{cfg['stt_model']}  TTS {cfg['tts_provider']}:{cfg['tts_model']}"
              + (f"  MISSING {cfg['missing_keys']}" if cfg["missing_keys"] else ""))
        if args.tts and not cfg["missing_keys"]:
            import asyncio

            async def synth():
                engine = sp.build_tts(cfg)
                t0 = time.monotonic()
                first = None
                samples = 0
                async with engine.synthesize("Okay, checking that now.") as stream:
                    async for ev in stream:
                        first = first or time.monotonic()
                        samples += ev.frame.samples_per_channel
                await engine.aclose()
                print(f"  TTS OK: first audio {1000 * (first - t0):.0f} ms, {samples / 24000:.2f} s of audio")
            try:
                asyncio.run(synth())
            except Exception as exc:  # noqa: BLE001
                print(f"  TTS FAIL: {type(exc).__name__}: {str(exc)[:160]}")
    except Exception as exc:  # noqa: BLE001
        print("Speech config unavailable:", type(exc).__name__, str(exc)[:160])
    lk = [k for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET") if not os.environ.get(k)]
    print("LiveKit:", "configured" if not lk else "missing " + ", ".join(lk))
    return 0 if any(ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
