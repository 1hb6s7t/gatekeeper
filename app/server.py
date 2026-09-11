"""Gatekeeper HTTP server (FastAPI). Run: uvicorn app.server:app --reload --port 8765"""
from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import engine

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "projects"
DATA.mkdir(parents=True, exist_ok=True)
SAMPLES = ROOT / "samples"

app = FastAPI(title="守门人 Gatekeeper")


# ---------------------------------------------------------------- storage
def _path(pid: str) -> Path:
    return DATA / f"{pid}.json"


def load(pid: str) -> dict:
    p = _path(pid)
    if not p.exists():
        raise HTTPException(404, "project not found")
    project = json.loads(p.read_text(encoding="utf-8"))
    if "rules" not in project:
        project["rules"] = _migrate_intentional(project.get("intentional", []), project)
    project.setdefault("checks", [])
    project.setdefault("bible", [])
    return project


def save(project: dict) -> dict:
    _path(project["id"]).write_text(json.dumps(project, ensure_ascii=False, indent=1), encoding="utf-8")
    return project


# ---------------------------------------------------------------- models
class NewProject(BaseModel):
    title: str = "未命名作品"
    text: str


class ExtractReq(BaseModel):
    chapter_index: int
    use_cache: bool = True


class CheckReq(BaseModel):
    title: str = "新章节"
    text: str
    use_cache: bool = True


class ResolveReq(BaseModel):
    status: str  # accepted | ignored | intentional | open
    note: str = ""


class BibleFactReq(BaseModel):
    type: str
    name: str
    fact: str
    chapter: str
    quote: str = ""


class BibleFactPatch(BaseModel):
    fact: str | None = None
    quote: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate_intentional(items: list[dict], project: dict) -> list[dict]:
    """Convert the first-version text exceptions into structured rules."""
    rules = []
    for item in items or []:
        text = (item.get("text") or "").strip()
        entry, separator, rest = text.partition("：")
        fact, separator2, conflict_quote = rest.partition(" ⇄ ")
        if not entry or not fact:
            continue
        bible_quote = ""
        for bible_entry in project.get("bible", []):
            if bible_entry.get("name") != entry:
                continue
            bible_fact = next((f for f in bible_entry.get("facts", []) if f.get("fact") == fact), None)
            if bible_fact:
                bible_quote = bible_fact.get("quote", "")
                break
        rules.append({
            "id": item.get("id") or uuid.uuid4().hex[:8],
            "kind": "intentional",
            "entry": entry.strip(),
            "fact": fact.strip(),
            "bible_quote": bible_quote,
            "conflict_quote": conflict_quote.strip() if separator2 else "",
            "note": "",
            "from_finding": item.get("finding_id", ""),
            "created": item.get("created") or _now_iso(),
        })
    return rules


def _rule_prompt(rule: dict) -> str:
    anchor = rule.get("conflict_quote") or rule.get("bible_quote") or rule.get("fact", "")
    return f"{rule.get('entry', '')}：{rule.get('fact', '')} ⇄ {anchor}"


# ---------------------------------------------------------------- routes
@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/meta")
def meta():
    model = engine.OPENAI_MODEL if engine.PROVIDER == "openai" else engine.MODEL
    return {"provider": engine.PROVIDER, "model": model}


@app.get("/api/sample")
def sample():
    return {
        "title": "青崖夜行（示例）",
        "manuscript": (SAMPLES / "manuscript.md").read_text(encoding="utf-8"),
        "new_chapter_title": "第六章 云州夜雨",
        "new_chapter": (SAMPLES / "new_chapter.md").read_text(encoding="utf-8"),
        "answer_key": json.loads((SAMPLES / "answer_key.json").read_text(encoding="utf-8")),
    }


@app.post("/api/projects")
def create_project(req: NewProject):
    chapters = engine.split_chapters(req.text)
    if not chapters or not chapters[0]["text"]:
        raise HTTPException(400, "没有识别到正文")
    warnings = [
        f"{chapter['title']} {len(chapter['text'])} 字，超出建议长度，抽取可能变慢或截断"
        for chapter in chapters
        if len(chapter["text"]) > 6000
    ]
    project = {
        "id": uuid.uuid4().hex[:10],
        "title": req.title,
        "chapters": [{"title": c["title"], "text": c["text"], "extracted": False} for c in chapters],
        "bible": [],
        "checks": [],
        "rules": [],
    }
    if warnings:
        project["warnings"] = warnings
    return save(project)


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return load(pid)


