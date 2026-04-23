"""Web-side wrapper around the forensic pipeline.

Turns an incoming URL into a finished forensic + analyst report that the
UI can render. Runs on a background thread kicked off from a FastAPI
endpoint. Every step updates the Job in the shared JobStore so the UI
can poll progress.

Design choices:
* Outputs land under ``.cache/ff/web/incoming/<video_id>/`` so the
  paste-link pipeline doesn't pollute the curated ``references/`` tree.
* Video download uses the same yt-dlp helpers the CLI uses.
* Bucket is caller-supplied (``bucket_hint``) and can be corrected later
  via the /api/correct endpoint.
"""

from __future__ import annotations

import json
import logging
import re
import traceback
from pathlib import Path
from typing import Any

from fandomforge.web.jobs import JobStore

logger = logging.getLogger(__name__)

_YOUTUBE_ID_RE = re.compile(
    r"(?:v=|youtu\.be/|youtube\.com/(?:embed|shorts)/)([A-Za-z0-9_-]{8,16})"
)


def extract_video_id(url: str) -> str:
    """Parse a YouTube-style URL into an 11-char video id. Falls back to a
    hash-slug when parsing fails so non-YouTube URLs still produce a
    stable identifier."""
    m = _YOUTUBE_ID_RE.search(url)
    if m:
        return m.group(1)
    import hashlib
    return "url-" + hashlib.blake2b(url.encode(), digest_size=6).hexdigest()


def _resolve_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def incoming_root() -> Path:
    root = _resolve_repo_root() / ".cache" / "ff" / "web" / "incoming"
    root.mkdir(parents=True, exist_ok=True)
    return root


def run_pipeline(store: JobStore, job_id: str) -> None:
    job = store.get(job_id)
    if job is None:
        return
    try:
        from fandomforge.cli import _ytdlp_download, _ytdlp_audio_only
    except ImportError as exc:
        store.update(job_id, status="failed", error=f"cli import failed: {exc}")
        return

    url = job.url
    bucket = job.bucket_hint or "multifandom"
    video_id = extract_video_id(url)
    work_dir = incoming_root() / video_id
    work_dir.mkdir(parents=True, exist_ok=True)

    video_path = work_dir / f"{video_id}.mp4"
    audio_path = work_dir / f"{video_id}.song.wav"
    forensic_out = work_dir / f"{video_id}.forensic.json"

    def _log(msg: str) -> None:
        logger.info("[job %s] %s", job_id, msg)
        store.append_step(job_id, msg)

    store.update(job_id, status="running", forensic_id=video_id)
    _log(f"start — video_id={video_id} bucket_hint={bucket}")

    if forensic_out.exists():
        _log("reusing existing forensic (cached)")
        try:
            forensic = json.loads(forensic_out.read_text(encoding="utf-8"))
            _finalize_analysis(store, job_id, forensic, bucket)
            return
        except (OSError, json.JSONDecodeError) as exc:
            _log(f"cached forensic unreadable: {exc} — re-running")

    if not video_path.exists():
        _log("downloading video via yt-dlp")
        ok = _ytdlp_download(url, video_path)
        if not ok or not video_path.exists():
            store.update(
                job_id, status="failed",
                error="video download failed — check yt-dlp/cookies",
                finished_at=_now(),
            )
            return
        _log(f"downloaded {video_path.stat().st_size // 1024} KB")

    if not audio_path.exists():
        _log("downloading audio via yt-dlp")
        _ytdlp_audio_only(url, audio_path)

    from fandomforge.intelligence.forensic_deconstructor import (
        ForensicRequest,
        deconstruct_video,
    )

    req = ForensicRequest(
        video_path=video_path,
        video_id=video_id,
        bucket=bucket,
        url=url,
        song_audio=audio_path if audio_path.exists() else None,
        output_path=forensic_out,
        progress=_log,
    )

    try:
        forensic = deconstruct_video(req)
    except Exception as exc:  # noqa: BLE001
        logger.exception("forensic pipeline crashed")
        store.update(
            job_id,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            finished_at=_now(),
        )
        store.append_step(job_id, traceback.format_exc(limit=3))
        return

    _finalize_analysis(store, job_id, forensic, bucket)


def _finalize_analysis(
    store: JobStore, job_id: str, forensic: dict[str, Any], bucket: str
) -> None:
    from fandomforge.intelligence.forensic_analyst import analyze_forensic

    try:
        analysis = analyze_forensic(forensic)
        payload = analysis.to_dict()
    except Exception as exc:  # noqa: BLE001
        logger.exception("analyst crashed")
        store.update(
            job_id,
            status="failed",
            error=f"analyst failure: {type(exc).__name__}: {exc}",
            finished_at=_now(),
        )
        return

    payload["auto_tags"] = _derive_tags(forensic, payload)
    store.update(
        job_id,
        status="done",
        forensic=forensic,
        analysis=payload,
        finished_at=_now(),
    )
    store.append_step(
        job_id,
        f"done — grade {payload.get('projected_grade', '?')} "
        f"(score {payload.get('projected_score', 0):.1f})",
    )


def _derive_tags(forensic: dict[str, Any], analysis: dict[str, Any]) -> list[str]:
    """Cheap heuristic tags surfaced alongside the strengths/weaknesses
    list — used to prime the user-edit UI."""
    tags: list[str] = []
    mined = forensic.get("mined_priors") or {}
    cuts_on_beat = mined.get("cuts_on_beat_pct")
    if cuts_on_beat is not None:
        if cuts_on_beat >= 0.6:
            tags.append("beat-synced")
        elif cuts_on_beat <= 0.2:
            tags.append("off-beat")
    if mined.get("lyric_sync_pct", 0) >= 0.3:
        tags.append("lyric-sync")
    if (forensic.get("audio_layers") or {}).get("dialogue_present"):
        tags.append("dialogue-heavy")
    if mined.get("library_sfx_per_min", 0) >= 2.0:
        tags.append("sfx-layered")
    dur = analysis.get("duration_sec", 0)
    if dur >= 240:
        tags.append("long-form")
    elif dur <= 90:
        tags.append("short-form")
    if analysis.get("shot_count", 0) / max(dur / 60, 0.1) >= 100:
        tags.append("rapid-cuts")
    return tags


def _now() -> float:
    import time
    return time.time()
