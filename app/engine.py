"""Gatekeeper engine: chapter splitting, bible extraction, consistency check.

Real model providers (switch with GATEKEEPER_PROVIDER):
  - "openai": an OpenAI-compatible Chat Completions endpoint (non-streaming)
  - "anthropic": official Anthropic Python SDK
  - "claude-cli": shells out to `claude -p` (Claude Code non-interactive mode)
Plus "cache": replay pre-recorded responses for the bundled sample (no network).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get("GATEKEEPER_CACHE_DIR", str(ROOT / "data" / "cache")))
if not CACHE_DIR.is_absolute():
    CACHE_DIR = ROOT / CACHE_DIR
CACHE_DIR.mkdir(parents=True, exist_ok=True)

MODEL = os.environ.get("GATEKEEPER_MODEL", "claude-opus-5")
OPENAI_BASE_URL = os.environ.get("GATEKEEPER_OPENAI_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
OPENAI_MODEL = os.environ.get("GATEKEEPER_OPENAI_MODEL", "mimo-v2.5-pro")
PROVIDER = os.environ.get("GATEKEEPER_PROVIDER", "auto")  # auto | openai | anthropic | claude-cli | cache

ENTRY_TYPES = ["character", "location", "item", "timeline"]
TYPE_LABEL = {"character": "角色", "location": "地点", "item": "物品", "timeline": "时间线"}
FINDING_TYPES = ["fact", "time", "character", "location", "item"]


# ---------------------------------------------------------------- chapters
CHAPTER_RE = re.compile(r"^\s*(第\s*[0-9一二三四五六七八九十百零两]+\s*[章回节]|Chapter\s+\d+)[^\n]*$", re.M)


def split_chapters(text: str) -> list[dict[str, str]]:
    """Split pasted manuscript into chapters by '第N章' headings. Falls back to one chapter."""
    text = text.replace("\r\n", "\n").strip()
    heads = list(CHAPTER_RE.finditer(text))
    if not heads:
        return [{"title": "第1章", "text": text}]
    chapters = []
    for i, m in enumerate(heads):
        start = m.end()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[start:end].strip()
        chapters.append({"title": m.group(0).strip(), "text": body})
    return chapters


# ---------------------------------------------------------------- providers
def _cache_key(kind: str, prompt: str) -> Path:
    h = hashlib.sha256((kind + "\n" + prompt).encode("utf-8")).hexdigest()[:24]
    return CACHE_DIR / f"{kind}-{h}.json"


def _call_anthropic(prompt: str, system: str) -> str:
    import anthropic  # official SDK

    client = anthropic.Anthropic(default_headers={"anthropic-beta": "context-1m-2025-08-07"})
    with client.messages.stream(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError("model refused the request")
    return "".join(b.text for b in msg.content if b.type == "text")


def _call_claude_cli(prompt: str, system: str) -> str:
    cmd = ["claude", "-p", "--output-format", "text", "--append-system-prompt", system,
           "--effort", os.environ.get("GATEKEEPER_CLI_EFFORT", "medium")]
    if os.environ.get("GATEKEEPER_CLI_MODEL"):
        cmd += ["--model", os.environ["GATEKEEPER_CLI_MODEL"]]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=600, shell=os.name == "nt")
    if r.returncode != 0:
        raise RuntimeError(f"claude -p failed ({r.returncode}): {r.stderr[:400]}")
    return r.stdout


def _call_openai(prompt: str, system: str) -> str:
    """Call an OpenAI-compatible endpoint without asking it for an event stream.

    Some compatibility gateways always return SSE when the request is streamed,
    and then answer the SDK's fallback request with SSE as well.  Gatekeeper
    deliberately makes a regular Chat Completions request here so the SDK can
    decode one JSON response deterministically.
    """
    from openai import OpenAI

    api_key = os.environ.get("GATEKEEPER_OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("未设置 GATEKEEPER_OPENAI_API_KEY")
    client = OpenAI(base_url=OPENAI_BASE_URL, api_key=api_key, timeout=600)
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        max_tokens=8000,
        temperature=0,
        stream=False,
    )
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise RuntimeError("OpenAI 兼容 API 返回空 choices")
    choice = choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        raise RuntimeError("输出被截断")
    content = getattr(getattr(choice, "message", None), "content", None)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("OpenAI 兼容 API 返回空文本")
    return content


def _provider_order() -> list[str]:
    if PROVIDER == "auto":
        order = []
        if os.environ.get("GATEKEEPER_OPENAI_API_KEY"):
            order.append("openai")
        order.extend(["anthropic", "claude-cli"])
        return order
    return [PROVIDER]


def call_model(
    kind: str,
    prompt: str,
    system: str,
    *,
    allow_cache: bool = True,
    retries: int = 0,
) -> tuple[str, str]:
    """Return (raw_text, source), retrying provider failures when requested."""
    key = _cache_key(kind, prompt)
    if allow_cache and key.exists():
        return key.read_text(encoding="utf-8"), "cache"
    provider = PROVIDER
    if provider == "cache":
        raise RuntimeError("cache miss and provider=cache")
    dispatch = {
        "openai": _call_openai,
        "anthropic": _call_anthropic,
        "claude-cli": _call_claude_cli,
    }
    order = _provider_order()
    if not order or any(p not in dispatch for p in order):
        raise RuntimeError(f"未知模型通道：{provider}")
    last_errors: list[str] = []
    for _attempt in range(max(0, retries) + 1):
        errors = []
        for p in order:
            try:
                out = dispatch[p](prompt, system)
                if not isinstance(out, str) or not out.strip():
                    raise RuntimeError("模型返回空文本")
                key.write_text(out, encoding="utf-8")
                return out, p
            except Exception as e:  # noqa: BLE001 - fall through to next provider
                errors.append(f"{p}: {e}")
        last_errors = errors
    raise RuntimeError(" | ".join(last_errors))


def parse_json(raw: str) -> Any:
    raw = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if m:
        raw = m.group(1).strip()
    start = min([i for i in (raw.find("{"), raw.find("[")) if i >= 0], default=0)
    raw = raw[start:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # trim trailing junk after last closing bracket
        end = max(raw.rfind("}"), raw.rfind("]"))
        return json.loads(raw[: end + 1])


def _print_parse_retry(raw: str) -> None:
    """Print a bounded malformed response without breaking on a GBK terminal."""
    message = f"OpenAI 返回不是完整 JSON，准备重试；原始输出前 300 字：{raw[:300]}"
    try:
        print(message, file=sys.stderr)
    except UnicodeEncodeError:
        sys.stderr.buffer.write((message + "\n").encode("utf-8", errors="replace"))


def _parse_model_json(
    kind: str,
    prompt: str,
    system: str,
    *,
    allow_cache: bool,
) -> tuple[Any, str]:
    raw, source = call_model(kind, prompt, system, allow_cache=allow_cache)
    try:
        return parse_json(raw), source
    except (TypeError, ValueError, json.JSONDecodeError):
        if source != "openai":
            raise
        _print_parse_retry(raw)
        retry_raw, retry_source = call_model(
            kind,
            prompt,
            system,
            allow_cache=False,
            retries=1,
        )
        return parse_json(retry_raw), retry_source


# ---------------------------------------------------------------- prompts
SYSTEM = (
    "你是一位长篇小说的责任编辑，专门负责设定与连续性核对。"
    "你不写正文，不改文风，不评价文笔。你只做两件事：从章节中抽取可核对的事实；对新章节找出与既有事实的矛盾。"
    "每一条结论都必须附上原文引用，引用必须是逐字复制的原文片段（10 到 60 字），不得改写。"
    "只输出 JSON，不要任何解释。"
)

EXTRACT_PROMPT = """下面是一部小说的第 {n} 章，标题「{title}」。同时给你当前已有的设定库（可能为空）。

