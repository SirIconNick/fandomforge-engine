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

    def _clip_category_for(role: str, pacing: str | None, in_climax: bool, kind: str) -> str:
        """Stamp clip_category at propose-time so type_fit QA sees a sensible
        distribution. extract_clip_metadata later only overrides if unset,
        so this is the authoritative stamp.

        Action-heavy edits need action-high on drops + frantic downbeats —
        the default ROLE_TO_CATEGORY maps `action` to `action-mid` which tanks
        type_fit on action edits. Pacing-aware override fixes that.
        """
        if role == "hero":
            return "action-high" if in_climax or pacing in {"fast", "frantic"} else "climactic"
        if role == "action":
            if pacing == "frantic" or (in_climax and kind == "drop"):
                return "action-high"
            if pacing in {"fast", "medium"}:
                return "action-mid"
            return "action-mid"
        if role == "reaction":
            return "reaction-emotional" if in_climax else "reaction-quiet"
        if role == "establishing":
            return "establishing"
        if role == "cut-on-action":
            return "action-mid"
        if role == "environment":
            return "establishing"
        if role == "detail":
            return "texture"
        if role == "motion":
            return "transitional"
        return "texture"

    # Phase B→A- upgrade: editorial role assignment instead of round-robin.
    # Roles are chosen by (sync_kind, act_position_in_edit, act_pacing). Drops
    # in the climactic act get `hero` with an implicit clip_category hint;
    # downbeats early in the edit get establishing; downbeats mid-edit cycle
    # through action/reaction; downbeats near the end get reaction to signal
    # resolution. This replaces SHOT_ROLES_ORDER[i % N] which produced
    # random role ordering regardless of dramatic position.

    def _act_index_at(t_sec: float) -> int:
        """1-based index of the act containing t_sec. 1 if no acts."""
        for idx, a in enumerate(acts, start=1):
            start = float(a.get("start_sec", 0) or 0)
            end = float(a.get("end_sec", 0) or 0)
            if start <= t_sec < end:
                return idx
        return 1

    def _position_in_act(t_sec: float) -> float:
        """0.0-1.0 where t_sec falls within its containing act."""
        for a in acts:
            start = float(a.get("start_sec", 0) or 0)
            end = float(a.get("end_sec", 0) or 0)
            if start <= t_sec < end and end > start:
                return (t_sec - start) / (end - start)
        return 0.0

    n_acts = max(1, len(acts))

    for i, (time_sec, kind) in enumerate(sync_points):
        if time_sec >= song_duration:
            break

        pacing = _act_pacing_at(time_sec)
        act_idx = _act_index_at(time_sec)
        pos_in_act = _position_in_act(time_sec)
        # Climactic acts are the last third of the edit (act 3+ in a 4-act
        # structure, act 2+ in a 3-act). Drops in those acts get the fiercest
        # treatment.
        in_climax = (act_idx / n_acts) >= 0.66

        # Decide role from (sync_kind, act position, act pacing).
        if kind == "drop":
            role = "hero"  # drops always carry weight
        elif kind == "downbeat":
            # Editorial cycle through the act: establishing → action → reaction
            if act_idx == 1 and pos_in_act < 0.4:
                role = "establishing"
            elif in_climax and pos_in_act < 0.7:
                role = "action"
            elif in_climax:
                role = "reaction"  # late-climax = emotional payoff
            elif pacing in {"fast", "frantic"}:
                role = "action" if pos_in_act < 0.5 else "cut-on-action"
            elif pacing == "slow":
                role = "reaction" if pos_in_act > 0.5 else "environment"
            else:
                # Cycle through action / reaction / detail with a deterministic
                # pattern based on beat index — better than random round-robin.
                cycle = ["action", "reaction", "detail", "motion"]
                role = cycle[i % len(cycle)]
        else:
            role = "insert"

        # Duration from pacing band when available, else legacy defaults.
        if pacing:
            lo_sec, hi_sec = shot_duration_band(pacing)
            if kind == "drop":
                target_sec = hi_sec
            elif kind == "downbeat":
                target_sec = (lo_sec + hi_sec) / 2
            else:
                target_sec = lo_sec
            duration_frames = max(cfg.min_shot_frames, _sec_to_frames(target_sec, cfg.fps))
        else:
            if kind == "drop":
                duration_frames = cfg.hero_shot_frames
            elif kind == "downbeat":
                duration_frames = cfg.hero_shot_frames // 2
            else:
                duration_frames = cfg.min_shot_frames

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
            "clip_category": _clip_category_for(role, pacing, in_climax, kind),
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
            # Default to SAFE — aspect_normalize will flip this to False on
            # any shot it can't fit within the target platform's safe envelope.
            # Strict platforms (tiktok/reels/shorts) require this field; the
            # default stamp means qa.safe_area doesn't spuriously block on
            # shot_proposer-drafted lists where aspect_normalize still computed
            # a clean fit.
            "safe_area_ok": True,
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