@app.post("/api/projects/{pid}/extract")
def extract(pid: str, req: ExtractReq):
    project = load(pid)
    i = req.chapter_index
    if i < 0 or i >= len(project["chapters"]):
        raise HTTPException(400, "chapter_index out of range")
    ch = project["chapters"][i]
    try:
        result = engine.extract_chapter(project["bible"], i + 1, ch, allow_cache=req.use_cache)
    except Exception as e:  # surface model/provider errors to the UI
        raise HTTPException(502, f"模型调用失败：{e}") from e
    ch["extracted"] = True
    save(project)
    return {"project": project, "added": result["added"], "source": result["source"]}


@app.post("/api/projects/{pid}/check")
def check(pid: str, req: CheckReq):
    project = load(pid)
    if not project["bible"]:
        raise HTTPException(400, "请先建立设定库")
    parts = engine.split_chapters(req.text)
    body = parts[0]["text"] if len(parts) == 1 else req.text.strip()
    title = req.title.strip() or (parts[0]["title"] if parts[0]["title"] != "第1章" else "新章节")
    chapter = {"title": title, "text": body}
    rules = project.get("rules", [])
    intentional = [_rule_prompt(rule) for rule in rules]

    # A re-check after adding an intentional rule does not need another model
    # call when the chapter and bible are unchanged. Reuse the previous raw
    # findings and apply the new program-level rule filter below.
    prior = None
    if req.use_cache:
        prior = next(
            (
                old
                for old in reversed(project.get("checks", []))
                if old.get("title") == chapter["title"] and old.get("text") == chapter["text"]
            ),
            None,
        )
    if prior is not None:
        findings = deepcopy(prior.get("findings", []))
        for finding in findings:
            finding["id"] = uuid.uuid4().hex[:8]
            finding["status"] = "open"
            finding.pop("rule_id", None)
        result = {"findings": findings, "source": "cache"}
    else:
        try:
            result = engine.check_chapter(project["bible"], len(project["chapters"]), chapter, intentional, allow_cache=req.use_cache)
        except Exception as e:
            raise HTTPException(502, f"模型调用失败：{e}") from e

    for finding in result["findings"]:
        rule_id = engine.match_rule(finding, rules)
        if rule_id:
            finding["status"] = "intentional"
            finding["rule_id"] = rule_id
        else:
            finding["status"] = "open"
            finding.pop("rule_id", None)

    check_rec = {"id": uuid.uuid4().hex[:8], "title": chapter["title"], "text": chapter["text"], "findings": result["findings"], "source": result["source"]}
    project["checks"].append(check_rec)
    save(project)
    return {"project": project, "check": check_rec}


@app.post("/api/projects/{pid}/checks/{cid}/findings/{fid}")
def resolve(pid: str, cid: str, fid: str, req: ResolveReq):
    if req.status not in ("accepted", "ignored", "intentional", "open"):
        raise HTTPException(400, "bad status")
    project = load(pid)
    for c in project["checks"]:
        if c["id"] != cid:
            continue
        for f in c["findings"]:
            if f["id"] == fid:
                f["status"] = req.status
                rules = project.setdefault("rules", [])
                project["rules"] = [d for d in rules if d.get("from_finding") != fid]
                f.pop("rule_id", None)
                if req.status == "intentional":
                    rule = next(
                        (
                            d
                            for d in project["rules"]
                            if d.get("entry") == f.get("bible_entry") and d.get("fact") == f.get("bible_fact")
                        ),
                        None,
                    )
                    if rule is None:
                        rule = {
                            "id": uuid.uuid4().hex[:8],
                            "kind": "intentional",
                            "entry": f.get("bible_entry", ""),
                            "fact": f.get("bible_fact", ""),
                            "bible_quote": f.get("bible_quote", ""),
                            "conflict_quote": f.get("conflict_quote", ""),
                            "note": req.note.strip(),
                            "from_finding": fid,
                            "created": _now_iso(),
                        }
                        project["rules"].append(rule)
                    elif req.note.strip():
                        rule["note"] = req.note.strip()
                    f["rule_id"] = rule["id"]
                save(project)
                return project
    raise HTTPException(404, "finding not found")


