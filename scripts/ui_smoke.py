"""Browser-level smoke for the interactions the API smoke script does not click.

Covers: extraction stop/resume, answer-key panel, intentional rule create ->
re-check skip -> rule delete, manual fact add + inline edit, Markdown export.

Run against a live server on :8765 (sample prompts hit the bundled cache, so no
model calls are made): python scripts/ui_smoke.py
"""
import asyncio
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://localhost:8765"
EXTRACT_ROUTE = "**/api/projects/*/extract"
CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"


async def main() -> int:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME, headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900})
        await page.add_init_script(
            "window.__opened = []; const orig = window.open;"
            " window.open = (u, ...a) => { window.__opened.push(u); return orig.call(window, u, ...a); };"
        )
        console_errors: list[str] = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

        try:
            await page.goto(BASE, wait_until="networkidle")
            await page.click("#btnSample")
            await page.click("#btnCreate")
            await page.wait_for_selector("#chapters .chap", timeout=10000)
            print("ok  载入示例 + 切分章节")

            # ---- stop/resume: slow the extract route so the stop click lands mid-flight
            async def slow_extract(route):
                await asyncio.sleep(1.0)
                await route.continue_()

            await page.route(EXTRACT_ROUTE, slow_extract)
            await page.click("#btnExtractAll")
            await page.wait_for_timeout(250)
            await page.click("#btnExtractAll")  # stop; loop breaks before the next chapter
            await page.wait_for_function(
                "document.querySelector('#midStatus').textContent.includes('已停止')", timeout=30000
            )
            stopped = (await page.text_content("#midStatus")).strip()
            m = re.search(r"已停止，已抽取 (\d) 章", stopped)
            assert m and 1 <= int(m.group(1)) <= 4, stopped
            print(f"ok  停止：{stopped}")

            await page.unroute(EXTRACT_ROUTE)
            await page.click("#btnExtractAll")  # resume from the break point
            await page.wait_for_function(
                "document.querySelector('#midStatus').textContent.includes('设定库已建立')", timeout=120000
            )
            extracted = await page.eval_on_selector_all("#chapters .pill.good", "els=>els.length")
            assert extracted == 5, f"extracted {extracted}"
            timing = await page.text_content("#chapters .chap .n")
            assert "s · 缓存" in timing, timing
            print(f"ok  继续：{extracted} 章已抽取，秒表显示「{timing.strip()}」")

            # ---- check + answer-key panel
            await page.click('.tabs button[data-tab="newch"]')
            await page.click("#btnCheck")
            await page.wait_for_selector("#findings .finding", timeout=60000)
            findings = await page.eval_on_selector_all("#findings .finding", "els=>els.length")
            assert findings == 9, f"findings {findings}"
            print(f"ok  检查矛盾：{findings} 条发现")

            await page.click("#btnEval")
            await page.wait_for_selector("#findings .summary", timeout=5000)
            print("ok  对照答案卡面板渲染")

            # ---- intentional rule -> re-check skip -> delete rule
            page.once("dialog", lambda d: d.accept("备注：预埋伏笔，第六卷回收"))
            await page.click('#findings button[data-s="intentional"]')
            await page.wait_for_selector("#bible .rule", state="attached", timeout=10000)
            print("ok  有意为之：规则已创建")

            await page.click("#btnCheck")
            await page.wait_for_selector("#findings .skipped", state="attached", timeout=60000)
            print("ok  重检：该条已折叠进「已按规则跳过」")

            await page.click(".tabs button[data-tab='bible']")
            await page.click("#bible .rules summary")
            await page.click("#bible .rule-del")
            await page.wait_for_function("document.querySelectorAll('#bible .rule').length === 0", timeout=10000)
            skipped = await page.eval_on_selector_all("#findings .skipped", "els=>els.length")
            assert skipped == 0, "skipped section still visible after rule delete"
            print("ok  删除规则：规则移除，发现恢复显示")

            # ---- manual fact add + inline edit
            await page.click(".manual summary")
            await page.select_option("#manualType", "character")
            await page.fill("#manualName", "测试角色")
            await page.fill("#manualFact", "测试事实：由右手使刀变为左手使刀")
            await page.select_option("#manualChapter", index=0)
            await page.fill("#manualQuote", "林砚")
            await page.click(".manual-grid button[type=submit]")
            await page.wait_for_function(
                "document.querySelector('#midStatus').textContent.includes('手工事实已加入设定库')", timeout=10000
            )
            row = page.locator("#bible .fact", has_text="测试事实：由右手使刀变为左手使刀")
            assert await row.count() == 1, "manual fact not rendered"
            assert await row.locator(".pill.warn").count() == 0, "manual quote not verified"
            print("ok  手工添加事实：已入设定库，引用逐字命中")

            await row.locator(".fact-text").dblclick()
            editor = page.locator("#bible input[type=text]")
            await editor.fill("测试事实（已编辑）")
            await editor.press("Enter")
            await page.wait_for_function(
                "document.querySelector('#midStatus').textContent.includes('事实已更新')", timeout=10000
            )
            assert await page.locator("#bible .fact", has_text="测试事实（已编辑）").count() == 1
            print("ok  行内编辑：回车保存生效")

            # ---- export .md (window.open + Content-Disposition attachment:
            # the download belongs to the popup, so record the opened URL instead)
            await page.click("#btnExport")
            await page.wait_for_timeout(1000)
            opened = await page.evaluate("window.__opened || []")
            assert opened and "/export.md" in opened[-1], f"export button opened {opened}"
            export = await page.evaluate(
                """async url => {
                    const r = await fetch(url);
                    return {cd: r.headers.get('content-disposition') || '', text: await r.text()};
                }""",
                opened[-1],
            )
            assert "attachment" in export["cd"], export["cd"]
            markdown = export["text"]
            assert "林砚" in markdown and "第六章" in markdown, "export content incomplete"
            pid = opened[-1].split("/api/projects/")[1].split("/")[0]
            print(f"ok  导出 .md：{len(markdown)} 字符，附件下载头已确认，含设定库与第六章")

            # ---- cleanup: remove the project this run created
            (ROOT / "data" / "projects" / f"{pid}.json").unlink(missing_ok=True)

            assert not console_errors, f"console errors: {console_errors}"
            print("ok  无 console 错误；临时项目已清理")
            print("\nui_smoke: PASS")
            return 0
        finally:
            await browser.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
