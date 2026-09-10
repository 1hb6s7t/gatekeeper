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
                json={"status": "intentional"},
            ),
            "mark finding intentional",
        )
        assert len(resolved.get("intentional", [])) in (0, 1)

        committed = assert_ok(
            client.post(
                f"/api/projects/{pid}/checks/{check['id']}/commit",
            ),
            "commit chapter",
        )
        assert len(committed["chapters"]) == 6
        print("smoke_api: PASS")
    finally:
        if project_path.exists():
            project_path.unlink()


if __name__ == "__main__":
    main()
