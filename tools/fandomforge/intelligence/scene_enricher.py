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
        isinstance(s.get("avg_luma"), (int, float)) for s in scenes
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
