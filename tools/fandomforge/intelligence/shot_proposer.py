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
    # Dedupe: never pick (source_id, offset) that's within `dedupe_window_sec`
    # of an earlier shot's (source_id, offset). Tolerance widens through
    # `dedupe_window_fallbacks` if the candidate pool is exhausted.
    dedupe: bool = True
    dedupe_window_sec: float = 0.1  # 100ms — "basically the same shot"
    dedupe_window_fallbacks: tuple[float, ...] = (0.5, 2.0)  # widen if stuck
    dedupe_max_retries_per_shot: int = 32


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
                "path": s.get("path"),
                "duration_sec": (s.get("media") or {}).get("duration_sec"),
                # Propagate derived-artifact paths so scene-aware offset
                # picking can find the scenes.json for this source.
                "derived": s.get("derived") or {},
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


def _sample_frame_luma(video_path: str, offset_sec: float) -> float | None:
    """Probe the mean luma (0-255) of a single frame at `offset_sec`.

    Returns None if ffmpeg is unavailable or the probe fails. Used to reject
    offsets that land on cut-to-black / fade-through moments even within a
    detected scene — action trailers have plenty of intrinsically dark frames
    mid-scene.
    """
    import shutil as _sh
    import subprocess as _sp
    if _sh.which("ffprobe") is None:
        return None
    try:
        proc = _sp.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-read_intervals", f"{max(0.0, offset_sec):.3f}%+#1",
                "-show_entries", "frame=pkt_dts_time",
                "-show_frames", "-of", "json",
                "-f", "lavfi",
                f"movie={video_path},signalstats",
            ],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (_sp.TimeoutExpired, FileNotFoundError, OSError):
        return None
    # Simpler path: use ffmpeg signalstats in a one-frame pull.
    try:
        proc = _sp.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner",
                "-ss", f"{max(0.0, offset_sec):.3f}",
                "-i", video_path,
                "-frames:v", "1",
                "-vf", "signalstats,metadata=print",
                "-an", "-f", "null", "-",
            ],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (_sp.TimeoutExpired, FileNotFoundError, OSError):
        return None
    # signalstats prints `lavfi.signalstats.YAVG=123.45` in stderr
    stderr = proc.stderr or ""
    for line in stderr.splitlines():
        if "YAVG=" in line:
            try:
                return float(line.split("YAVG=")[1].strip())
            except (ValueError, IndexError):
                continue
    return None


def _clip_video_path(clip: dict[str, Any]) -> str | None:
    """Find a playable path for the clip if available (for luma sampling)."""
    # Catalog entries may store path directly or via derived['path'] / source-catalog.
    return clip.get("path") or (clip.get("derived") or {}).get("path")


def _load_scenes_for_clip(clip: dict[str, Any]) -> list[tuple[float, float]]:
    """Read scene boundaries for a source if its catalog entry points at a
    scenes.json artifact. Returns a list of (start, end) tuples; empty if
    the file is missing or malformed."""
    derived = clip.get("derived") or {}
    scenes_path = derived.get("scenes")
    if not scenes_path:
        return []
    try:
        from pathlib import Path as _P
        data = json.loads(_P(scenes_path).read_text())
    except (OSError, json.JSONDecodeError):
        return []
    out: list[tuple[float, float]] = []
    for s in data.get("scenes", []) or []:
        try:
            start = float(s["start_sec"])
            end = float(s["end_sec"])
            if end > start:
                out.append((start, end))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def _pick_offset_in_scene(
    scenes: list[tuple[float, float]],
    duration: float,
    rng: random.Random,
    padding_sec: float = 0.8,
    min_scene_sec: float = 3.0,
    shot_span_sec: float = 2.0,
) -> float:
    """Pick an offset inside a long-enough scene such that the full 2-second
    shot window fits between the scene boundaries with padding — so the shot
    never crosses a cut-to-black transition. Falls back to a uniform random
    offset if no scene qualifies."""
    # The shot spans [offset, offset + shot_span_sec]. To keep it fully inside
    # a scene with padding on both sides, the scene must be at least
    # (shot_span_sec + 2*padding_sec) long.
    min_needed = shot_span_sec + 2 * padding_sec
    eligible = [
        (start, end) for (start, end) in scenes
        if (end - start) >= max(min_scene_sec, min_needed)
        and end <= duration - padding_sec
    ]
    if not eligible:
        # Long tail fallback: pick the longest available scene even if it
        # doesn't satisfy min_scene_sec, still respecting boundaries.
        long_scenes = [
            (s, e) for (s, e) in scenes if (e - s) >= min_needed
        ]
        if long_scenes:
            start, end = rng.choice(long_scenes)
            lo = start + padding_sec
            hi = max(lo + 0.1, end - padding_sec - shot_span_sec)
            return rng.uniform(lo, hi)
        return rng.uniform(1.0, max(2.0, duration - 5.0))
    start, end = rng.choice(eligible)
    lo = start + padding_sec
    # Ensure offset + shot_span fits inside the scene.
    hi = end - padding_sec - shot_span_sec
    if hi <= lo:
        hi = lo + 0.1
    return rng.uniform(lo, hi)