请从这一章中抽取**以后写作时容易写错、且能被核对的事实**，合并进设定库。规则：
- 只记录文中明确写出的事实，不推测。只要「硬事实」：外貌特征、年龄、身份、惯用手、人物关系、伤势/能力/状态及其变化、地点的方位与结构、物品的归属与所在位置、时间标记与事件先后。不要记录情绪、动作细节、对话内容本身。
- 这一章最多抽 15 条，宁缺毋滥。同一件事只记一条，不要在多个实体下重复。
- 每条事实附 quote：这一章里逐字复制的原文片段（10 到 60 字）。
- 若某事实是对既有事实的**更新**（例如受伤、物品易手、关系改变、人物死亡），必须记录，并在 fact 里写清「由 X 变为 Y」。
- 同一实体用同一 name；已有实体不要重复创建，只追加 facts。
- timeline 类型只建一个实体，name 用「第{n}章」，facts 按顺序列出 2 到 5 个关键事件，每条带时间标记（如「三日后」「当夜」「次日清晨」）和距故事开始的推算天数（如「第 4 天」）。

输出 JSON：
{{"entries":[{{"type":"character|location|item|timeline","name":"...","facts":[{{"fact":"...","quote":"..."}}]}}]}}

当前设定库（压缩）：
{bible}

