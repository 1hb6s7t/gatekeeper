"""Verify the public demo end to end over the tunnel, then clean up after itself.

Run on the same machine as the demo server (it removes the project file directly,
since there is no delete-project route).

Usage: python scripts/verify_public_demo.py [base_url]
"""
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "data" / "projects"
BASE = sys.argv[1] if len(sys.argv) > 1 else "https://inner-homework-naturally-policies.trycloudflare.com"


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=120.0) as c:
        meta = c.get("/api/meta").json()
        print(f"meta: provider={meta.get('provider')} model={meta.get('model')}")

        sample = c.get("/api/sample").json()
        project = c.post("/api/projects", json={"title": "公网演示核验", "text": sample["manuscript"]}).json()
        pid = project["id"]
        print(f"project {pid}: {len(project['chapters'])} 章")
        try:
            for i in range(len(project["chapters"])):
                r = c.post(f"/api/projects/{pid}/extract", json={"chapter_index": i, "use_cache": True}).json()
                assert r["source"] == "cache", f"chapter {i} source={r['source']}"
            print(f"抽取 {len(project['chapters'])}/{len(project['chapters'])} 章，全部命中缓存")

            project = c.get(f"/api/projects/{pid}").json()
            facts = sum(len(e["facts"]) for e in project["bible"])
            verified = sum(1 for e in project["bible"] for f in e["facts"] if f.get("quote_verified"))
            changes = sum(1 for e in project["bible"] for f in e["facts"] if f.get("change"))
            print(f"设定库：{len(project['bible'])} 实体 / {facts} 事实 / 引用核验 {verified} / 变更标记 {changes}")

            r = c.post(
                f"/api/projects/{pid}/check",
                json={"title": sample["new_chapter_title"], "text": sample["new_chapter"], "use_cache": True},
            ).json()
            check = r["check"]
            all_verified = all(f["conflict_verified"] for f in check["findings"])
            print(f"检查：{len(check['findings'])} 条发现，source={check['source']}，冲突引用全部核验={all_verified}")

            # A reviewer editing the new chapter hits a cache miss: they must get the
            # plain-Chinese offline-demo explanation, not a raw provider error.
            miss = c.post(
                f"/api/projects/{pid}/check",
                json={"title": sample["new_chapter_title"], "text": sample["new_chapter"] + "\n\n（作者临时加的一段话。）", "use_cache": True},
            )
            try:
                detail = miss.json().get("detail", "")
            except ValueError:
                detail = miss.text
            print(f"缓存未命中：HTTP {miss.status_code} via {miss.headers.get('server')} type={miss.headers.get('content-type')}")
            print(f"  中文提示={'离线演示模式' in detail}")
            print(f"  -> {detail[:160]}")

            md = c.get(f"/api/projects/{pid}/export.md")
            print(f"导出：HTTP {md.status_code}，{len(md.text)} 字符，含「林砚」={'林砚' in md.text}")

            video = c.get("/report.mp4", headers={"Range": "bytes=0-1023"})
            print(f"录屏：HTTP {video.status_code} {video.headers.get('content-type')} accept-ranges={video.headers.get('accept-ranges')} bytes={video.headers.get('content-range')}")
        finally:
            path = PROJECTS / f"{pid}.json"
            if path.exists():
                path.unlink()
            print(f"已清理项目 {pid}（{path.name}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
