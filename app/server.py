"""Gatekeeper HTTP server (FastAPI). Run: uvicorn app.server:app --reload --port 8765"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
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
    return json.loads(p.read_text(encoding="utf-8"))


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


# ---------------------------------------------------------------- routes
@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/meta")
def meta():
    return {"provider": engine.PROVIDER, "model": engine.MODEL}


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
    project = {
        "id": uuid.uuid4().hex[:10],
        "title": req.title,
        "chapters": [{"title": c["title"], "text": c["text"], "extracted": False} for c in chapters],
        "bible": [],
        "checks": [],
        "intentional": [],
    }
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
    intentional = [d["text"] for d in project["intentional"]]
    try:
        result = engine.check_chapter(project["bible"], len(project["chapters"]), chapter, intentional, allow_cache=req.use_cache)
    except Exception as e:
        raise HTTPException(502, f"模型调用失败：{e}") from e
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
                key = f"{f['bible_entry']}：{f['bible_fact']} ⇄ {f['conflict_quote']}"
                project["intentional"] = [d for d in project["intentional"] if d["finding_id"] != fid]
                if req.status == "intentional":
                    project["intentional"].append({"finding_id": fid, "text": key})
                save(project)
                return project
    raise HTTPException(404, "finding not found")


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


@app.delete("/api/projects/{pid}/bible/{eid}/facts/{fid}")
def delete_fact(pid: str, eid: str, fid: str):
    project = load(pid)
    for e in project["bible"]:
        if e["id"] == eid:
            e["facts"] = [f for f in e["facts"] if f["id"] != fid]
    project["bible"] = [e for e in project["bible"] if e["facts"]]
    return save(project)


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/favicon.ico")
def favicon():
    from fastapi.responses import Response
    return Response(status_code=204)
