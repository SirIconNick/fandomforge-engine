"""Infer a per-shot emotion vector across an edit.

V1 is heuristic: uses existing `mood_tags` and `role` on each shot plus a
tag → emotion lookup table. Future versions can swap in CLIP-prompt
similarity or transcript sentiment without changing the output schema.

The output is a series of samples, one per shot, where each sample's `vector`
is aligned to a shared `dimensions` list. QA gate uses this to detect
"dead zones" (flat intensity for >N seconds), and the web BeatMapVisualizer
overlays the intensity curve onto the energy curve.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DIMENSIONS = [
    "grief",
    "triumph",
    "fear",
    "awe",
    "tension",
    "release",
    "sorrow",
    "elation",
]

# Role heuristic — what emotion does each shot role typically carry?
ROLE_EMOTION: dict[str, dict[str, float]] = {
    "hero": {"triumph": 0.7, "awe": 0.4, "elation": 0.5},
    "action": {"tension": 0.6, "fear": 0.3, "triumph": 0.3},
    "reaction": {"grief": 0.3, "sorrow": 0.3, "fear": 0.3},
    "detail": {"awe": 0.4, "tension": 0.3},
    "motion": {"tension": 0.5, "release": 0.3},
    "cut-on-action": {"tension": 0.5},
    "environment": {"awe": 0.6},
    "establishing": {"awe": 0.5, "tension": 0.2},
    "gaze": {"grief": 0.4, "sorrow": 0.4},
    "insert": {"tension": 0.3},
    "title": {"awe": 0.3},
    "transition": {"release": 0.5},
}

# Mood-tag → emotion boosts.
TAG_EMOTION: dict[str, dict[str, float]] = {
    "grief": {"grief": 1.0, "sorrow": 0.8},
    "loss": {"grief": 0.9, "sorrow": 0.7},
    "mentor-loss": {"grief": 0.8, "sorrow": 0.7},
    "sacrifice": {"sorrow": 0.7, "triumph": 0.4},
    "triumph": {"triumph": 1.0, "elation": 0.7},
    "victory": {"triumph": 0.9, "elation": 0.6},
    "fear": {"fear": 1.0, "tension": 0.7},
    "horror": {"fear": 0.9, "tension": 0.6},
    "tension": {"tension": 0.9},
    "chase": {"tension": 0.8, "fear": 0.5},
    "awe": {"awe": 1.0},
    "wonder": {"awe": 0.8, "elation": 0.4},
    "release": {"release": 1.0, "elation": 0.5},
    "catharsis": {"release": 0.8, "sorrow": 0.4},
    "joy": {"elation": 1.0, "triumph": 0.5},
    "love": {"elation": 0.7, "awe": 0.4},
    "rage": {"tension": 0.8, "fear": 0.3},
}


def _zero_vector() -> list[float]:
    return [0.0] * len(DIMENSIONS)


def _set_dim(vec: list[float], dim: str, value: float) -> None:
    try:
        i = DIMENSIONS.index(dim)
        vec[i] = max(vec[i], min(1.0, value))
    except ValueError:
        pass


def _shot_to_vector(shot: dict[str, Any]) -> tuple[list[float], str | None]:
    vec = _zero_vector()

    role = shot.get("role")
    if isinstance(role, str):
        for dim, boost in ROLE_EMOTION.get(role, {}).items():
            _set_dim(vec, dim, boost)

    for tag in shot.get("mood_tags") or []:
        tag_str = str(tag).lower()
        for dim, boost in TAG_EMOTION.get(tag_str, {}).items():
            _set_dim(vec, dim, boost)

    scores = shot.get("scores") or {}
    emotion_score = scores.get("emotion")
    if isinstance(emotion_score, (int, float)):
        scale = float(emotion_score) / 5.0
        for i in range(len(vec)):
            vec[i] *= max(0.1, min(1.0, scale))

    dominant = None
    peak = 0.0
    for i, v in enumerate(vec):
        if v > peak:
            peak = v
            dominant = DIMENSIONS[i]
    return vec, dominant


def _intensity(vec: list[float]) -> float:
    if not vec:
        return 0.0
    return sum(vec) / len(vec) + max(vec) * 0.5


def _time_from_shot(shot: dict[str, Any], fps: float) -> float:
    start_frame = shot.get("start_frame")
    if isinstance(start_frame, (int, float)):
        return float(start_frame) / fps
    beat_sync = shot.get("beat_sync") or {}
    t = beat_sync.get("time_sec")
    return float(t) if isinstance(t, (int, float)) else 0.0


def infer_arc(shot_list: dict[str, Any]) -> dict[str, Any]:
    """Infer an emotion-arc artifact from a shot-list dict."""
    fps = float(shot_list.get("fps") or 24)
    shots = shot_list.get("shots") or []

    samples: list[dict[str, Any]] = []
    for shot in shots:
        vec, dominant = _shot_to_vector(shot)
        intensity = min(1.0, _intensity(vec))
        sample: dict[str, Any] = {
            "shot_id": shot.get("id") or f"shot_{len(samples)}",
            "time_sec": _time_from_shot(shot, fps),
            "vector": vec,
            "intensity": intensity,
        }
        if dominant:
            sample["dominant"] = dominant
            sample["confidence"] = max(0.2, min(1.0, max(vec) if vec else 0.2))
        samples.append(sample)

    return {
        "schema_version": 1,
        "project_slug": shot_list.get("project_slug") or "unknown",
        "dimensions": list(DIMENSIONS),
        "samples": samples,
        "method": "heuristic_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "emotion_arc/heuristic-v1",
    }


def infer_for_project(project_slug: str, *, project_root: Path | None = None) -> dict[str, Any]:
    root = project_root or Path.cwd()
    shot_list_path = root / "projects" / project_slug / "data" / "shot-list.json"
    if not shot_list_path.exists():
        raise FileNotFoundError(
            f"{shot_list_path} not found. Run `ff propose shots --project {project_slug}` first."
        )
    shot_list = json.loads(shot_list_path.read_text())
    return infer_arc(shot_list)


def detect_dead_zones(arc: dict[str, Any], *, min_gap_sec: float = 20.0, flat_tolerance: float = 0.05) -> list[tuple[float, float]]:
    """Return (start_sec, end_sec) ranges where intensity barely changes for > min_gap_sec."""
    samples = arc.get("samples") or []
    if len(samples) < 2:
        return []
    dead: list[tuple[float, float]] = []
    run_start: float | None = None
    run_intensity: float | None = None
    last_time: float = 0.0
    for s in samples:
        t = float(s.get("time_sec", 0))
        intensity = float(s.get("intensity", 0))
        if run_intensity is None:
            run_start = t
            run_intensity = intensity
            last_time = t
            continue
        if abs(intensity - run_intensity) <= flat_tolerance:
            last_time = t
            continue
        if run_start is not None and (last_time - run_start) >= min_gap_sec:
            dead.append((run_start, last_time))
        run_start = t
        run_intensity = intensity
        last_time = t
    if run_start is not None and (last_time - run_start) >= min_gap_sec:
        dead.append((run_start, last_time))
    return dead


__all__ = [
    "DIMENSIONS",
    "infer_arc",
    "infer_for_project",
    "detect_dead_zones",
]
