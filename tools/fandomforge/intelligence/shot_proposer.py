"""Auto-draft a shot list from edit-plan + beat-map + source catalog.

This is a heuristic first-draft proposer — not an optimizer. The idea is:
read the rhythm (beat-map), read the structure (edit-plan acts), read what
source clips are available (catalog), and propose a shot-list where:

  - Drops get hero shots (action/hero role)
  - Downbeats get cut points between hero-shot clusters
  - Buildup sections get establishing/reaction shots
  - Each act's fandom quota is honored (cycled through fandoms in order)
  - Every shot is schema-valid

The output is always reviewable. The user's expected workflow is:
    ff propose shots --project X  →  review the JSON  →  edit if needed  →  run pipeline
OR via web: click "Draft a shot list" → get a JSON Patch in the chat thread
     → accept/reject per-op → apply.

If the catalog is empty (fresh project with no ingested sources), the proposer
emits placeholder source_ids like "PLACEHOLDER_A" so the downstream QA gate
flags them and the user can substitute real clips.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SHOT_ROLES_ORDER = [
    "establishing", "action", "hero", "reaction",
    "detail", "motion", "cut-on-action", "environment",
]

FRAMING_BY_ROLE = {
    "establishing": "wide",
    "action": "medium",
    "hero": "CU",
    "reaction": "CU",
    "detail": "insert",
    "motion": "MS",
    "cut-on-action": "medium",
    "environment": "WS",
    "gaze": "MCU",
    "insert": "insert",
    "title": "",
    "transition": "",
}


@dataclass
class ProposerConfig:
    fps: float = 24.0
    width: int = 1920
    height: int = 1080
    min_shot_frames: int = 12  # 0.5s at 24fps
    max_shot_frames: int = 144  # 6s at 24fps
    hero_shot_frames: int = 48  # 2s at 24fps on drops
    random_seed: int = 42  # deterministic output


@dataclass
class ProposerInputs:
    project_slug: str
    edit_plan: dict[str, Any]
    beat_map: dict[str, Any]
    catalog: list[dict[str, Any]]
    config: ProposerConfig


def _sec_to_frames(sec: float, fps: float) -> int:
    return int(round(sec * fps))


def _fmt_timecode(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:d}:{m:02d}:{s:06.3f}"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def load_inputs(project_slug: str, *, project_root: Path | None = None) -> ProposerInputs:
    """Load edit-plan, beat-map, and catalog for a project."""
    root = project_root or Path.cwd()
    project_dir = root / "projects" / project_slug
    data_dir = project_dir / "data"

    edit_plan = _load_json(data_dir / "edit-plan.json") or {}
    beat_map = _load_json(data_dir / "beat-map.json") or {}

    # Catalog may be at either `catalog.json` (older projects) or
    # `source-catalog.json` (what `ff ingest` actually writes). Prefer the
    # source-catalog because that's the canonical post-ingest output.
    catalog_clips: list[dict[str, Any]] = []
    source_catalog = _load_json(data_dir / "source-catalog.json")
    if source_catalog and isinstance(source_catalog, dict):
        for s in source_catalog.get("sources") or []:
            # Use the file stem as source_id so downstream resolvers (which glob
            # raw/<source_id>.*) can find the actual file. The hash-based id in
            # the catalog isn't usable as a filename match.
            file_stem = None
            if s.get("path"):
                from pathlib import Path as _P
                file_stem = _P(s["path"]).stem
            source_id = file_stem or s.get("id")
            catalog_clips.append({
                "id": source_id,
                "source_id": source_id,
                "duration_sec": (s.get("media") or {}).get("duration_sec"),
            })
    if not catalog_clips:
        catalog_data = _load_json(data_dir / "catalog.json") or {}
        if isinstance(catalog_data, dict):
            catalog_clips = catalog_data.get("clips") or []
        elif isinstance(catalog_data, list):
            catalog_clips = catalog_data

    config = ProposerConfig()
    return ProposerInputs(
        project_slug=project_slug,
        edit_plan=edit_plan,
        beat_map=beat_map,
        catalog=catalog_clips,
        config=config,
    )


def _pick_source(
    catalog: list[dict[str, Any]],
    fandoms: list[str],
    rng: random.Random,
    fallback_index: int,
) -> tuple[str, float]:
    """Pick a source id and a time offset within that source."""
    if catalog:
        clip = rng.choice(catalog)
        source_id = clip.get("id") or clip.get("source_id") or f"catalog_{fallback_index}"
        duration = float(clip.get("duration_sec") or clip.get("duration") or 60.0)
        offset = rng.uniform(1.0, max(2.0, duration - 5.0))
        return source_id, offset
    fandom = fandoms[fallback_index % max(1, len(fandoms))] if fandoms else "placeholder"
    safe = "".join(c if c.isalnum() else "_" for c in fandom).upper() or "SOURCE"
    return f"PLACEHOLDER_{safe}_{fallback_index}", float(fallback_index) * 3.0 + 1.0


def _collect_sync_points(beat_map: dict[str, Any]) -> list[tuple[float, str]]:
    """Emit (time_sec, kind) tuples to sync shots to. Drops first, then downbeats."""
    points: list[tuple[float, str]] = []
    for drop in beat_map.get("drops", []) or []:
        t = drop.get("time")
        if isinstance(t, (int, float)):
            points.append((float(t), "drop"))
    for i, t in enumerate(beat_map.get("downbeats", []) or []):
        if isinstance(t, (int, float)):
            points.append((float(t), "downbeat"))
    points.sort(key=lambda p: p[0])
    return points


def _assign_act(time_sec: float, acts: list[dict[str, Any]], fallback_count: int = 3) -> int:
    """Assign a shot's time to an act index (1-based)."""
    if acts:
        for idx, act in enumerate(acts, start=1):
            end = act.get("end_sec") or act.get("duration_sec") or 0
            if time_sec <= float(end):
                return idx
        return len(acts)
    return min(fallback_count, max(1, int(time_sec // 30) + 1))


def propose_shot_list(inputs: ProposerInputs) -> dict[str, Any]:
    """Produce a schema-valid shot-list JSON from the inputs."""
    cfg = inputs.config
    rng = random.Random(cfg.random_seed)

    fandoms = inputs.edit_plan.get("fandoms") or []
    if isinstance(fandoms, list) and fandoms and isinstance(fandoms[0], dict):
        fandom_names = [f.get("name", f"Fandom{i}") for i, f in enumerate(fandoms)]
    elif isinstance(fandoms, list):
        fandom_names = [str(f) for f in fandoms]
    else:
        fandom_names = []

    acts = inputs.edit_plan.get("acts") or []
    song_duration = float(
        inputs.beat_map.get("duration_sec")
        or inputs.edit_plan.get("song", {}).get("duration_sec")
        or 60.0
    )

    sync_points = _collect_sync_points(inputs.beat_map)
    if not sync_points:
        # No beat-map? Fall back to a 4s grid.
        sync_points = [
            (float(t), "free") for t in range(0, int(song_duration), 4)
        ]

    shots: list[dict[str, Any]] = []
    fallback_i = 0

    for i, (time_sec, kind) in enumerate(sync_points):
        if time_sec >= song_duration:
            break

        # Decide duration: drops → hero, downbeats → medium, others → short
        if kind == "drop":
            duration_frames = cfg.hero_shot_frames
            role = "hero"
        elif kind == "downbeat":
            duration_frames = cfg.hero_shot_frames // 2
            role = SHOT_ROLES_ORDER[i % len(SHOT_ROLES_ORDER)]
        else:
            duration_frames = cfg.min_shot_frames
            role = "insert"

        # Clamp duration to song bounds
        start_frame = _sec_to_frames(time_sec, cfg.fps)
        end_sec = min(song_duration, time_sec + duration_frames / cfg.fps)
        duration_frames = max(cfg.min_shot_frames, _sec_to_frames(end_sec - time_sec, cfg.fps))

        source_id, offset = _pick_source(inputs.catalog, fandom_names, rng, fallback_i)
        fallback_i += 1

        act = _assign_act(time_sec, acts, fallback_count=max(1, len(acts) or 3))

        shot = {
            "id": f"s{i+1:03d}",
            "act": act,
            "start_frame": start_frame,
            "duration_frames": duration_frames,
            "source_id": source_id,
            "source_timecode": _fmt_timecode(offset),
            "role": role,
            "mood_tags": [],
            "framing": FRAMING_BY_ROLE.get(role, ""),
            "motion_vector": None,
            "eyeline": "",
            "beat_sync": {
                "type": kind if kind in ("beat", "downbeat", "drop", "buildup", "breakdown", "onset", "free") else "free",
                "index": i,
                "time_sec": time_sec,
            },
            "scores": {
                "theme_fit": 3.0,
                "fandom_balance": 3.0,
                "emotion": 3.0,
                "beat_sync_score": 4.5 if kind == "drop" else 4.0 if kind == "downbeat" else 3.0,
            },
        }
        shots.append(shot)

    # Dedupe any overlapping shots and sort by start_frame
    shots.sort(key=lambda s: s["start_frame"])
    pruned: list[dict[str, Any]] = []
    last_end = -1
    for s in shots:
        if s["start_frame"] >= last_end:
            pruned.append(s)
            last_end = s["start_frame"] + s["duration_frames"]

    result = {
        "schema_version": 1,
        "project_slug": inputs.project_slug,
        "fps": cfg.fps,
        "resolution": {"width": cfg.width, "height": cfg.height},
        "song_duration_sec": song_duration,
        "shots": pruned,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "shot_proposer/heuristic-v1",
    }
    if fandom_names and acts:
        quota = {}
        for act_idx in range(1, len(acts) + 1):
            per_fandom_share = round(1.0 / len(fandom_names), 4) if fandom_names else 1.0
            quota[f"act{act_idx}"] = {name: per_fandom_share for name in fandom_names}
        result["fandom_quota"] = quota
    return result


def propose_for_project(project_slug: str, *, project_root: Path | None = None) -> dict[str, Any]:
    """Convenience: load inputs and propose in one call."""
    inputs = load_inputs(project_slug, project_root=project_root)
    return propose_shot_list(inputs)


__all__ = [
    "ProposerConfig",
    "ProposerInputs",
    "load_inputs",
    "propose_shot_list",
    "propose_for_project",
]