# ---------------------------------------------------------------------------
# Densify — fill gaps between sync-point shots so total duration covers song
# ---------------------------------------------------------------------------


# Conservative filler band when no act pacing is declared. 1.5s average keeps
# pacing readable without hammering the viewer; the real pacing bands from
# arc_architect.shot_duration_band() take over whenever acts carry `pacing`.
_DEFAULT_FILLER_BAND_SEC: tuple[float, float] = (1.0, 2.0)
_MIN_FILLER_SEC = 0.4  # don't emit a filler shorter than this; fold into neighbor

# Scenes whose avg_luma is below this threshold are near-black frames —
# fade-outs, cuts-to-black, establishing darks. The old picker happily used
# them as fillers and the render showed visible black flashes between shots.
# We hard-reject anything below this floor and only fall back to it when the
# source has NO brighter scenes (cataclysmic last resort).
#
# Threshold was 0.15 initially but the post-render reviewer (blackdetect
# with pix_th=0.1) still flagged dim action scenes where avg_luma=0.17-0.23
# had sub-segments below 0.1. Raised to 0.22 to put a safety buffer between
# the picker's avg_luma floor and the reviewer's per-frame threshold.
MIN_SCENE_LUMA = 0.22


# Pacing → target scene intensity_tier. Drives which scenes densify picks
# when scene-matching is enabled. Keeps slow acts feeling slow (low-intensity
# fillers), frantic acts feeling earned (high-intensity fillers).
_PACING_TO_INTENSITY: dict[str, str] = {
    "slow": "low",
    "medium": "medium",
    "fast": "high",
    "frantic": "high",
}


# Target cuts-per-minute by edit_type. Drives how aggressively densify
# stretches filler durations when the pacing bands alone would emit too
# many shots. Numbers come from reference_priors empirical medians —
# action edits sit around 45 cpm, sad edits around 18, tribute around 25.
# Lower bounds on natural editing feel; stretch never takes a filler
# below its pacing band's `lo`. Upper clamp is `hi * 1.5` so slow acts
# stay recognizably slower than frantic acts.
_TARGET_CPM_BY_EDIT_TYPE: dict[str, float] = {
    "action": 45.0,
    "sad": 18.0,
    "tribute": 25.0,
    "dance": 30.0,
    "hype_trailer": 55.0,
    "cataloged_and_ready": 40.0,
    "fan_mashup": 40.0,
}
_DEFAULT_TARGET_CPM = 35.0
_STRETCH_CLAMP_HI_MULT = 1.5  # never stretch a filler past band_hi * this


def _resolve_target_cpm(edit_plan: dict[str, Any] | None) -> float:
    """Pick the target cuts-per-minute for this edit.

    Priority: explicit edit_plan.target_cpm > edit_type lookup > default.
    Rejects non-numeric / non-positive values (defensive — LLM-generated
    edit plans have been known to emit strings).
    """
    if edit_plan:
        explicit = edit_plan.get("target_cpm")
        if isinstance(explicit, (int, float)) and float(explicit) > 0:
            return float(explicit)
        et = str(edit_plan.get("edit_type") or "")
        if et in _TARGET_CPM_BY_EDIT_TYPE:
            return _TARGET_CPM_BY_EDIT_TYPE[et]
    return _DEFAULT_TARGET_CPM