第 {n} 章正文：
<<<
{text}
>>>"""

CHECK_PROMPT = """下面是一部小说的设定库（由前 {n} 章抽取，每条事实带来源章节与原文），以及作者刚写的新章节「{title}」。

请找出新章节中与设定库**矛盾**的地方。规则：
- 只报真正的矛盾：新章节明确写出的内容与设定库中某条事实不能同时为真。
- 不报「没有提到」「风格变化」「情节合理性」。
- 若新章节明确写出了变化的原因（例如「伤已痊愈」「把玉佩送给了她」），不算矛盾。
- 作者已标记为「有意为之」的项目列在下方，与其相同的矛盾不要再报。
- 每条矛盾给出：type（fact 事实 / time 时间 / character 人设 / location 地点 / item 物品）、severity（high 读者一定会发现 / medium 细心读者会发现 / low 细节）、bible_entry（设定库里的实体 name）、bible_fact（冲突的那条事实原文）、bible_chapter（该事实来源章节）、bible_quote（该事实的原文引用，逐字）、conflict_quote（新章节里冲突处的原文，逐字复制 10 到 60 字）、explanation（一句话说明为什么矛盾）、suggestion（一句话修改建议，改新章节而不是改设定）。
- 最多报 15 条，按 severity 从高到低。

输出 JSON：
{{"findings":[{{"type":"...","severity":"high|medium|low","bible_entry":"...","bible_fact":"...","bible_chapter":"...","bible_quote":"...","conflict_quote":"...","explanation":"...","suggestion":"..."}}]}}

设定库：
{bible}

作者标记为有意为之（勿报）：
{intentional}

