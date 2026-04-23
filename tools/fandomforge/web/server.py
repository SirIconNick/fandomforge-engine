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
import os
import threading
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request

from fandomforge.web.auth import api_key_configured, require_api_key
from fandomforge.web.persistent_jobs import resolve_job_store
from fandomforge.web.pipeline import (
    extract_video_id,
    incoming_root,
    run_pipeline,
    validate_url,
)

store = resolve_job_store()

logger = logging.getLogger("ff.web")

_docs_url = "/api/docs"
if os.environ.get("FF_DISABLE_DOCS", "").lower() in ("1", "true", "yes"):
    _docs_url = None

app = FastAPI(title="FandomForge", docs_url=_docs_url)
app.middleware("http")(require_api_key)

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
    ok, msg = validate_url(body.url)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    video_id = extract_video_id(body.url)

    # Duplicate detection — if we already have a forensic for this URL
    # (matched by video_id), return the cached result immediately instead
    # of re-running the pipeline.
    cached = _find_cached_forensic(video_id)
    if cached is not None:
        job = store.create(url=body.url, bucket_hint=body.bucket_hint)
        store.update(job.job_id, status="done", forensic_id=video_id)
        store.append_step(job.job_id, f"reusing cached forensic for {video_id}")
        # Fire the analyst synchronously — it's fast (<100ms) and the
        # client expects full results in the next poll.
        _finalize_cached(job.job_id, cached)
        return {"job_id": job.job_id, "status": "done", "cached": True, "forensic_id": video_id}

    job = store.create(url=body.url, bucket_hint=body.bucket_hint)
    # Run in a plain Python thread so yt-dlp + ffmpeg + librosa + whisper
    # don't block the uvicorn event loop.
    thread = threading.Thread(
        target=run_pipeline, args=(store, job.job_id), daemon=True
    )
    thread.start()
    if msg:  # non-fatal validation warning
        store.append_step(job.job_id, f"note: {msg}")
    return {"job_id": job.job_id, "status": job.status, "cached": False}


def _find_cached_forensic(video_id: str) -> Path | None:
    """Check every known location for an existing forensic JSON matching
    ``video_id`` — web incoming dir + references/<bucket>/forensic/."""
    # Prefer the web incoming dir (paste-link flow's home)
    incoming = incoming_root() / video_id / f"{video_id}.forensic.json"
    if incoming.exists():
        return incoming
    # Also check curated references corpus (any bucket)
    refs = _references_dir()
    if refs.exists():
        for child in refs.iterdir():
            candidate = child / "forensic" / f"{video_id}.forensic.json"
            if candidate.exists():
                return candidate
    return None


def _finalize_cached(job_id: str, forensic_path: Path) -> None:
    """Synchronous analyze pass for a cached forensic — mirrors the
    pipeline's _finalize_analysis but skips the background thread since
    the heavy work is already on disk."""
    import json as _json
    from fandomforge.intelligence.forensic_analyst import analyze_forensic
    from fandomforge.web.pipeline import _derive_tags

    try:
        forensic = _json.loads(forensic_path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError) as exc:
        store.update(job_id, status="failed", error=f"cached load: {exc}")
        return
    try:
        analysis = analyze_forensic(forensic)
        payload = analysis.to_dict()
        payload["auto_tags"] = _derive_tags(forensic, payload)
    except Exception as exc:  # noqa: BLE001
        store.update(job_id, status="failed", error=f"analyst: {exc}")
        return
    store.update(job_id, status="done", forensic=forensic, analysis=payload)


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


@app.get("/api/corrections")
async def api_list_corrections(limit: int = 50) -> list[dict[str, Any]]:
    """Paginated list of recent corrections, newest first."""
    from fandomforge.intelligence.corrections_journal import iter_corrections
    entries = [e.to_dict() for e in iter_corrections()]
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return entries[: max(1, min(int(limit), 500))]


@app.delete("/api/correct/{forensic_id}")
async def api_delete_correction(forensic_id: str) -> dict[str, Any]:
    """Delete every correction for ``forensic_id``. Idempotent — deleting
    a nonexistent id returns deleted=0 instead of 404."""
    from fandomforge.intelligence.corrections_journal import delete_corrections_for
    from fandomforge.intelligence.forensic_craft_bias import clear_cache

    n = delete_corrections_for(forensic_id)
    clear_cache()
    return {"ok": True, "deleted": n, "forensic_id": forensic_id}


@app.get("/api/effective-weights/{bucket}")
async def api_effective_weights(bucket: str) -> dict[str, Any]:
    """Per-feature breakdown of every bias layer's contribution to the
    final craft weight for ``bucket``. Lets the UI explain why a weight
    is what it is."""
    from fandomforge.intelligence.forensic_craft_bias import (
        effective_weights_breakdown,
    )
    from fandomforge.config import craft_weights_for

    safe = Path(bucket).name
    breakdown = effective_weights_breakdown(safe)
    live = craft_weights_for(safe)  # what the pipeline actually sees
    return {
        "bucket": safe,
        "breakdown": breakdown,
        "live_effective": live,
    }


@app.get("/api/video/{forensic_id}")
async def api_video(forensic_id: str):
    """Stream the downloaded mp4 so the UI can preview the video the user
    is correcting. Only returns files under the incoming or references
    trees — no arbitrary path traversal."""
    safe = Path(forensic_id).name
    # Search web-incoming first, then references buckets
    incoming = incoming_root() / safe / f"{safe}.mp4"
    if incoming.exists():
        return FileResponse(
            incoming,
            media_type="video/mp4",
            filename=f"{safe}.mp4",
        )
    refs = _references_dir()
    if refs.exists():
        for child in refs.iterdir():
            candidate = child / "forensic" / f"{safe}.mp4"
            if candidate.exists():
                return FileResponse(
                    candidate,
                    media_type="video/mp4",
                    filename=f"{safe}.mp4",
                )
            # Legacy per-playlist dirs store mp4 at top level
            candidate = child / f"{safe}.mp4"
            if candidate.exists():
                return FileResponse(
                    candidate,
                    media_type="video/mp4",
                    filename=f"{safe}.mp4",
                )
    raise HTTPException(status_code=404, detail=f"no video for {safe}")


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
    tunnel_file = _repo_root() / ".cache" / "ff" / "tunnel-url.txt"
    tunnel_url = ""
    if tunnel_file.exists():
        try:
            tunnel_url = tunnel_file.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return {
        "ok": True,
        "incoming_root": str(incoming_root()),
        "references_dir": str(_references_dir()),
        "auth_required": api_key_configured(),
        "tunnel_url": tunnel_url,
    }
