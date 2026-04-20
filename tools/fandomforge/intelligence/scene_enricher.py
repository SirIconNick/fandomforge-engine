"""Enrich scene boundary JSON with per-scene avg_luma (and future motion_dir).

Raw `ff ingest` writes scenes.json with only boundary data (index, start_sec,
end_sec, start_frame, end_frame). The shot picker needs avg_luma to reject
black-frame scenes. This module samples N frames per scene via ffmpeg,
averages their luma, and writes the augmented data back in place.

Idempotent: scenes already carrying avg_luma are skipped (unless force=True).
Safe to re-run after autopilot or after adding new sources to a project.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from fandomforge.intelligence.reference_analyzer_deep import (
    _ffmpeg_available,
    _frame_stats,
    _sample_frame,
)


_SAMPLES_PER_SCENE = 3
"""How many frames to sample per scene. 3 covers start/middle/end — enough
for a stable avg_luma estimate without a per-scene ffmpeg call storm."""

_MOTION_DIR_CHANGE_FLOOR = 3.0
"""Minimum per-pixel average diff for a scene to be tagged with a direction.
Below this the scene gets motion_dir='static'. Computed after normalizing
grayscale frames to [0, 255]."""

_MOTION_DIR_MAGNITUDE_FLOOR = 0.05
"""Minimum normalized centroid shift (fraction of frame width/height) for
a direction to count. Below this the scene is 'mixed' — change is there,
but not concentrated along one axis."""


def _compute_motion_dir(
    video: Path,
    start: float,
    end: float,
    tmp_dir: Path,
    scene_idx: int,
) -> str | None:
    """Estimate scene's dominant motion direction from two frame samples.

    Computes per-pixel difference between an early frame and a late frame,
    then the centroid of pixels that gained brightness minus the centroid
    of pixels that lost brightness. Sign of that vector → direction.

    Returns one of: 'left', 'right', 'up', 'down', 'static', 'mixed'.
    None if ffmpeg/PIL fails — caller treats None as unknown.
    """
    try:
        from PIL import Image  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return None
    if end <= start:
        return None
    t1 = start + 0.2 * (end - start)
    t2 = start + 0.6 * (end - start)
    p1 = tmp_dir / f"mdir_{scene_idx:04d}_a.jpg"
    p2 = tmp_dir / f"mdir_{scene_idx:04d}_b.jpg"
    if not _sample_frame(video, t1, p1):
        return None
    if not _sample_frame(video, t2, p2):
        return None
    try:
        arr1 = np.asarray(Image.open(p1).convert("L")).astype("float32")
        arr2 = np.asarray(Image.open(p2).convert("L")).astype("float32")
    except Exception:  # noqa: BLE001
        return None
    if arr1.shape != arr2.shape or arr1.size == 0:
        return None
    diff = arr2 - arr1
    gained = np.maximum(diff, 0.0)
    lost = np.maximum(-diff, 0.0)
    if gained.sum() < 1.0 or lost.sum() < 1.0:
        return "static"
    avg_change = float(np.abs(diff).mean())
    if avg_change < _MOTION_DIR_CHANGE_FLOOR:
        return "static"

    h, w = diff.shape
    ys = np.arange(h, dtype="float32").reshape(h, 1)
    xs = np.arange(w, dtype="float32").reshape(1, w)

    def _centroid(mass):  # type: ignore[no-untyped-def]
        total = float(mass.sum())
        if total <= 0:
            return None
        cy = float((mass * ys).sum()) / total
        cx = float((mass * xs).sum()) / total
        return cy, cx

    g_cent = _centroid(gained)
    l_cent = _centroid(lost)
    if g_cent is None or l_cent is None:
        return "static"
    dy = (g_cent[0] - l_cent[0]) / max(h, 1)
    dx = (g_cent[1] - l_cent[1]) / max(w, 1)
    # If neither axis has meaningful shift → mixed.
    if abs(dx) < _MOTION_DIR_MAGNITUDE_FLOOR and abs(dy) < _MOTION_DIR_MAGNITUDE_FLOOR:
        return "mixed"
    if abs(dx) >= abs(dy):
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"


def enrich_scenes(
    scenes_json_path: Path,
    video_path: Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Read a scenes.json file, add avg_luma + peak_luma to every scene
    lacking them, write the file back. Returns a stats dict.

    When every scene already carries avg_luma and force=False, short-circuits
    without touching ffmpeg. That keeps autopilot fast on idempotent re-runs.
    """
    if not scenes_json_path.exists():
        return {
            "ok": False,
            "reason": "scenes_json_not_found",
            "path": str(scenes_json_path),
        }
    if not video_path.exists():
        return {
            "ok": False,
            "reason": "video_not_found",
            "path": str(video_path),
        }
    if not _ffmpeg_available():
        return {"ok": False, "reason": "ffmpeg_unavailable"}

    try:
        data = json.loads(scenes_json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"ok": False, "reason": "invalid_json", "error": str(exc)}
    scenes = data.get("scenes") or []
    if not scenes:
        return {"ok": False, "reason": "no_scenes_in_file"}

    if not force and all(
        isinstance(s.get("avg_luma"), (int, float))
        and isinstance(s.get("motion_dir"), str)
        for s in scenes
    ):
        return {
            "ok": True,
            "skipped": True,
            "scenes": len(scenes),
            "reason": "already_enriched",
        }

    enriched = 0
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        for i, sc in enumerate(scenes):
            if not force and isinstance(sc.get("avg_luma"), (int, float)):
                continue
            start = float(sc.get("start_sec") or 0.0)
            end = float(sc.get("end_sec") or 0.0)
            if end <= start:
                failed += 1
                continue
            # Evenly spaced samples inside [start, end]. +1/N+1 keeps them
            # off the boundaries so we avoid picking a fade-to-black split.
            sample_times = [
                start + (end - start) * (k + 1) / (_SAMPLES_PER_SCENE + 1)
                for k in range(_SAMPLES_PER_SCENE)
            ]
            lumas: list[float] = []
            for k, t in enumerate(sample_times):
                frame_path = tmp_p / f"s{i:04d}_f{k}.jpg"
                if not _sample_frame(video_path, t, frame_path):
                    continue
                stats = _frame_stats(frame_path)
                if stats is None:
                    continue
                lumas.append(stats["luma"])
            if not lumas:
                failed += 1
                continue
            sc["avg_luma"] = round(sum(lumas) / len(lumas), 4)
            sc["peak_luma"] = round(max(lumas), 4)

            # Motion direction. Only (re)compute when missing or force=True.
            # Best-effort — None means we couldn't infer it, so the picker
            # downstream just skips the motion-continuity penalty for this
            # scene. Don't mark the scene as failed if motion fails but luma
            # succeeded; half a loaf is still progress.
            if force or not isinstance(sc.get("motion_dir"), str):
                mdir = _compute_motion_dir(video_path, start, end, tmp_p, i)
                if mdir is not None:
                    sc["motion_dir"] = mdir
            enriched += 1

    scenes_json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "skipped": False,
        "scenes": len(scenes),
        "enriched": enriched,
        "failed": failed,
    }