MIN_LUMA_YAVG = 56.0  # Out of 255 (~0.22 normalized). Frames below this read as "too dark" in final render.
LUMA_PROBE_MAX_RETRIES = 12
LUMA_SOURCE_SWAP_MAX_RETRIES = 4  # try up to 4 different catalog clips before accepting a dim window
LUMA_SHOT_SPAN_SEC = 2.0  # length of the shot we're trying to place


def _shot_span_is_bright(
    video_path: str,
    offset_sec: float,
    shot_span: float = LUMA_SHOT_SPAN_SEC,
) -> bool:
    """Probe three frames across the shot's timespan (start, middle, end).
    Reject if ANY of them is below MIN_LUMA_YAVG — catches shots that fade
    through dark even though they open bright."""
    samples = [offset_sec, offset_sec + shot_span / 2, offset_sec + shot_span - 0.05]
    readings: list[float] = []
    for t in samples:
        luma = _sample_frame_luma(video_path, t)
        if luma is None:
            # probe unavailable; don't reject based on silence
            return True
        readings.append(luma)
    # All three frames must clear the threshold.
    return min(readings) >= MIN_LUMA_YAVG


def _pick_source(
    catalog: list[dict[str, Any]],
    fandoms: list[str],
    rng: random.Random,
    fallback_index: int,
) -> tuple[str, float]:
    """Pick a source id and a time offset within that source. No dedupe.

    Multi-layer darkness defense:
      1. Prefer offsets inside a detected scene with padding on both sides
         (avoid fade boundaries).
      2. Probe the proposed 2-second window's start / middle / end frames via
         ffmpeg signalstats. If any is below MIN_LUMA_YAVG, pick a new offset.
      3. Retry up to LUMA_PROBE_MAX_RETRIES offsets within the picked clip.
      4. If a clip's offset pool is exhausted, swap to a different catalog
         clip (up to LUMA_SOURCE_SWAP_MAX_RETRIES tries). Swapping sources is
         preferred over swapping tiers — preserves intent-match.
      5. If every attempt exhausts, return the last (dim) candidate so the
         caller still gets a placement rather than nothing.
    """
    if catalog:
        last_attempt: tuple[str, float] | None = None
        for source_attempt in range(LUMA_SOURCE_SWAP_MAX_RETRIES):
            clip = rng.choice(catalog)
            source_id = clip.get("id") or clip.get("source_id") or f"catalog_{fallback_index}"
            duration = float(clip.get("duration_sec") or clip.get("duration") or 60.0)
            scenes = _load_scenes_for_clip(clip)
            video_path = _clip_video_path(clip)

            def _fresh_offset() -> float:
                if scenes:
                    return _pick_offset_in_scene(scenes, duration, rng)
                return rng.uniform(1.0, max(2.0, duration - 5.0))

            offset = _fresh_offset()
            if not video_path:
                # No video probe available — accept the first clip immediately.
                return source_id, offset

            bright_found = False
            for _ in range(LUMA_PROBE_MAX_RETRIES):
                if _shot_span_is_bright(video_path, offset):
                    bright_found = True
                    break
                offset = _fresh_offset()

            if bright_found:
                return source_id, offset
            last_attempt = (source_id, offset)
            # otherwise loop and try a different clip from the catalog

        # Every catalog swap exhausted — use the most recent attempt.
        if last_attempt is not None:
            return last_attempt
        # Defensive fallback (catalog became empty mid-loop somehow).
    fandom = fandoms[fallback_index % max(1, len(fandoms))] if fandoms else "placeholder"
    safe = "".join(c if c.isalnum() else "_" for c in fandom).upper() or "SOURCE"
    return f"PLACEHOLDER_{safe}_{fallback_index}", float(fallback_index) * 3.0 + 1.0


def _collision(
    candidate: tuple[str, float],
    used: set[tuple[str, float]],
    window_sec: float,
) -> bool:
    """True if `candidate` is within `window_sec` of any (source, offset) in `used`."""
    src, offset = candidate
    window_ticks = max(1, int(round(window_sec * 10)))  # offsets rounded to 0.1s
    rounded = round(offset, 1)
    for tick in range(-window_ticks, window_ticks + 1):
        if (src, round(rounded + tick * 0.1, 1)) in used:
            return True
    return False