@app.get("/api/projects/{pid}/rules")
def get_rules(pid: str):
    return {"rules": load(pid).get("rules", [])}


@app.delete("/api/projects/{pid}/rules/{rid}")
def delete_rule(pid: str, rid: str):
    project = load(pid)
    rules = project.get("rules", [])
    if not any(rule.get("id") == rid for rule in rules):
        raise HTTPException(404, "rule not found")
    project["rules"] = [rule for rule in rules if rule.get("id") != rid]
    for check_rec in project.get("checks", []):
        for finding in check_rec.get("findings", []):
            if finding.get("rule_id") == rid:
                finding.pop("rule_id", None)
                finding["status"] = "open"
    return save(project)


@app.post("/api/projects/{pid}/checks/{cid}/commit")
def commit(pid: str, cid: str):
    """Append the checked chapter to the manuscript so it can be extracted next."""
    project = load(pid)
    for c in project["checks"]:
        if c["id"] == cid:
            project["chapters"].append({"title": c["title"], "text": c["text"], "extracted": False})
            save(project)
            return project
    raise HTTPException(404, "check not found")


@app.post("/api/projects/{pid}/bible/facts")
def add_fact(pid: str, req: BibleFactReq):
    if req.type not in engine.ENTRY_TYPES:
        raise HTTPException(400, "bad bible entry type")
    name = req.name.strip()
    fact = req.fact.strip()
    if not name or not fact:
        raise HTTPException(400, "实体名和事实不能为空")
    project = load(pid)
    chapter = next((c for c in project["chapters"] if c["title"] == req.chapter), None)
    if chapter is None:
        raise HTTPException(400, "来源章节不存在")
    engine.merge_entries(
        project["bible"],
        [{"type": req.type, "name": name, "facts": [{"fact": fact, "quote": req.quote.strip()}]}],
        chapter["title"],
        chapter["text"],
        chapter_no=engine.chapter_number(chapter["title"]),
    )
    return save(project)


@app.patch("/api/projects/{pid}/bible/{eid}/facts/{fid}")
def update_fact(pid: str, eid: str, fid: str, req: BibleFactPatch):
    if req.fact is None and req.quote is None:
        raise HTTPException(400, "没有要修改的字段")
    project = load(pid)
    for entry in project["bible"]:
        if entry.get("id") != eid:
            continue
        for fact_record in entry.get("facts", []):
            if fact_record.get("id") != fid:
                continue
            if req.fact is not None:
                fact = req.fact.strip()
                if not fact:
                    raise HTTPException(400, "事实不能为空")
                fact_record["fact"] = fact
                change = engine._change_marker(fact)
                if change:
                    fact_record["change"] = change
                else:
                    fact_record.pop("change", None)
                fact_record["chapter_no"] = engine.chapter_number(
                    fact,
                    engine.chapter_number(fact_record.get("chapter", "")),
                )
            if req.quote is not None:
                fact_record["quote"] = req.quote.strip()
            source = next(
                (c for c in project["chapters"] if c["title"] == fact_record.get("chapter")),
                None,
            )
            fact_record["quote_verified"] = bool(fact_record.get("quote")) and bool(
                source and engine.quote_in(source.get("text", ""), fact_record["quote"])
            )
            return save(project)
    raise HTTPException(404, "fact not found")


@app.delete("/api/projects/{pid}/bible/{eid}/facts/{fid}")
def delete_fact(pid: str, eid: str, fid: str):
    project = load(pid)
    for e in project["bible"]:
        if e["id"] == eid:
            e["facts"] = [f for f in e["facts"] if f["id"] != fid]
    project["bible"] = [e for e in project["bible"] if e["facts"]]
    return save(project)


def _md_inline(value: object) -> str:
    return str(value or "").replace("\r", "").replace("\n", " ").strip()


