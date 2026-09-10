"""Evaluate Gatekeeper on the bundled sample: extract chapters 1-5, check chapter 6, score against answer_key.json.

Usage: python eval.py [--no-cache]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from app import engine

ROOT = Path(__file__).resolve().parent
use_cache = "--no-cache" not in sys.argv


def main() -> None:
    manuscript = (ROOT / "samples" / "manuscript.md").read_text(encoding="utf-8")
    new_chapter = (ROOT / "samples" / "new_chapter.md").read_text(encoding="utf-8")
    key = json.loads((ROOT / "samples" / "answer_key.json").read_text(encoding="utf-8"))

    chapters = engine.split_chapters(manuscript)
    bible: list[dict] = []
    t0 = time.time()
    for i, ch in enumerate(chapters):
        r = engine.extract_chapter(bible, i + 1, ch, allow_cache=use_cache)
        print(f"抽取 {ch['title']}: +{r['added']} 条事实 [{r['source']}] {time.time() - t0:.0f}s")
    facts = [f for e in bible for f in e["facts"]]
    verified = sum(1 for f in facts if f["quote_verified"])
    print(f"设定库：{len(bible)} 个实体，{len(facts)} 条事实，引用逐字命中 {verified}/{len(facts)}")

    new = engine.split_chapters(new_chapter)[0]
    t1 = time.time()
    r = engine.check_chapter(bible, len(chapters), new, [], allow_cache=use_cache)
    findings = r["findings"]
    print(f"检查 {new['title']}: {len(findings)} 条发现 [{r['source']}] {time.time() - t1:.0f}s")

    def hit(bug: dict, f: dict) -> bool:
        hay = f["conflict_quote"] + " " + f["explanation"]
        return any(k in hay for k in bug["keywords"])

    matched: dict[int, dict] = {}
    for f in findings:
        for bug in key["bugs"]:
            if bug["id"] not in matched and hit(bug, f):
                matched[bug["id"]] = f  # one finding may cover several planted bugs
    false_pos = [f for f in findings if not any(f is m for m in matched.values())]
    trap_hits = [t for t in key["traps"] if any(k in f["conflict_quote"] for f in false_pos for k in t["keywords"])]

    print("\n== 预埋矛盾召回 ==")
    for bug in key["bugs"]:
        f = matched.get(bug["id"])
        mark = "✓" if f else "✗"
        extra = f"  ← [{f['type']}/{f['severity']}] {f['conflict_quote'][:40]}" if f else f"  （{bug['truth']}）"
        print(f"{mark} #{bug['id']:>2} {bug['type']:<9}{extra}")
    print(f"\n召回 {len(matched)}/{len(key['bugs'])}，误报 {len(false_pos)}，踩陷阱 {len(trap_hits)}")
    if false_pos:
        print("\n== 未匹配到答案卡的发现（人工复核）==")
        for f in false_pos:
            print(f"- [{f['type']}/{f['severity']}] {f['explanation']}  ｜冲突处「{f['conflict_quote'][:40]}」")
    cq_ok = sum(1 for f in findings if f["conflict_verified"])
    print(f"\n冲突引用逐字命中 {cq_ok}/{len(findings)}")


if __name__ == "__main__":
    main()
