"""T7: capture docs/shot6_timeline.png and docs/shot7_rules.png via Playwright."""
import asyncio, sys
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://localhost:8765"


async def main() -> int:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            executable_path=r"C:/Program Files/Google/Chrome/Application/chrome.exe",
            headless=True,
        )
        page = await browser.new_page(viewport={"width": 1400, "height": 900})
        await page.goto(BASE, wait_until="networkidle")
        await page.click("#btnSample")
        await page.click("#btnCreate")
        await page.wait_for_selector("#chapters .chap", timeout=10000)
        await page.click("#btnExtractAll")
        await page.wait_for_function(
            "document.querySelector('#btnExtractAll')?.textContent === '建立设定库'",
            timeout=120000,
        )
        print("bible built (cached)")

        # shot6: timeline tab
        await page.click('.tabs button[data-tab="timeline"]')
        await page.wait_for_selector("#timeline .chain", timeout=10000)
        await page.wait_for_timeout(300)
        await page.screenshot(path=str(ROOT / "docs" / "shot6_timeline.png"))
        print("shot6 saved")

        # shot7: check, mark first finding intentional with a note, re-check -> rule skip
        await page.click('.tabs button[data-tab="newch"]')
        await page.click("#btnCheck")
        await page.wait_for_selector("#findings .finding", timeout=120000)
        await page.wait_for_function(
            "!document.querySelector('#btnCommit').disabled", timeout=120000
        )
        page.once("dialog", lambda d: d.accept("备注：预埋伏笔，第六卷回收"))
        await page.click('#findings button[data-s="intentional"]')
        await page.wait_for_selector("#bible .rule", state="attached", timeout=10000)
        print("rule created")
        await page.click("#btnCheck")
        await page.wait_for_selector("#findings .skipped", state="attached", timeout=120000)
        await page.wait_for_timeout(300)
        # expand the rules panel and the skipped list so both are visible
        await page.click('.tabs button[data-tab="bible"]')
        await page.wait_for_timeout(300)
        await page.click("#bible .rules summary")
        await page.wait_for_timeout(200)
        await page.click("#findings .skipped summary")
        await page.wait_for_timeout(200)
        await page.screenshot(path=str(ROOT / "docs" / "shot7_rules.png"))
        print("shot7 saved")
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))