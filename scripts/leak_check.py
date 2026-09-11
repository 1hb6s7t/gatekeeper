"""Prove a model key cannot reach a client.

Two modes, and they prove different things.

**Local (default).** The app is started in-process with a canary key in the
environment and a deliberately hostile upstream: one that answers 401 and echoes
the Authorization header it received back inside its error body.  That is the
worst realistic case, because an SDK error can quote the failing request.  Every
route is then walked, including the failure paths, and asserted to contain the
canary nowhere in its body or headers.  The working tree is scanned afterwards so
a key cannot land in a project file or a cache entry either.

    python scripts/leak_check.py

**Live (`--url`).** Read-only probes against a running instance, asserting that
no response carries a credential-shaped string and that /api/meta exposes only
the channel name and model id.  It deliberately never calls extract or check, so
checking a deployed demo does not spend the author's budget.

    python scripts/leak_check.py --url https://your-demo.example
"""
from __future__ import annotations

import argparse
import contextlib
import http.server
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CANARY = "tp-canary0000000000000000000000leak"
CREDENTIAL_SHAPES = (
    re.compile(r"\b(?:sk|tp|rk|pk|ghp|glpat|xox[baprs])[-_][A-Za-z0-9._-]{12,}"),
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"(?i)authorization\b"),
)

failures: list[str] = []
notes: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        failures.append(f"{label}{(' — ' + detail) if detail else ''}")


# ---------------------------------------------------------------- hostile upstream
class EchoKeyHandler(http.server.BaseHTTPRequestHandler):
    """Answer 401 and quote back the credential it was given."""

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(length)
        quoted = self.headers.get("authorization") or CANARY
        body = json.dumps({
            "error": {
                "message": f"invalid api key: {quoted} (header authorization: {quoted})",
                "type": "invalid_request_error",
            }
        }).encode("utf-8")
        self.send_response(401)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # keep the test output readable
        return


def start_echo_server() -> tuple[http.server.ThreadingHTTPServer, str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), EchoKeyHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/v1"


# ---------------------------------------------------------------- shared assertions
def scan_text(text: str, label: str, *, canary: str | None = CANARY) -> None:
    if canary and canary in text:
        check(False, f"{label} 未泄漏密钥", "响应里出现了探针密钥")
        return
    for pattern in CREDENTIAL_SHAPES:
        match = pattern.search(text)
        if match:
            check(False, f"{label} 无凭据形状字符串", f"命中 {match.group(0)[:40]!r}")
            return
    check(True, f"{label} 无凭据泄漏")


def scan_response(resp, label: str, *, canary: str | None = CANARY) -> None:
    scan_text(resp.text, label, canary=canary)
    header_dump = "\n".join(f"{k}: {v}" for k, v in resp.headers.items())
    scan_text(header_dump, f"{label} [headers]", canary=canary)


def scan_tree(root: Path, *, canary: str = CANARY) -> None:
    offenders = []
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        try:
            if canary in path.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(str(path.relative_to(root)))
        except OSError:
            continue
    check(not offenders, "工作区文件未写入密钥", f"命中：{offenders}")


