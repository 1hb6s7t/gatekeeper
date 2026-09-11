"""Brakes and redaction for an instance that runs with a real model key.

The online demo runs on the author's own machine behind a tunnel, with a real
key in its environment.  Two things therefore need guarding, and they are
different problems:

  * **Spending.**  The URL is public and unauthenticated, so anyone who has it
    can ask the engine to extract a manuscript and burn the key's quota.  Cache
    hits never reach a provider, so replaying the bundled sample stays free;
    everything else is metered by `reserve()`.
  * **Saying too much.**  An SDK exception can embed the request it failed on,
    headers included, and the headers carry the key.  Anything an exception
    contributes to a response body goes through `scrub()` first, so a 401 from
    upstream cannot become a key disclosure.

Nothing here holds, logs or transmits the key itself; it is only read by the
provider call in `engine`.
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parent.parent

STATE_PATH = Path(os.environ.get("GATEKEEPER_LIMIT_STATE", str(ROOT / "data" / "limits.json")))
if not STATE_PATH.is_absolute():
    STATE_PATH = ROOT / STATE_PATH

# Local development with your own key can switch the budget off entirely.
DISABLED = os.environ.get("GATEKEEPER_DISABLE_LIMITS", "").strip().lower() not in ("", "0", "false", "no")

WINDOW_SECONDS = int(os.environ.get("GATEKEEPER_IP_WINDOW_SECONDS", "3600"))
IP_LIMIT = int(os.environ.get("GATEKEEPER_IP_LIMIT", "20"))
DAILY_LIMIT = int(os.environ.get("GATEKEEPER_DAILY_LIMIT", "150"))
MAX_CONCURRENCY = int(os.environ.get("GATEKEEPER_MAX_CONCURRENCY", "1"))

_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)\b(?:authorization|proxy-authorization|api[-_]?key|api[-_]?token|x-api-key)\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"\b(?:sk|tp|rk|pk|ghp|gho|glpat|xox[baprs])[-_][A-Za-z0-9._\-]{8,}"),
)

_lock = threading.Lock()
_windows: dict[str, deque[float]] = defaultdict(deque)
_slots = threading.Semaphore(MAX_CONCURRENCY)
_in_flight = 0

# The visitor identity for the request being served.  Set by `slot`, read by
# `reserve` a few frames deeper inside the engine.
_client: contextvars.ContextVar[str] = contextvars.ContextVar("gatekeeper_client", default="unknown")


class Exceeded(RuntimeError):
    """A request that would spend model budget was refused."""


# ---------------------------------------------------------------- redaction
def scrub(message: object, *, limit: int = 300, first_line_only: bool = True) -> str:
    """Return a client-safe rendering of an upstream error.

    By default only the first line survives: SDK errors tack the failing request
    onto later lines, and that is where headers (and therefore the key) show up.
    Whatever is left is stripped of anything shaped like a credential, then
    truncated.  The server log keeps the later lines (`first_line_only=False`)
    because it wants the diagnosis, but it never wants the credential: log files
    get pasted into issues and attached to bug reports.
    """
    text = str(message or "").strip()
    if not text:
        return "（无说明）"
    if first_line_only:
        text = text.splitlines()[0].strip()
    for pattern in _CREDENTIAL_PATTERNS:
        text = pattern.sub("[已隐藏]", text)
    if len(text) > limit:
        text = text[:limit] + "…"
    return text or "（无说明）"


# ---------------------------------------------------------------- budget state
def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _read_state() -> dict:
    """Today's counter, reset on the first use of a new UTC day."""
    today = _today()
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(state, dict) and state.get("day") == today:
            return {"day": today, "calls": int(state.get("calls") or 0)}
    except (OSError, ValueError, TypeError):
        pass
    return {"day": today, "calls": 0}


def _write_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass  # an unwritable state dir must not break the request


def usage() -> dict:
    """Current counters, for the server log."""
    with _lock:
        state = _read_state()
        return {**state, "daily_limit": DAILY_LIMIT, "in_flight": _in_flight}


# ---------------------------------------------------------------- the brakes
@contextmanager
def slot(client: str) -> Iterator[None]:
    """Hold the instance's model-call slot for one request, and tag the visitor.

    One at a time on purpose: this gateway answers a chapter extraction in 78 to
    306 seconds, so queueing is cheaper than letting parallel requests multiply
    what a single visitor can spend before the counters notice.
    """
    global _in_flight
    if not DISABLED and not _slots.acquire(blocking=False):
        raise Exceeded(
            f"演示实例同时只处理 {MAX_CONCURRENCY} 次真实模型调用（模型额度由作者自付），"
            "请等当前的请求结束后重试。缓存覆盖的示例流程不受影响。"
        )
    token = _client.set(client)
    with _lock:
        _in_flight += 1
    try:
        yield
    finally:
        with _lock:
            _in_flight -= 1
        _client.reset(token)
        if not DISABLED:
            _slots.release()


def reserve() -> None:
    """Charge one real model call to the visitor, or refuse the request.

    The engine calls this immediately before each provider request, so a cache
    hit (the bundled sample, or a manuscript this instance already answered) is
    never charged.  Raises `Exceeded`, which the server maps to a 429.
    """
    if DISABLED:
        return
    client = _client.get()
    now = time.monotonic()
    with _lock:
        window = _windows[client]
        while window and now - window[0] > WINDOW_SECONDS:
            window.popleft()
        if len(window) >= IP_LIMIT:
            raise Exceeded(
                f"同一个访客每 {max(1, WINDOW_SECONDS // 60)} 分钟最多 {IP_LIMIT} 次真实模型调用，"
                "请稍后再试。缓存覆盖的示例流程不受影响。"
            )
        state = _read_state()
        if state["calls"] >= DAILY_LIMIT:
            raise Exceeded(
                f"演示实例今天的模型调用额度（{DAILY_LIMIT} 次）已用完，明天恢复。"
                "仓库、录屏和本地运行都不受影响。"
            )
        window.append(now)
        state["calls"] += 1
        _write_state(state)