def _export_markdown(project: dict) -> str:
    lines = [f"# {_md_inline(project.get('title') or '未命名作品')}", ""]
    lines.extend(["## 章节", ""])
    for i, chapter in enumerate(project.get("chapters", []), 1):
        state = "已抽取" if chapter.get("extracted") else "待抽取"
        lines.append(f"{i}. **{_md_inline(chapter.get('title'))}** · {len(chapter.get('text', ''))} 字 · {state}")
    warnings = project.get("warnings") or []
    if warnings:
        lines.extend(["", "> **长度提示**", *[f"> - {_md_inline(warning)}" for warning in warnings]])

    lines.extend(["", "## 设定库", ""])
    order = ["character", "location", "item", "timeline"]
    labels = engine.TYPE_LABEL
    entries = sorted(project.get("bible", []), key=lambda entry: order.index(entry.get("type")) if entry.get("type") in order else len(order))
    if not entries:
        lines.append("（空）")
    for entry in entries:
        lines.extend([f"### {labels.get(entry.get('type'), entry.get('type', ''))}：{_md_inline(entry.get('name'))}", ""])
        for fact in entry.get("facts", []):
            text = _md_inline(fact.get("fact"))
            if fact.get("change"):
                text += f"（变更：{_md_inline(fact['change'].get('from'))} → {_md_inline(fact['change'].get('to'))}）"
            verified = "已核验" if fact.get("quote_verified") else "未核验"
            lines.append(f"- ({_md_inline(fact.get('chapter'))}) {text} · 引用{verified}")
            if fact.get("quote"):
                lines.append(f"  - 原文：「{_md_inline(fact.get('quote'))}」")
        lines.append("")

    rules = project.get("rules", [])
    lines.extend(["## 有意为之规则", ""])
    if not rules:
        lines.append("（无）")
    else:
        for rule in rules:
            lines.append(f"- **{_md_inline(rule.get('entry'))}**：{_md_inline(rule.get('fact'))}")
            if rule.get("note"):
                lines.append(f"  - 备注：{_md_inline(rule.get('note'))}")
        lines.append("")

    lines.extend(["## 最近一次检查", ""])
    checks = project.get("checks", [])
    if not checks:
        lines.append("（尚未检查）")
    else:
        check = checks[-1]
        lines.extend([
            f"### {_md_inline(check.get('title'))}",
            f"来源：{_md_inline(check.get('source'))} · 发现 {len(check.get('findings', []))} 条",
            "",
        ])
        severity = {"high": "高", "medium": "中", "low": "低"}
        statuses = {"open": "待处理", "accepted": "已采纳", "ignored": "已忽略", "intentional": "有意为之"}
        findings = sorted(check.get("findings", []), key=lambda item: {"high": 0, "medium": 1, "low": 2}.get(item.get("severity"), 3))
        if not findings:
            lines.append("没有发现矛盾。")
        for finding in findings:
            state = statuses.get(finding.get("status"), finding.get("status", "待处理"))
            lines.append(f"- **[{severity.get(finding.get('severity'), finding.get('severity', '中'))}] {state}** {_md_inline(finding.get('bible_entry'))} · {_md_inline(finding.get('bible_chapter'))}：{_md_inline(finding.get('bible_fact'))}")
            if finding.get("bible_quote"):
                lines.append(f"  - 设定引用：「{_md_inline(finding.get('bible_quote'))}」")
            if finding.get("conflict_quote"):
                lines.append(f"  - 新章引用：「{_md_inline(finding.get('conflict_quote'))}」")
            if finding.get("explanation"):
                lines.append(f"  - 说明：{_md_inline(finding.get('explanation'))}")
            if finding.get("suggestion"):
                lines.append(f"  - 建议：{_md_inline(finding.get('suggestion'))}")
    return "\n".join(lines).rstrip() + "\n"


@app.get("/api/projects/{pid}/export.md")
def export_markdown(pid: str):
    project = load(pid)
    title = _md_inline(project.get("title") or "gatekeeper") or "gatekeeper"
    filename = quote(f"{title}.md", safe="")
    return Response(
        content=_export_markdown(project),
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.get("/report.mp4")
def report_video():
    """Serve the recorded walkthrough next to the app so reviewers can watch it in the browser."""
    path = ROOT / "docs" / "report.mp4"
    if not path.exists():
        raise HTTPException(404, "report.mp4 not found — see README")
    return FileResponse(path, media_type="video/mp4")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/favicon.ico")
def favicon():
    from fastapi.responses import Response
    return Response(status_code=204)