新章节正文：
<<<
{text}
>>>"""


# ---------------------------------------------------------------- bible ops
def compact_bible(bible: list[dict], with_quotes: bool = True) -> str:
    if not bible:
        return "（空）"
    lines = []
    for e in bible:
        lines.append(f"[{TYPE_LABEL.get(e['type'], e['type'])}] {e['name']}")
        for f in e["facts"]:
            q = f" ｜引:「{f['quote']}」" if with_quotes and f.get("quote") else ""
            lines.append(f"  - ({f['chapter']}) {f['fact']}{q}")
    return "\n".join(lines)


def merge_entries(bible: list[dict], new_entries: list[dict], chapter_title: str, chapter_text: str) -> int:
    """Merge extracted entries into bible in place. Returns number of facts added."""
    added = 0
    by_key = {(e["type"], e["name"]): e for e in bible}
    for ne in new_entries:
        t = ne.get("type")
        name = (ne.get("name") or "").strip()
        if t not in ENTRY_TYPES or not name:
            continue
        entry = by_key.get((t, name))
        if not entry:
            entry = {"id": uuid.uuid4().hex[:8], "type": t, "name": name, "facts": []}
            bible.append(entry)
            by_key[(t, name)] = entry
        existing = {f["fact"] for f in entry["facts"]}
        for f in ne.get("facts", []):
            fact = (f.get("fact") or "").strip()
            if not fact or fact in existing:
                continue
            quote = (f.get("quote") or "").strip()
            entry["facts"].append({
                "id": uuid.uuid4().hex[:8],
                "fact": fact,
                "quote": quote,
                "quote_verified": bool(quote) and quote in chapter_text,
                "chapter": chapter_title,
            })
            existing.add(fact)
            added += 1
    return added


def match_rule(finding: dict, rules: list[dict]) -> str | None:
    """Return the intentional-rule id matching a finding, if any.

    Rules are intentionally conservative: entity names must match exactly,
    then either the fact text or a verified bible quote must provide a stable
    textual anchor. This keeps the filtering decision inspectable and avoids
    treating a merely similar character name as an author's exception.
    """
    entry_name = (finding.get("bible_entry") or "").strip()
    finding_fact = (finding.get("bible_fact") or "").strip()
    finding_quote = (finding.get("bible_quote") or "").strip()
    if not entry_name:
        return None

    def compact(value: str) -> str:
        return re.sub(r"\s+", "", value)

    for rule in rules:
        if (rule.get("entry") or "").strip() != entry_name:
            continue
        rule_fact = (rule.get("fact") or "").strip()
        rule_quote = (rule.get("bible_quote") or "").strip()
        if rule_fact and finding_fact and rule_fact == finding_fact:
            return rule.get("id")
        if rule_quote and finding_quote and (rule_quote in finding_quote or finding_quote in rule_quote):
            return rule.get("id")
        left = compact(rule_fact)
        right = compact(finding_fact)
        if left and right:
            common = SequenceMatcher(None, left, right).find_longest_match(
                0, len(left), 0, len(right)
            )
            if common.size >= 12:
                return rule.get("id")
    return None


def extract_chapter(bible: list[dict], n: int, chapter: dict, *, allow_cache: bool = True) -> dict:
    prompt = EXTRACT_PROMPT.format(n=n, title=chapter["title"], bible=compact_bible(bible, with_quotes=False), text=chapter["text"])
    data, source = _parse_model_json("extract", prompt, SYSTEM, allow_cache=allow_cache)
    added = merge_entries(bible, data.get("entries", []), chapter["title"], chapter["text"])
    return {"added": added, "source": source}


def check_chapter(bible: list[dict], n: int, chapter: dict, intentional: list[str], *, allow_cache: bool = True) -> dict:
    prompt = CHECK_PROMPT.format(
        n=n, title=chapter["title"], bible=compact_bible(bible), text=chapter["text"],
        intentional="\n".join(f"- {s}" for s in intentional) or "（无）",
    )
    data, source = _parse_model_json("check", prompt, SYSTEM, allow_cache=allow_cache)
    findings = []
    all_quotes = [f["quote"] for e in bible for f in e["facts"] if f.get("quote")]
    for f in data.get("findings", []):
        cq = (f.get("conflict_quote") or "").strip()
        bq = (f.get("bible_quote") or "").strip()
        findings.append({
            "id": uuid.uuid4().hex[:8],
            "type": f.get("type") if f.get("type") in FINDING_TYPES else "fact",
            "severity": f.get("severity") if f.get("severity") in ("high", "medium", "low") else "medium",
            "bible_entry": f.get("bible_entry", ""),
            "bible_fact": f.get("bible_fact", ""),
            "bible_chapter": f.get("bible_chapter", ""),
            "bible_quote": bq,
            "bible_quote_verified": bool(bq) and any(bq in q or q in bq for q in all_quotes),
            "conflict_quote": cq,
            "conflict_verified": bool(cq) and cq in chapter["text"],
            "explanation": f.get("explanation", ""),
            "suggestion": f.get("suggestion", ""),
            "status": "open",
        })
    return {"findings": findings, "source": source}
