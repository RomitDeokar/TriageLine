#!/usr/bin/env python3
"""Record the PWA interruption demo (real app, simulated tools) as a clip for the submission video.

    python -m ui --offline &                       # or any running gateway
    pip install playwright && python -m playwright install chromium
    python3 scripts/record_ui_demo.py [--url http://localhost:8080] [--out docs/video/pwa_interrupt.mp4]

Shows: request -> quick acknowledgement -> background tools -> mid-task correction (barge-in) ->
cancelled stale work -> corrected answer -> same-breath retraction that performs nothing.
PWA segment only; the FDB-v3 live-run segment must be screen-recorded from ./run_fdb_v3.sh.
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


async def type_slow(page, text):
    await page.click("#text")
    await page.keyboard.type(text, delay=38)
    await page.keyboard.press("Enter")


async def main(url: str, out: Path):
    tmp = ROOT / ".cache" / "demo_video"
    shutil.rmtree(tmp, ignore_errors=True)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 390, "height": 844},
                                  record_video_dir=str(tmp), record_video_size={"width": 390, "height": 844})
        page = await ctx.new_page()
        await page.goto(url + "/live.html")
        await page.wait_for_timeout(2500)
        await type_slow(page, "Find a flight to Denver and book the 8 AM one for Alice.")
        await page.wait_for_timeout(700)
        await type_slow(page, "wait, make it Seattle instead")
        await page.wait_for_timeout(4500)
        await type_slow(page, "Turn on autopay for my gas bill from checking — actually, don't do that.")
        await page.wait_for_timeout(3500)
        await page.click("#menu")
        await page.wait_for_timeout(1800)
        await page.click("#sheet-close")
        await page.wait_for_timeout(800)
        await ctx.close()
        await b.close()
    webm = next(tmp.glob("*.webm"))
    out.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("ffmpeg") and out.suffix == ".mp4":
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm), "-vf", "scale=780:-2:flags=lanczos",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "26", "-movflags", "+faststart", str(out)],
                       check=True)
    else:
        out = out.with_suffix(".webm")
        shutil.copy(webm, out)
    shutil.rmtree(tmp, ignore_errors=True)
    print("wrote", out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--out", default=str(ROOT / "docs/video/pwa_interrupt.mp4"))
    a = ap.parse_args()
    asyncio.run(main(a.url.rstrip("/"), Path(a.out)))