def _pick_unique_source(
    catalog: list[dict[str, Any]],
    fandoms: list[str],
    rng: random.Random,
    fallback_index: int,
    used: set[tuple[str, float]],
    window_sec: float,
    max_retries: int,
    fallback_windows: tuple[float, ...],
) -> tuple[tuple[str, float], str | None]:
    """Pick a source that doesn't collide with `used`. Returns ((src, offset), warning_or_None)."""
    # Try at the tight window first.
    for window in (window_sec, *fallback_windows):
        for _ in range(max_retries):
            candidate = _pick_source(catalog, fandoms, rng, fallback_index)
            if not _collision(candidate, used, window):
                warning = None if window == window_sec else (
                    f"reuse-dedupe tolerance widened to {window}s for shot #{fallback_index + 1}"
                )
                return candidate, warning
    # Exhausted every attempt — drop the constraint and let reuse happen.
    return (
        _pick_source(catalog, fandoms, rng, fallback_index),
        f"reuse-dedupe dropped for shot #{fallback_index + 1} (catalog exhausted)",
    )


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
    # Shots we've already emitted — (source_id, rounded_offset). Used for dedup.
    used_sources: set[tuple[str, float]] = set()
    # id → (source_id, offset) for callback resolution.
    shot_index: dict[str, tuple[str, float]] = {}
    # Map edit-plan-declared callbacks: shot_id -> callback_of (prior shot id)
    plan_callbacks = _collect_planned_callbacks(inputs.edit_plan)
    warnings: list[str] = []

    # Phase 2.1+2.2 wire-in: when the edit-plan's acts[] include the new
    # `pacing` field (slow/medium/fast/frantic), the proposer uses each
    # act's band to set shot duration instead of the legacy fixed-by-role
    # defaults. Drops still get hero treatment but their duration anchors
    # to the act's pacing band rather than a hardcoded 2-second budget.
    from fandomforge.intelligence.arc_architect import shot_duration_band

    def _act_pacing_at(t: float) -> str | None:
        for a in acts:
            if float(a.get("start_sec", 0)) <= t < float(a.get("end_sec", 0)):
                return a.get("pacing")
        return None

    for i, (time_sec, kind) in enumerate(sync_points):
        if time_sec >= song_duration:
            break

        pacing = _act_pacing_at(time_sec)

        # Decide duration: drops → hero (longer end of band),
        # downbeats → middle of band, others → short end of band.
        if pacing:
            lo_sec, hi_sec = shot_duration_band(pacing)
            if kind == "drop":
                target_sec = hi_sec  # hero shots take the long end of pacing band
                role = "hero"
            elif kind == "downbeat":
                target_sec = (lo_sec + hi_sec) / 2
                role = SHOT_ROLES_ORDER[i % len(SHOT_ROLES_ORDER)]
            else:
                target_sec = lo_sec
                role = "insert"
            duration_frames = max(cfg.min_shot_frames, _sec_to_frames(target_sec, cfg.fps))
        else:
            # Legacy path — kept identical to v1 behavior so old test
            # snapshots still hold.
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

        shot_id = f"s{i+1:03d}"
        intent = None
        callback_of = plan_callbacks.get(shot_id)

        if callback_of and callback_of in shot_index:
            # Intentional reuse — mirror the callback target exactly.
            source_id, offset = shot_index[callback_of]
            intent = "callback"
        elif cfg.dedupe and inputs.catalog:
            (source_id, offset), warn = _pick_unique_source(
                inputs.catalog, fandom_names, rng, fallback_i,
                used_sources,
                window_sec=cfg.dedupe_window_sec,
                max_retries=cfg.dedupe_max_retries_per_shot,
                fallback_windows=cfg.dedupe_window_fallbacks,
            )
            if warn:
                warnings.append(warn)
        else:
            source_id, offset = _pick_source(
                inputs.catalog, fandom_names, rng, fallback_i,
            )

        fallback_i += 1
        used_sources.add((source_id, round(offset, 1)))
        shot_index[shot_id] = (source_id, offset)

        act = _assign_act(time_sec, acts, fallback_count=max(1, len(acts) or 3))

        shot = {
            "id": shot_id,
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
        if intent:
            shot["intent"] = intent
            shot["callback_of"] = callback_of
        shots.append(shot)

    # Dedupe any overlapping shots (timeline overlap, independent of source reuse)
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
    if warnings:
        result["warnings"] = warnings
    if fandom_names and acts:
        quota = {}
        for act_idx in range(1, len(acts) + 1):
            per_fandom_share = round(1.0 / len(fandom_names), 4) if fandom_names else 1.0
            quota[f"act{act_idx}"] = {name: per_fandom_share for name in fandom_names}
        result["fandom_quota"] = quota
    return result


def _collect_planned_callbacks(edit_plan: dict[str, Any]) -> dict[str, str]:
    """Pull out any `{id, callback_of}` pairs the edit plan declared.

    The LLM edit-strategist may put these at act-level (act['shot_intents'])
    or at the top level (edit_plan['shot_intents']). Callers can also specify
    them directly on a shot object in a pre-existing shot-list, though the
    proposer is usually building from scratch.
    """
    pairs: dict[str, str] = {}
    top = edit_plan.get("shot_intents") or []
    for entry in top:
        if isinstance(entry, dict) and entry.get("callback_of"):
            pairs[entry["id"]] = entry["callback_of"]
    for act in edit_plan.get("acts") or []:
        for entry in act.get("shot_intents") or []:
            if isinstance(entry, dict) and entry.get("callback_of"):
                pairs[entry["id"]] = entry["callback_of"]
    return pairs


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