def densify_shot_list(
    shot_list: dict[str, Any],
    *,
    edit_plan: dict[str, Any] | None = None,
    song_duration_sec: float | None = None,
    scenes_by_source: dict[str, list[dict[str, Any]]] | None = None,
    source_catalog: list[dict[str, Any]] | None = None,
    target_duration_sec: float | None = None,
    drop_times: list[float] | None = None,
    avoid_ranges: dict[str, list[tuple[float, float]]] | None = None,
) -> dict[str, Any]:
    """Expand a sparse slot-list shot-list to cover the target duration.

    propose_shot_list() emits one shot per sync point (drops + downbeats),
    which for a 229s song with 13 drops + 3 downbeats produces ~15 shots
    totaling only ~16s. qa.duration then hard-fails because total shot
    duration doesn't match the target.

    This pass fills the gap between each pair of consecutive slot shots
    (plus the tail to target end) with "insert"-role filler shots.

    `target_duration_sec` is the authoritative output length. When set and
    less than song_duration_sec, the densifier (1) drops slot shots whose
    start is past target, (2) clamps the final kept slot shot so it does
    not spill past target, and (3) terminates the tail fill at target.
    When unset, behavior falls through to song_duration_sec (legacy — a
    full-song edit).

    When `scenes_by_source` is provided (dict of source_id → list of scene
    dicts with start_sec / intensity_tier / motion / avg_luma), fillers are
    SCENE-MATCHED — each filler picks a scene whose intensity_tier aligns
    with the act's pacing, rotating across sources to avoid the "same sliver
    repeated 282 times" engagement-killer. Without scenes, falls back to
    the legacy flanking-shot-inheritance behavior.

    Returns a new shot-list dict with re-numbered ids (s001..sNNN) and
    densified shots. Input is not mutated.
    """
    from fandomforge.intelligence.arc_architect import shot_duration_band

    shots_in = list(shot_list.get("shots") or [])
    fps = float(shot_list.get("fps") or 24.0)
    song_sec = float(
        song_duration_sec
        or shot_list.get("song_duration_sec")
        or 0.0
    )
    if song_sec <= 0.0:
        # No song duration known — nothing to fill to. Return untouched.
        return dict(shot_list)

    # target_duration_sec overrides song length as the fill target. Required
    # so a 90s project-config doesn't render 229s of song. min() guards the
    # case where a user set target > song — don't fill past song either.
    target_sec_in = float(target_duration_sec or 0.0)
    if target_sec_in > 0.0:
        fill_sec = min(song_sec, target_sec_in)
    else:
        fill_sec = song_sec

    acts = (edit_plan or {}).get("acts") or []

    # Scene-match bookkeeping. used_scenes tracks (source_id, scene_index)
    # pairs we've already placed; source_use_count biases selection toward
    # under-represented sources so engagement doesn't collapse.
    # recent_source_ids is a rolling window of the last few picks — the
    # most recent one gets a hard penalty (never use same source twice
    # in a row), older ones get a soft penalty, so visually the edit
    # doesn't hammer one source for 3-4 shots straight.
    used_scenes: set[tuple[str, int]] = set()
    source_use_count: dict[str, int] = {}
    recent_source_ids: list[str] = []
    _RECENT_WINDOW = 3
    available_sources = list((scenes_by_source or {}).keys())

    def _intensity_for(t_sec: float) -> str:
        pacing = _pacing_at(t_sec)
        if pacing and pacing in _PACING_TO_INTENSITY:
            return _PACING_TO_INTENSITY[pacing]
        return "medium"

    def _scene_duration(sc: dict[str, Any]) -> float:
        """Handle minimal scene dicts (no duration_sec field) by falling back
        to end_sec - start_sec. Derived scenes from ff ingest emit the latter;
        legacy hand-curated scenes emit the former."""
        d = sc.get("duration_sec")
        if isinstance(d, (int, float)) and d > 0:
            return float(d)
        start = sc.get("start_sec")
        end = sc.get("end_sec")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            return max(0.0, float(end) - float(start))
        return 0.0

    def _scene_intensity(sc: dict[str, Any]) -> str:
        """Fallback intensity classification when the scene doesn't carry
        `intensity_tier`. Uses duration as a crude proxy: short scenes are
        high-energy (fast action cuts), long scenes are low-energy
        (establishing / reaction).
          <1.0s → high, 1.0-3.0s → medium, ≥3.0s → low
        Scenes with explicit intensity_tier keep theirs."""
        tier = sc.get("intensity_tier")
        if tier in ("low", "medium", "high"):
            return tier
        dur = _scene_duration(sc)
        if dur < 1.0:
            return "high"
        if dur < 3.0:
            return "medium"
        return "low"

    def _scene_in_avoid_range(src: str, sc: dict[str, Any]) -> bool:
        """A scene is in an avoid-range when its start OR end overlaps any
        (start, end) tuple supplied in avoid_ranges[src]. Used to skip
        compilation intros/outros (title cards, thumbnail grids) that
        look bright enough to slip past the luma filter but land wrong."""
        if not avoid_ranges:
            return False
        ranges = avoid_ranges.get(src) or []
        if not ranges:
            return False
        start = float(sc.get("start_sec") or 0)
        end = float(sc.get("end_sec") or start)
        for lo, hi in ranges:
            # Any overlap is enough to reject — partial-overlap picks tend
            # to spill into the card's visible portion.
            if end > lo and start < hi:
                return True
        return False

    def _scene_luma(sc: dict[str, Any]) -> float | None:
        """Return scene avg_luma as float, or None if the field is missing /
        invalid. Missing luma means we can't reject dark scenes — we skip
        the luma filter rather than reject everything."""
        val = sc.get("avg_luma")
        if isinstance(val, (int, float)):
            return float(val)
        return None

    def _opposite_dir(a: str | None, b: str | None) -> bool:
        """Two motion_dir values face opposite directions? Same-axis opposites
        only ('left' vs 'right', 'up' vs 'down'). 'static'/'mixed'/None are
        always compatible — never opposite."""
        if a is None or b is None:
            return False
        pairs = {("left", "right"), ("right", "left"),
                 ("up", "down"), ("down", "up")}
        return (a, b) in pairs

    def _pick_scene(
        target_intensity: str,
        used_src: set[str],
        prev_luma: float | None = None,
        prev_motion_dir: str | None = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Pick the best scene across all sources for the target intensity.

        Priority: (1) avg_luma ≥ MIN_SCENE_LUMA (no black frames),
        (2) intensity match, (3) source rotation (fewest prior uses),
        (4) within that source, luma proximity to prev_luma (continuity).

        If every unused scene across every source is dark, falls back to
        the brightest dark scene rather than returning None — better a
        slightly-dim filler than a crash or a gap.

        prev_luma is the flanking shot's avg_luma. When known, candidates
        within the chosen source are ranked by |cand.avg_luma - prev_luma|
        so consecutive cuts don't flash between very dark and very bright
        scenes. When prev_luma is None (no data), rotation order decides.
        """
        if not scenes_by_source:
            return None

        def _recency_penalty(src: str) -> int:
            if not recent_source_ids:
                return 0
            if src == recent_source_ids[-1]:
                return 1000  # HARD: never use same source back-to-back
            if src in recent_source_ids:
                return 10    # SOFT: prefer sources outside the recent window
            return 0

        # Rank sources by: (1) no back-to-back repeat, (2) prefer sources
        # not in recent window, (3) fewest prior uses overall. Breaks
        # hard repeats and reduces clusters without starving anything.
        ranked_sources = sorted(
            available_sources,
            key=lambda s: (_recency_penalty(s), source_use_count.get(s, 0), s),
        )

        def _sort_candidates(cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
            """Rank within a source by a combined continuity cost:
               cost = luma_distance + MOTION_DIR_PENALTY * opposite_flag
            Where opposite_flag is 1 if cand's motion_dir is the opposite of
            prev_motion_dir, else 0. Penalty is tuned so a strong luma match
            can still beat a direction mismatch, but when luma ties the
            same-axis-continuity candidate wins."""
            if prev_luma is None and prev_motion_dir is None:
                return cands

            def _dist(sc: dict[str, Any]) -> float:
                # Luma component
                if prev_luma is not None:
                    luma = _scene_luma(sc)
                    luma_part = abs(luma - prev_luma) if luma is not None else 1.0
                else:
                    luma_part = 0.0
                # Motion-direction component
                mdir_part = 0.3 if _opposite_dir(
                    sc.get("motion_dir"), prev_motion_dir,
                ) else 0.0
                return luma_part + mdir_part

            return sorted(cands, key=_dist)

        # Three passes: (a) strict intensity + luma, (b) any intensity + luma,
        # (c) last-resort including dark scenes (picks brightest dark).
        dark_fallbacks: list[tuple[float, str, dict[str, Any]]] = []
        for strict in (True, False):
            for src in ranked_sources:
                scenes = scenes_by_source.get(src) or []
                passing: list[dict[str, Any]] = []
                for sc in scenes:
                    idx = sc.get("index", -1)
                    if (src, idx) in used_scenes:
                        continue
                    if _scene_duration(sc) < 0.4:
                        continue
                    if _scene_in_avoid_range(src, sc):
                        continue  # intro/outro windows of compilations
                    if strict and _scene_intensity(sc) != target_intensity:
                        continue
                    luma = _scene_luma(sc)
                    if luma is not None and luma < MIN_SCENE_LUMA:
                        dark_fallbacks.append((luma, src, sc))
                        continue
                    passing.append(sc)
                if passing:
                    best = _sort_candidates(passing)[0]
                    return src, best
        # Every candidate was dark. Pick the brightest.
        if dark_fallbacks:
            dark_fallbacks.sort(key=lambda t: -t[0])
            return dark_fallbacks[0][1], dark_fallbacks[0][2]
        return None

    def _pacing_at(t_sec: float) -> str | None:
        for a in acts:
            start = float(a.get("start_sec", 0) or 0)
            end = float(a.get("end_sec", 0) or 0)
            if start <= t_sec < end:
                return a.get("pacing")
        return None

    # Pre-sort drops for the ramp lookup. Empty list means no ramping —
    # feature is off unless the caller supplies drops.
    _drops_sorted = sorted(drop_times) if drop_times else []

    def _band_at(at_t: float) -> tuple[float, float]:
        pacing = _pacing_at(at_t)
        if pacing:
            return shot_duration_band(pacing)
        return _DEFAULT_FILLER_BAND_SEC

    def _drop_ramp_factor(at_t: float) -> float:
        """Multiplier on filler duration based on proximity to drops.

        Ramps shots SHORTER in the 3.5s leading up to a drop (build
        tension) and slightly LONGER in the 2s after (breathing room,
        viewer recovers from the hit). Outside those windows, no effect.

        Returns a factor in [0.5, 1.15] that gets multiplied into the
        already-stretched filler duration.
        """
        if not _drops_sorted:
            return 1.0
        # Nearest upcoming drop
        upcoming = None
        for dt in _drops_sorted:
            if dt >= at_t:
                upcoming = dt
                break
        if upcoming is not None:
            dist = upcoming - at_t
            if 0.0 <= dist <= 3.5:
                # Linear ramp: dist=3.5 → 1.0, dist=0 → 0.5
                return 0.5 + (dist / 3.5) * 0.5
        # Post-drop window: find the most recent drop behind us
        recent = None
        for dt in _drops_sorted:
            if dt <= at_t:
                recent = dt
            else:
                break
        if recent is not None:
            dist_since = at_t - recent
            if 0.0 <= dist_since <= 2.0:
                # Ramp back up: dist_since=0 → 1.15 (slight breath),
                # dist_since=2 → 1.0
                return 1.15 - (dist_since / 2.0) * 0.15
        return 1.0

    def _filler_dur_sec(at_t: float) -> float:
        lo, hi = _band_at(at_t)
        median = (lo + hi) / 2.0
        # stretch_factor makes the overall cpm land near target_cpm.
        # drop_ramp_factor makes the shots contract into drops and
        # breathe just after. Both multiplicative, then clamped to the
        # band's [lo, hi*1.5] so the act still feels like what the arc
        # architect intended.
        stretched = median * stretch_factor * _drop_ramp_factor(at_t)
        return max(lo, min(stretched, hi * _STRETCH_CLAMP_HI_MULT))

    def _frames(sec: float) -> int:
        return max(1, int(round(sec * fps)))

    # Sort slot shots by start time so gap-fill is linear.
    shots_sorted = sorted(shots_in, key=lambda s: int(s.get("start_frame", 0)))

    # Clamp slot shots to fill_sec. propose_shot_list emits one shot per
    # sync point across the WHOLE song, so a 229s song with target 90s
    # still arrives with sync-point shots past 90s. Drop those; clamp the
    # last kept shot's duration if it spills past target. Without this
    # step the tail-fill logic runs on a clamped end but the slot shots
    # themselves still bleed past target_duration.
    fill_frames_total = max(1, int(round(fill_sec * fps)))
    clamped_shots: list[dict[str, Any]] = []
    for _s in shots_sorted:
        _start = int(_s.get("start_frame", 0) or 0)
        if _start >= fill_frames_total:
            continue  # slot shot starts past target → drop
        _dur = int(_s.get("duration_frames", 0) or 0)
        _copy = dict(_s)
        if _start + _dur > fill_frames_total:
            _copy["duration_frames"] = max(1, fill_frames_total - _start)
        clamped_shots.append(_copy)
    shots_sorted = clamped_shots

    # --- Shot-count budget governor ---------------------------------------
    # Compute a global stretch_factor so the final render's cuts-per-minute
    # lands near target_cpm (pulled from edit_plan.target_cpm → intent →
    # edit_type default). Without this, every "frantic" act uses the band
    # median of ~0.425s and a 229s song produces 297 shots (74 cpm) —
    # machine-gun cutting that tests measure as "duration-correct" but the
    # viewer experiences as unwatchable. See docs/RENDER_POSTMORTEM.md.
    target_cpm = _resolve_target_cpm(edit_plan)

    # Estimate natural fill count: sum over every gap, gap_sec / band_median
    # for that gap's pacing. Include the head gap (0 → first slot) and the
    # tail gap (last slot → fill_sec). Slot shots themselves count toward
    # the output shot count but not toward the stretch math (they're fixed).
    def _iter_gap_windows() -> list[tuple[float, float]]:
        gaps: list[tuple[float, float]] = []
        if not shots_sorted:
            gaps.append((0.0, fill_sec))
            return gaps
        first_start_sec = shots_sorted[0]["start_frame"] / fps
        if first_start_sec > 0:
            gaps.append((0.0, first_start_sec))
        for i, sh in enumerate(shots_sorted):
            sh_end_sec = (int(sh["start_frame"]) + int(sh["duration_frames"])) / fps
            if i + 1 < len(shots_sorted):
                nxt = shots_sorted[i + 1]["start_frame"] / fps
            else:
                nxt = fill_sec
            if nxt > sh_end_sec:
                gaps.append((sh_end_sec, nxt))
        return gaps

    def _natural_filler_count() -> float:
        total = 0.0
        for lo_t, hi_t in _iter_gap_windows():
            mid_t = (lo_t + hi_t) / 2.0
            pacing = _pacing_at(mid_t)
            lo_d, hi_d = shot_duration_band(pacing) if pacing else _DEFAULT_FILLER_BAND_SEC
            med = max(0.05, (lo_d + hi_d) / 2.0)
            total += (hi_t - lo_t) / med
        return total

    natural_fillers = _natural_filler_count()
    slot_count = len(shots_sorted)
    budget_total = max(1.0, target_cpm * fill_sec / 60.0)
    # Fillers we're allowed: budget minus slots. Guard against the case
    # where slot count already exceeds budget — leave stretch_factor=1
    # (caller wanted sparse slots + many fillers, we can't delete slots).
    budget_fillers = max(1.0, budget_total - slot_count)
    if natural_fillers > 0.1:
        stretch_factor = natural_fillers / budget_fillers
    else:
        stretch_factor = 1.0
    # Safety bounds: don't extreme-stretch (loses act feel) or extreme-shrink.
    stretch_factor = max(0.5, min(stretch_factor, 5.0))
    # ----------------------------------------------------------------------

    # Tracks the avg_luma and motion_dir of the most-recently-picked scene,
    # for continuity ranking on the NEXT pick. None until the first scene-
    # matched filler lands. Flank shots (slot-list entries) don't carry
    # these fields yet, so the first filler after a slot shot has no
    # reference — that's fine, _pick_scene degrades gracefully.
    last_picked_luma: dict[str, float | None] = {"v": None}
    last_picked_dir: dict[str, str | None] = {"v": None}

    def _make_filler(cursor: int, dur_frames: int, flank: dict[str, Any]) -> dict[str, Any]:
        """Scene-matched when scenes_by_source is provided, else inherits from
        flanking shot. Source rotation + intensity matching drive engagement."""
        target_intensity = _intensity_for(cursor / fps)
        src_id = flank["source_id"]
        src_tc = flank.get("source_timecode", "0:00:00.000")
        role = "insert"
        motion_vec = None
        clip_cat = "texture"  # default — overridden by intensity match below

        # Seed the recency window with the flanking shot's source ONLY if
        # it's not already in the window. Without this check, repeated
        # _make_filler calls for fillers in the same gap would re-push
        # the same flank source on every call, cycling it back to the
        # tail and allowing the previous filler's source to win on the
        # next pick — defeating the back-to-back guard.
        flank_src = flank.get("source_id")
        if flank_src and flank_src not in recent_source_ids:
            recent_source_ids.append(flank_src)
            if len(recent_source_ids) > _RECENT_WINDOW:
                recent_source_ids.pop(0)

        # prev_luma + prev_motion_dir for continuity. Prefer the last
        # picked scene's values; fall back to flank fields if present.
        # None is acceptable for either; _pick_scene degrades gracefully.
        prev_luma = last_picked_luma["v"]
        if prev_luma is None:
            fla = flank.get("avg_luma")
            if isinstance(fla, (int, float)):
                prev_luma = float(fla)
        prev_dir = last_picked_dir["v"]
        if prev_dir is None:
            fld = flank.get("motion_dir")
            if isinstance(fld, str):
                prev_dir = fld

        picked = _pick_scene(
            target_intensity,
            used_src=set(),
            prev_luma=prev_luma,
            prev_motion_dir=prev_dir,
        )
        if picked is not None:
            src, scene = picked
            src_id = src
            src_tc = _fmt_timecode(float(scene.get("start_sec", 0.0)))
            used_scenes.add((src, scene.get("index", -1)))
            source_use_count[src] = source_use_count.get(src, 0) + 1
            # Push this source onto the recency window and trim the head.
            recent_source_ids.append(src)
            if len(recent_source_ids) > _RECENT_WINDOW:
                recent_source_ids.pop(0)
            # Cap the filler duration to the picked scene's length so the
            # extraction doesn't spill into the next scene. Otherwise a 2.6s
            # filler using a 1.5s scene grabs the first 1.5s of the scene
            # PLUS 1.1s of whatever comes after — which is often dark/fade
            # material the picker specifically rejected.
            scene_dur_frames = max(1, int(round(_scene_duration(scene) * fps)))
            if dur_frames > scene_dur_frames:
                dur_frames = scene_dur_frames
            # Update the continuity memory for the next filler.
            picked_luma = _scene_luma(scene)
            if picked_luma is not None:
                last_picked_luma["v"] = picked_luma
            picked_dir = scene.get("motion_dir")
            if isinstance(picked_dir, str):
                last_picked_dir["v"] = picked_dir
            # Intensity → role + clip_category mapping. High-intensity fillers
            # in frantic acts earn the "action" role + action-high clip_category;
            # low-intensity in slow acts get reaction framing. Mid-intensity
            # stays insert/action-mid. Stamps clip_category at filler time so
            # type_fit QA sees the action-heavy distribution an action edit
            # needs — otherwise all fillers default to "texture" and type_fit
            # fails with "action-high 0%".
            if target_intensity == "high":
                role = "action"
                clip_cat = "action-high"
            elif target_intensity == "low":
                role = "reaction"
                clip_cat = "reaction-quiet"
            else:
                role = "insert"
                clip_cat = "action-mid"
            # Scene motion also feeds motion_vector so cut-on-action QA
            # has something to grade on.
            m = scene.get("motion")
            if isinstance(m, (int, float)) and m > 0.1:
                # Motion magnitude ~ direction proxy: high motion → horizontal
                # left-to-right default (0 degrees). Better direction inference
                # is a Phase 3.1 follow-up.
                motion_vec = 0.0
        else:
            # No scene data — inherit flank's clip_category if it has one,
            # else fall back to a role-appropriate default.
            clip_cat = flank.get("clip_category") or "texture"

        return {
            "id": "",  # renumbered below
            "act": flank.get("act", 1),
            "start_frame": cursor,
            "duration_frames": dur_frames,
            "source_id": src_id,
            "source_timecode": src_tc,
            "role": role,
            "clip_category": clip_cat,
            "mood_tags": [],
            "framing": "",
            "motion_vector": motion_vec,
            "eyeline": "",
            "beat_sync": {
                "type": "free",
                "index": 0,
                "time_sec": cursor / fps,
            },
            "scores": {
                "theme_fit": 3.0, "fandom_balance": 3.0,
                "emotion": 3.0, "beat_sync_score": 2.5,
            },
            "safe_area_ok": bool(flank.get("safe_area_ok", True)),
            "densified": True,
        }

    def _fill_gap(cursor: int, end_frame: int, flank: dict[str, Any]) -> list[dict[str, Any]]:
        fillers: list[dict[str, Any]] = []
        while cursor < end_frame:
            cursor_sec = cursor / fps
            target_sec = _filler_dur_sec(cursor_sec)
            remaining_frames = end_frame - cursor
            dur_frames = min(_frames(target_sec), remaining_frames)
            if dur_frames / fps < _MIN_FILLER_SEC:
                # Tail is too short to justify its own shot. Fold it into
                # the previous filler so the total gap coverage is exact
                # — otherwise qa.duration accumulates ~0.3s * N segments
                # of "almost" coverage.
                if fillers:
                    fillers[-1]["duration_frames"] += remaining_frames
                break
            filler = _make_filler(cursor, dur_frames, flank)
            # _make_filler may shorten dur_frames (e.g. to cap at the picked
            # scene's actual duration). Read the stored value so cursor
            # advances by what we actually filled, not what we asked for.
            actual_frames = int(filler["duration_frames"])
            fillers.append(filler)
            cursor += max(actual_frames, 1)
        return fillers

    densified: list[dict[str, Any]] = []
    # Head-fill: zero to first slot shot.
    if shots_sorted:
        first_start = int(shots_sorted[0]["start_frame"])
        if first_start > 0:
            densified.extend(_fill_gap(0, first_start, shots_sorted[0]))

    for i, shot in enumerate(shots_sorted):
        densified.append(dict(shot))  # copy so we can renumber without mutating caller
        cur_end_frame = int(shot["start_frame"]) + int(shot["duration_frames"])
        if i + 1 < len(shots_sorted):
            next_start_frame = int(shots_sorted[i + 1]["start_frame"])
        else:
            next_start_frame = _frames(fill_sec)

        if next_start_frame <= cur_end_frame:
            continue  # no gap
        densified.extend(_fill_gap(cur_end_frame, next_start_frame, shot))

    # Re-ID shots sequentially.
    for idx, shot in enumerate(densified, start=1):
        shot["id"] = f"s{idx:03d}"

    out = dict(shot_list)
    out["shots"] = densified
    # Drop any prior densify warnings before appending the new one. Otherwise
    # repeated densify runs accumulate stale "15 → 297" messages alongside
    # the current "15 → 53", and reviewers penalize based on the first (old)
    # string they find.
    prior = list(out.get("warnings") or [])
    out["warnings"] = [
        w for w in prior
        if not (isinstance(w, str) and w.startswith("densified from "))
    ]
    out["warnings"].append(
        f"densified from {len(shots_in)} slot shots to {len(densified)} "
        f"(filler={len(densified) - len(shots_in)}, role=insert)"
    )
    return out


__all__ = [
    "ProposerConfig",
    "ProposerInputs",
    "densify_shot_list",
    "load_inputs",
    "propose_shot_list",
    "propose_for_project",
]
