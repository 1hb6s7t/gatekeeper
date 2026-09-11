"""Record the <=5 min report screencast: deck scenes paced to the TTS narration
plus a live walkthrough of the cached demo app.

Prereqs: docs/narration/*.wav + durations.json (scripts/tts_narration.ps1),
a cache-mode server on :8766. Output: docs/video_raw/*.webm
"""
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
DECK = (ROOT / "docs" / "report_deck.html").as_uri()
APP = "http://127.0.0.1:8766"
RAW = ROOT / "docs" / "video_raw"
CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
PAD = 0.7  # breathing room after each narration segment


def durations() -> dict[int, float]:
    raw = json.loads((ROOT / "docs" / "narration" / "durations.json").read_text(encoding="utf-8"))
    return {int(k): float(v) for k, v in raw.items()}


async def main() -> int:
    durs = durations()
    if RAW.exists():
        shutil.rmtree(RAW)
    RAW.mkdir(parents=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME, headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            record_video_dir=str(RAW),
            record_video_size={"width": 1280, "height": 720},
        )
        page = await context.new_page()

        async def paced(scene_id: int, work) -> None:
            """Run a scene's actions, then hold until its narration has finished."""
            started = time.monotonic()
            await work()
            remaining = durs[scene_id] + PAD - (time.monotonic() - started)
            if remaining > 0:
                await page.wait_for_timeout(int(remaining * 1000))
            print(f"  scene {scene_id}: {time.monotonic() - started:.1f}s (narration {durs[scene_id]:.1f}s)")

        await page.goto(DECK, wait_until="networkidle")
        for sid in (1, 2, 3, 4):
            async def show(sid=sid):
                await page.evaluate(f"showScene({sid})")
            await paced(sid, show)

        # scene 5: live walkthrough of the bundled sample
        async def demo():
            await page.goto(APP, wait_until="networkidle")
            await page.click("#btnSample")
            await page.click("#btnCreate")
            await page.wait_for_selector("#chapters .chap", timeout=15000)
            await page.wait_for_timeout(4000)
            await page.click("#btnExtractAll")
            await page.wait_for_function(
                "document.querySelector('#btnExtractAll').textContent === '建立设定库'", timeout=120000
            )
            await page.wait_for_timeout(3000)
            await page.click('.tabs button[data-tab="timeline"]')
            await page.wait_for_selector("#timeline .chain", timeout=15000)
            await page.wait_for_timeout(7000)
            await page.click('.tabs button[data-tab="newch"]')
            await page.click("#btnCheck")
            await page.wait_for_selector("#findings .finding", timeout=120000)
            await page.wait_for_function(
                "!document.querySelector('#btnCommit').disabled", timeout=120000
            )
            await page.wait_for_timeout(4000)
            await page.click("#btnEval")
            await page.wait_for_selector("#findings .summary", timeout=15000)

        await paced(5, demo)

        # scenes 6-8 back on the deck
        await page.goto(DECK, wait_until="networkidle")
        for sid in (6, 7, 8):
            async def show(sid=sid):
                await page.evaluate(f"showScene({sid})")
            await paced(sid, show)

        await context.close()
        await browser.close()

    videos = sorted(RAW.glob("*.webm"), key=lambda p: p.stat().st_mtime)
    if not videos:
        print("no video produced", file=sys.stderr)
        return 1
    print(f"\nrecorded: {videos[-1]} ({videos[-1].stat().st_size // 1024} KB)")
    print(f"total narration {sum(durs.values()):.1f}s + pads {PAD * len(durs):.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