def enrich_project(project_dir: Path, *, force: bool = False) -> dict[str, Any]:
    """Enrich every scenes.json referenced by a project's source-catalog.

    Scene files can live in one of two places: derived/<b3-id>/scenes.json
    (modern ff ingest) or data/scenes/<stem>.json (legacy / manual). Try
    the derived path first, fall back to data/scenes.
    """
    catalog_path = project_dir / "data" / "source-catalog.json"
    if not catalog_path.exists():
        return {
            "ok": False,
            "reason": "catalog_not_found",
            "path": str(catalog_path),
        }
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"ok": False, "reason": "invalid_catalog", "error": str(exc)}

    results: list[dict[str, Any]] = []
    for entry in catalog.get("sources") or []:
        path = entry.get("path") or ""
        if not path:
            continue
        video = Path(path)
        if not video.is_absolute():
            video = project_dir / path
        stem = Path(path).stem
        candidates = [
            project_dir / "derived" / str(entry.get("id") or "") / "scenes.json",
            project_dir / "data" / "scenes" / f"{stem}.json",
        ]
        scene_file = next((c for c in candidates if c.exists()), None)
        if scene_file is None:
            results.append({
                "source": stem,
                "ok": False,
                "reason": "no_scenes_file",
            })
            continue
        result = enrich_scenes(scene_file, video, force=force)
        result["source"] = stem
        result["scene_file"] = str(scene_file)
        results.append(result)

    return {
        "ok": True,
        "sources_total": len(results),
        "enriched": sum(
            1 for r in results if r.get("ok") and not r.get("skipped")
        ),
        "skipped": sum(1 for r in results if r.get("skipped")),
        "failed": sum(1 for r in results if not r.get("ok")),
        "details": results,
    }


__all__ = ["enrich_scenes", "enrich_project"]
