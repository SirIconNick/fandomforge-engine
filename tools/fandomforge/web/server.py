"""FandomForge web UI server.

Routes:

* ``GET /`` — single-page HTML UI: paste-link form, live analysis panel,
  correction form, bucket reference guide.
* ``POST /api/analyze`` — queue a forensic analysis for a URL. Returns
  a ``job_id`` the UI polls.
* ``GET /api/job/<job_id>`` — poll job status + partial results.
* ``GET /api/recent`` — recent jobs across the session (UI history).
* ``POST /api/correct`` — persist a user correction to the corrections
  journal. Correction flows into the craft-weight bias stack next time
  ``config.craft_weights_for()`` is called.
* ``GET /api/buckets`` — list every bucket the corpus knows about with
  its latest consensus + sample counts.
* ``GET /api/bucket/<name>`` — full bucket report markdown + json.
* ``GET /api/summary`` — corpus + training + corrections snapshot for
  the home-page hero stats.

Run via ``ff serve`` (new CLI command) or directly::

    uvicorn fandomforge.web.server:app --host 0.0.0.0 --port 4321
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request

from fandomforge.web.jobs import store
from fandomforge.web.pipeline import run_pipeline, incoming_root, extract_video_id

logger = logging.getLogger("ff.web")

app = FastAPI(title="FandomForge", docs_url="/api/docs")

_WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))
app.mount(
    "/static",
    StaticFiles(directory=str(_WEB_DIR / "static")),
    name="static",
)


# ---------- request bodies -------------------------------------------------


class AnalyzeRequest(BaseModel):
    url: str = Field(..., min_length=5, max_length=512)
    bucket_hint: str = Field(default="multifandom", max_length=32)


class CorrectionRequest(BaseModel):
    forensic_id: str = Field(..., min_length=1, max_length=64)
    url: str = ""
    title: str = ""
    original_bucket: str = ""
    corrected_bucket: str = Field(..., min_length=1, max_length=32)
    original_craft_weights: dict[str, float] = Field(default_factory=dict)
    corrected_craft_weights: dict[str, float] = Field(default_factory=dict)
    tags_added: list[str] = Field(default_factory=list)
    tags_removed: list[str] = Field(default_factory=list)
    notes: str = ""


# ---------- helpers --------------------------------------------------------


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    return Path.cwd()


def _references_dir() -> Path:
    return _repo_root() / "references"


# ---------- HTML -----------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    # Starlette >= 1.0 expects (request, name, context) — the legacy
    # ("name", {"request": request, ...}) form triggers an unhashable-dict
    # error deep inside the Jinja2 cache.
    return templates.TemplateResponse(request, "index.html", {})


# ---------- analyze --------------------------------------------------------


@app.post("/api/analyze")
async def api_analyze(body: AnalyzeRequest, tasks: BackgroundTasks) -> dict[str, Any]:
    job = store.create(url=body.url, bucket_hint=body.bucket_hint)
    # Run in a plain Python thread so yt-dlp + ffmpeg + librosa + whisper
    # don't block the uvicorn event loop. FastAPI's BackgroundTasks runs
    # after the response is sent, which is what we want — the client only
    # needs job_id before polling.
    thread = threading.Thread(
        target=run_pipeline, args=(store, job.job_id), daemon=True
    )
    thread.start()
    return {"job_id": job.job_id, "status": job.status}


@app.get("/api/job/{job_id}")
async def api_job(job_id: str) -> dict[str, Any]:
    snap = store.snapshot(job_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="unknown job_id")
    return snap


@app.get("/api/recent")
async def api_recent(limit: int = 20) -> list[dict[str, Any]]:
    return store.list_recent(limit=limit)


# ---------- corrections ----------------------------------------------------


@app.post("/api/correct")
async def api_correct(body: CorrectionRequest) -> dict[str, Any]:
    from fandomforge.intelligence.corrections_journal import (
        CorrectionEntry, append_correction,
    )
    from fandomforge.intelligence.forensic_craft_bias import clear_cache

    entry = CorrectionEntry(
        forensic_id=body.forensic_id,
        url=body.url,
        title=body.title,
        original_bucket=body.original_bucket,
        corrected_bucket=body.corrected_bucket,
        original_craft_weights=body.original_craft_weights,
        corrected_craft_weights=body.corrected_craft_weights,
        tags_added=body.tags_added,
        tags_removed=body.tags_removed,
        notes=body.notes,
    )
    path = append_correction(entry)
    clear_cache()
    return {
        "ok": True,
        "path": str(path),
        "message": f"correction recorded — {body.corrected_bucket} will get a 40% pull toward your weights",
    }


# ---------- bucket reference guide -----------------------------------------


@app.get("/api/buckets")
async def api_buckets() -> list[dict[str, Any]]:
    refs = _references_dir()
    out: list[dict[str, Any]] = []
    if not refs.exists():
        return out
    for child in sorted(refs.iterdir()):
        if not child.is_dir():
            continue
        report_path = child / "bucket-report.json"
        if not report_path.exists():
            continue
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cpm_range = (
            data.get("consensus_target_cpm_range")
            or data.get("target_cpm_range")
            or [None, None]
        )
        out.append(
            {
                "name": child.name,
                "sample_size": data.get("sample_count")
                               or data.get("sample_size") or 0,
                "recommended_edit_type": data.get("consensus_edit_type")
                                         or data.get("recommended_edit_type") or "",
                "target_cpm_min": cpm_range[0] if len(cpm_range) > 0 else None,
                "target_cpm_max": cpm_range[1] if len(cpm_range) > 1 else None,
                "consensus_craft_weights": data.get("consensus_craft_weights") or {},
                "defining_moves": data.get("defining_moves") or [],
                "anti_patterns": data.get("anti_patterns") or [],
                "mined_priors": data.get("mined_priors") or {},
            }
        )
    return out


@app.get("/api/bucket/{name}")
async def api_bucket(name: str) -> dict[str, Any]:
    safe = Path(name).name
    report_json = _references_dir() / safe / "bucket-report.json"
    report_md = _references_dir() / safe / "bucket-report.md"
    if not report_json.exists():
        raise HTTPException(status_code=404, detail=f"unknown bucket: {safe}")
    try:
        data = json.loads(report_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"unreadable: {exc}") from None
    markdown = ""
    if report_md.exists():
        try:
            markdown = report_md.read_text(encoding="utf-8")
        except OSError:
            pass
    return {"name": safe, "json": data, "markdown": markdown}


# ---------- summary --------------------------------------------------------


@app.get("/api/summary")
async def api_summary() -> dict[str, Any]:
    refs = _references_dir()
    total_forensics = 0
    per_bucket: dict[str, int] = {}
    if refs.exists():
        for child in refs.iterdir():
            if not child.is_dir():
                continue
            forensic_dir = child / "forensic"
            if not forensic_dir.exists():
                continue
            count = len(list(forensic_dir.glob("*.forensic.json")))
            if count:
                per_bucket[child.name] = count
                total_forensics += count

    from fandomforge.intelligence.training_journal import summary as training_summary
    from fandomforge.intelligence.corrections_journal import corrections_summary

    return {
        "total_forensics": total_forensics,
        "per_bucket": per_bucket,
        "training": training_summary(),
        "corrections": corrections_summary(),
    }


@app.get("/api/health")
async def api_health() -> dict[str, Any]:
    return {
        "ok": True,
        "incoming_root": str(incoming_root()),
        "references_dir": str(_references_dir()),
    }
