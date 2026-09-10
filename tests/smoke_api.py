"""FastAPI smoke test for Gatekeeper's bundled end-to-end path.

The test deliberately uses the bundled cache. It must not require network
access, an API key, or a running uvicorn process.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("GATEKEEPER_PROVIDER", "cache")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app import engine
from app.server import DATA, app


def assert_ok(response, message: str = ""):
    assert response.status_code < 400, (
        f"{message} HTTP {response.status_code}: {response.text[:500]}"
    )
    return response.json()


def main() -> None:
    client = TestClient(app)
    sample = assert_ok(client.get("/api/sample"))
    project = assert_ok(
        client.post(
            "/api/projects",
            json={"title": sample["title"], "text": sample["manuscript"]},
        ),
        "create project",
    )
    pid = project["id"]
    project_path = DATA / f"{pid}.json"

    try:
        assert len(engine.split_chapters("Chapter 1 Opening\nalpha\nChapter 2 Return\nbeta")) == 2
        assert len(engine.split_chapters("1. First\nalpha\n2、Second\nbeta")) == 2
        assert len(engine.split_chapters("卷一\nalpha\n===\nbeta")) == 2
        assert len(project["chapters"]) == 5
        assert not project.get("warnings")

        for i in range(5):
            result = assert_ok(
                client.post(
                    f"/api/projects/{pid}/extract",
                    json={"chapter_index": i, "use_cache": True},
                ),
                f"extract chapter {i + 1}",
            )
            assert result["source"] == "cache"
            project = result["project"]

        assert len(project["bible"]) == 15
        assert sum(len(entry["facts"]) for entry in project["bible"]) == 60
        assert sum(
            bool(f.get("change"))
            for entry in project["bible"]
            for f in entry["facts"]
        ) >= 2

        checked = assert_ok(
            client.post(
                f"/api/projects/{pid}/check",
                json={
                    "title": sample["new_chapter_title"],
                    # Include the heading to cover server-side heading removal.
                    "text": sample["new_chapter"],
                    "use_cache": True,
                },
            ),
            "check new chapter",
        )
        check = checked["check"]
        assert check["source"] == "cache"
        assert len(check["findings"]) == 9
        assert all(f["conflict_verified"] for f in check["findings"])

        resolved = assert_ok(
            client.post(
                f"/api/projects/{pid}/checks/{check['id']}/findings/{check['findings'][0]['id']}",
                json={"status": "intentional", "note": "这是伏笔"},
            ),
            "mark finding intentional",
        )
        assert len(resolved.get("rules", [])) == 1
        assert resolved["rules"][0]["note"] == "这是伏笔"

        repeated = assert_ok(
            client.post(
                f"/api/projects/{pid}/check",
                json={
                    "title": sample["new_chapter_title"],
                    "text": sample["new_chapter"],
                    "use_cache": True,
                },
            ),
            "re-check after adding intentional rule",
        )
        assert repeated["check"]["source"] == "cache"
        repeated_findings = repeated["check"]["findings"]
        assert sum(f["status"] == "intentional" and bool(f.get("rule_id")) for f in repeated_findings) == 1
        assert sum(f["status"] == "open" for f in repeated_findings) == 8

        manual = assert_ok(
            client.post(
                f"/api/projects/{pid}/bible/facts",
                json={
                    "type": "item",
                    "name": "手工事实测试",
                    "fact": "手工事实初稿",
                    "chapter": project["chapters"][0]["title"],
                    "quote": "青玉佩贴着皮肉，温的",
                },
            ),
            "add manual fact",
        )
        manual_entry = next(e for e in manual["bible"] if e["name"] == "手工事实测试")
        manual_fact = manual_entry["facts"][0]
        assert manual_fact["quote_verified"] is True
        edited = assert_ok(
            client.patch(
                f"/api/projects/{pid}/bible/{manual_entry['id']}/facts/{manual_fact['id']}",
                json={"fact": "手工事实修订"},
            ),
            "edit manual fact",
        )
        edited_fact = next(
            f for e in edited["bible"] if e["id"] == manual_entry["id"] for f in e["facts"] if f["id"] == manual_fact["id"]
        )
        assert edited_fact["fact"] == "手工事实修订"
        assert edited_fact["quote_verified"] is True

        committed = assert_ok(
            client.post(
                f"/api/projects/{pid}/checks/{check['id']}/commit",
            ),
            "commit chapter",
        )
        assert len(committed["chapters"]) == 6

        exported = client.get(f"/api/projects/{pid}/export.md")
        assert exported.status_code == 200, exported.text[:500]
        assert "林砚" in exported.text
        assert "第六章 云州夜雨" in exported.text
        assert "手工事实修订" in exported.text
        print("smoke_api: PASS")
    finally:
        if project_path.exists():
            project_path.unlink()


if __name__ == "__main__":
    main()