# ---------------------------------------------------------------- local mode
def local_run() -> None:
    scratch = Path(tempfile.mkdtemp(prefix="gatekeeper-leak-"))
    echo_server, base_url = start_echo_server()
    os.environ.update({
        "GATEKEEPER_PROVIDER": "openai",
        "GATEKEEPER_OPENAI_API_KEY": CANARY,
        "GATEKEEPER_OPENAI_BASE_URL": base_url,
        "GATEKEEPER_OPENAI_MODEL": "canary-model",
        "GATEKEEPER_CACHE_DIR": str(scratch / "cache"),
        "GATEKEEPER_LIMIT_STATE": str(scratch / "limits.json"),
        "GATEKEEPER_DISABLE_LIMITS": "",   # the real brakes are part of the surface
    })
    os.environ.pop("GATEKEEPER_CACHE_FALLBACK_DIR", None)  # force real provider calls

    from fastapi.testclient import TestClient

    from app import engine, server

    created: list[Path] = []
    try:
        # Was the hostile condition actually reached?  If the SDK stopped quoting
        # the failing request, the scrub below is never exercised and the test
        # says so instead of claiming a guarantee it did not test.
        raw_leaked = False
        try:
            engine._call_openai("probe", "probe")
        except Exception as e:  # noqa: BLE001 - the failure is the point
            raw_leaked = CANARY in str(e)
        if raw_leaked:
            check(True, "上游确实回显了密钥（探针有效）")
        else:
            notes.append("上游异常未回显密钥，scrub 本次未被真实触发（客户端侧断言仍然成立）")

        client = TestClient(server.app, raise_server_exceptions=False)

        resp = client.get("/api/meta")
        check(resp.status_code == 200, "GET /api/meta 200", str(resp.status_code))
        check(set(resp.json()) <= {"provider", "model"}, "GET /api/meta 只暴露通道与模型", str(resp.json()))
        check(CANARY not in resp.text, "GET /api/meta 不含密钥")
        scan_response(resp, "GET /api/meta")

        for path in ("/", "/api/sample", "/static/index.html", "/favicon.ico"):
            resp = client.get(path)
            check(resp.status_code in (200, 204), f"GET {path} 可达", str(resp.status_code))
            scan_response(resp, f"GET {path}")

        resp = client.get("/api/projects/deadbeef")
        check(resp.status_code == 404, "GET 不存在的项目 404", str(resp.status_code))
        scan_response(resp, "GET /api/projects/deadbeef")

        manuscript = "第一章 起\n\n林砚十七岁，惯用右手使刀。\n\n第二章 承\n\n他左手缠着布条。\n"
        resp = client.post("/api/projects", json={"title": "探针", "text": manuscript})
        check(resp.status_code == 200, "POST /api/projects 200", str(resp.status_code))
        scan_response(resp, "POST /api/projects")
        pid = resp.json()["id"]
        created.append(server.DATA / f"{pid}.json")

        # The failure path under test: a real provider call that errors out.
        # stderr is captured too: the server log wants the diagnosis, but a log
        # gets pasted into issues and must not carry the key either.
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            resp = client.post(f"/api/projects/{pid}/extract", json={"chapter_index": 0, "use_cache": False})
        check(resp.status_code == 424, "抽取失败返回 424（不是被 Cloudflare 吞掉的 5xx）", str(resp.status_code))
        detail = resp.json().get("detail", "") if resp.headers.get("content-type", "").startswith("application/json") else ""
        check("模型调用失败" in detail, "失败说明仍对评审可读", detail[:80])
        check(CANARY not in resp.text, "抽取失败响应不含密钥")
        scan_response(resp, "POST /extract（失败路径）")
        if raw_leaked:
            check("[已隐藏]" in detail, "被回显的密钥已被替换为 [已隐藏]", detail[:80])
            check(CANARY not in captured.getvalue(), "服务端日志同样不含密钥")
            check("provider call failed" in captured.getvalue(), "服务端日志保留了诊断信息")

        resp = client.post(f"/api/projects/{pid}/check", json={"title": "第六章", "text": "林砚用左手使刀。", "use_cache": False})
        check(resp.status_code == 400, "空设定库时检查被拦下", str(resp.status_code))
        scan_response(resp, "POST /check（无设定库）")

        for path in (f"/api/projects/{pid}/export.md", "/api/projects/nope/export.md"):
            resp = client.get(path)
            check(resp.status_code in (200, 404), f"GET {path}", str(resp.status_code))
            scan_response(resp, f"GET {path}")

        resp = client.post("/api/projects", json={"title": "过长", "text": "字" * (server.MAX_TEXT_CHARS + 1)})
        check(resp.status_code == 413, "超长稿件被 413 拒绝", str(resp.status_code))
        scan_response(resp, "POST /api/projects（超长）")

        resp = client.get("/report.mp4", headers={"range": "bytes=0-1023"})
        check(resp.status_code in (200, 206, 404), "GET /report.mp4", str(resp.status_code))
        scan_response(resp, "GET /report.mp4")

        resp = client.post("/api/projects/xyz/extract", json={"chapter_index": 0})
        check(resp.status_code == 404, "向不存在的项目抽取 404", str(resp.status_code))
        scan_response(resp, "POST /extract（项目不存在）")

        scan_tree(scratch)
        scan_tree(ROOT / "data")
        scan_tree(ROOT / "app")
    finally:
        echo_server.shutdown()
        for path in created:
            path.unlink(missing_ok=True)
        shutil.rmtree(scratch, ignore_errors=True)


# ---------------------------------------------------------------- live mode
def live_run(url: str) -> None:
    import urllib.error
    import urllib.request

    base = url.rstrip("/")
    # Read-only on purpose: probing a deployed instance must not spend its budget.
    probes = ["/", "/api/meta", "/api/sample", "/favicon.ico", "/static/index.html", "/api/projects/deadbeef"]
    for path in probes:
        request = urllib.request.Request(base + path, headers={"range": "bytes=0-1023"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8", errors="ignore")
                status = response.status
                headers = "\n".join(f"{k}: {v}" for k, v in response.getheaders())
        except urllib.error.HTTPError as e:
            body, status = e.read().decode("utf-8", errors="ignore"), e.code
            headers = "\n".join(f"{k}: {v}" for k, v in e.headers.items())
        check(status in (200, 204, 404), f"GET {path} 可达", str(status))
        # Nothing here should look like a credential, and the canary is unknown
        # for a live instance, so shape is all we can assert.
        scan_text(body, f"GET {path}", canary=None)
        scan_text(headers, f"GET {path} [headers]", canary=None)

    request = urllib.request.Request(base + "/api/meta")
    with urllib.request.urlopen(request, timeout=30) as response:
        meta = json.loads(response.read().decode("utf-8"))
    check(set(meta) <= {"provider", "model"}, "/api/meta 只暴露通道与模型", str(meta))
    notes.append(f"线上通道：{meta}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", help="check a running instance instead of starting one locally")
    args = parser.parse_args()

    print(f"探针密钥：{CANARY[:12]}…（假密钥，不会调用任何真实模型）")
    if args.url:
        print(f"模式：线上只读探测 {args.url}")
        live_run(args.url)
    else:
        print("模式：本地全路径 + 恶意上游回显")
        local_run()

    print()
    for note in notes:
        print(f"注：{note}")
    if failures:
        print(f"\nFAIL · {len(failures)} 项未通过")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\nPASS · 密钥没有到达客户端的路径")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
