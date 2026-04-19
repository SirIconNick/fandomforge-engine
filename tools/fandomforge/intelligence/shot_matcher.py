"""Shot-to-beat matcher — the core automation.

Given:
    - beat-map.json (when to cut)
    - source-catalog.json (what's available)
    - edit-plan.json (theme / acts / fandom quotas / vibe)
    - CLIP embeddings per source (search by meaning)

Produce a schema-valid shot-list.json with one shot per beat slot, scored
on T/F/E/B:

    T = theme-fit          (CLIP cosine to act's theme query)
    F = fandom-balance     (how close fandom mix is to per-act quota)
    E = emotion            (mood_tags vs. act emotional_goal overlap)
    B = beat-sync          (duration_frames matches beat spacing)

Shots flagged by cliche_detector get `cliche_flag=true`. The QA gate (Phase 3)
rejects cliche shots that don't carry an `override_reason`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fandomforge import __version__
from fandomforge.intelligence.cliche_detector import is_cliche
from fandomforge.validation import validate, validate_and_write

logger = logging.getLogger(__name__)

__all__ = ["MatchConfig", "match_shots", "match_shots_from_files"]


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class MatchConfig:
    """Knobs the caller may tune per project."""

    fps: int = 24
    resolution: tuple[int, int] = (1920, 1080)
    # Target duration per shot in frames. If the act has 4 beats and a 48-frame
    # spacing, each shot lands at 48 frames (2 sec at 24fps).
    min_shot_frames: int = 18
    max_shot_frames: int = 144
    # Weights for the final combined score.
    w_theme: float = 0.4
    w_fandom: float = 0.2
    w_emotion: float = 0.25
    w_beat_sync: float = 0.15
    # How strictly to reject cliche shots at match time (True = exclude,
    # False = keep but mark cliche_flag). QA gate handles final rejection.
    exclude_cliche: bool = False
    cliche_threshold: float = 0.75
    # Cap per-source reuse across the edit.
    max_reuse_per_source: int = 3


@dataclass
class _Candidate:
    """A scored candidate shot for a single beat slot."""

    source_id: str
    fandom: str
    time_sec: float
    theme_score: float
    fandom_score: float
    emotion_score: float
    beat_sync_score: float
    mood_tags: list[str] = field(default_factory=list)
    description: str = ""

    @property
    def combined(self) -> float:
        # Outer scale — set by match_shots with config weights.
        return 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _beat_slots_for_act(
    act: dict[str, Any],
    beat_map: dict[str, Any],
    default_shot_frames: int,
    fps: int,
) -> list[dict[str, Any]]:
    """Return a list of beat-sync slots for an act.

    Each slot is:
        {"start_sec": float, "duration_sec": float, "beat_type": str, "beat_index": int}

    Strategy: take every downbeat inside the act window; if fewer than 3, use
    every beat; if still zero, use regular 2-sec slots.
    """
    start_sec = float(act["start_sec"])
    end_sec = float(act["end_sec"])
    downbeats = [b for b in beat_map.get("downbeats", []) if start_sec <= b < end_sec]
    beats = [b for b in beat_map.get("beats", []) if start_sec <= b < end_sec]

    if len(downbeats) >= 3:
        anchors = downbeats
        beat_type = "downbeat"
    elif len(beats) >= 3:
        anchors = beats
        beat_type = "beat"
    else:
        step = default_shot_frames / fps
        anchors = []
        t = start_sec
        while t < end_sec - step / 2:
            anchors.append(t)
            t += step
        beat_type = "free"

    slots: list[dict[str, Any]] = []
    for i, a in enumerate(anchors):
        next_a = anchors[i + 1] if i + 1 < len(anchors) else end_sec
        duration = max(0.5, min(6.0, next_a - a))
        slots.append({
            "start_sec": a,
            "duration_sec": duration,
            "beat_type": beat_type,
            "beat_index": i,
        })
    return slots


def _load_clip_embeddings(source: dict[str, Any]) -> tuple[Any, Any] | None:
    """Load (times, embeddings) arrays from a source's .clip.npz file.

    Returns None if the embeddings file is missing.
    """
    derived = source.get("derived", {})
    npz_path = derived.get("clip_embeddings")
    if not npz_path or not Path(npz_path).exists():
        return None
    try:
        import numpy as np  # type: ignore
    except ImportError:
        return None
    data = np.load(npz_path)
    return data["times"], data["embeddings"]


def _encode_text_queries(queries: list[str]) -> Any:
    """Embed a list of text queries with OpenCLIP. Returns an (N, D) tensor, or
    None if open_clip isn't available."""
    try:
        import open_clip  # type: ignore
        import torch  # type: ignore
    except ImportError:
        return None
    from fandomforge.ingest import OPENCLIP_CACHE
    model, _, _ = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="laion2b_s34b_b79k", cache_dir=str(OPENCLIP_CACHE)
    )
    tokenizer = open_clip.get_tokenizer("ViT-B-32")
    getattr(model, "eval")()
    with torch.no_grad():
        toks = tokenizer(queries)
        emb = model.encode_text(toks)
        emb = emb / emb.norm(dim=-1, keepdim=True)
    return emb.cpu().numpy()


def _theme_score_for_source(
    source: dict[str, Any],
    act_query_emb: Any,
) -> tuple[float, float, Any]:
    """Score a source against an act theme query.

    Returns (best_time_sec, best_score, all_scores_array). If CLIP data is
    missing, returns (0.0, 0.5, None) so the source stays in the running based
    on fandom/emotion alone.
    """
    clip = _load_clip_embeddings(source)
    if clip is None or act_query_emb is None:
        return 0.0, 0.5, None
    times, embs = clip
    import numpy as np  # type: ignore

    # Cosine sim — both sides are L2-normalized already.
    sims = embs @ act_query_emb  # shape (num_frames,)
    best = int(np.argmax(sims))
    return float(times[best]), float((sims[best] + 1.0) / 2.0), sims


def _emotion_score(
    source_moods: list[str],
    act_emotion_goal: str,
) -> float:
    """Bag-of-words overlap between source mood_tags and the act's emotional_goal."""
    if not source_moods or not act_emotion_goal:
        return 0.5
    goal_words = {w.lower() for w in act_emotion_goal.split() if len(w) > 3}
    if not goal_words:
        return 0.5
    hits = sum(1 for m in source_moods if m.lower() in goal_words)
    return min(1.0, hits / max(1, len(source_moods)))


def _fandom_score(
    source_fandom: str,
    fandom_focus: dict[str, float],
    actual_shares: dict[str, float],
) -> float:
    """How well picking this source helps us hit the target fandom share.

    Higher when the source's fandom is under-represented vs. target.
    """
    if not fandom_focus:
        return 1.0
    target = fandom_focus.get(source_fandom, 0.0)
    actual = actual_shares.get(source_fandom, 0.0)
    gap = target - actual
    if gap <= 0:
        return 0.3  # fandom already met or overshot
    return min(1.0, 0.5 + gap)


def _beat_sync_score(
    duration_frames: int,
    slot_duration_sec: float,
    fps: int,
    min_frames: int,
    max_frames: int,
) -> float:
    """Score how close a candidate shot's duration matches its slot's beat."""
    if duration_frames < min_frames or duration_frames > max_frames:
        return 0.3
    target_frames = slot_duration_sec * fps
    if target_frames <= 0:
        return 0.5
    err = abs(duration_frames - target_frames) / target_frames
    return max(0.0, 1.0 - err)


def _shot_role_for_act(act_number: int, slot_index: int) -> str:
    """Pick a role based on where in the act we are.

    Act 1 / first slot: establishing; Act N / last slot: hero; everything in
    between cycles through action / reaction / detail / motion.
    """
    if slot_index == 0:
        return "establishing"
    cycle = ["action", "reaction", "detail", "motion", "hero"]
    return cycle[slot_index % len(cycle)]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def match_shots(
    *,
    beat_map: dict[str, Any],
    source_catalog: dict[str, Any],
    edit_plan: dict[str, Any],
    config: MatchConfig | None = None,
) -> dict[str, Any]:
    """Match beat slots in each act to source clips, return a schema-valid
    shot-list.json dict.
    """
    cfg = config or MatchConfig()
    validate(beat_map, "beat-map")
    validate(source_catalog, "source-catalog")
    validate(edit_plan, "edit-plan")

    fps = int(edit_plan.get("fps", cfg.fps))
    resolution = edit_plan.get("resolution") or {"width": cfg.resolution[0], "height": cfg.resolution[1]}

    sources = source_catalog["sources"]

    # Only load CLIP + encode text queries when at least one source has frame
    # embeddings on disk. Otherwise the matcher falls back to fandom/emotion
    # scoring and never touches a neural net.
    any_clip = any(
        Path(s.get("derived", {}).get("clip_embeddings", "")).exists()
        for s in sources
    )
    text_embs = None
    if any_clip:
        act_queries: list[str] = []
        for act in edit_plan["acts"]:
            q = act.get("emotional_goal", "") + " " + act.get("key_image", "") + " " + act.get("name", "")
            act_queries.append(q.strip() or edit_plan["concept"].get("theme", "a dramatic moment"))
        text_embs = _encode_text_queries(act_queries)
    if not sources:
        raise ValueError("source-catalog has no sources; cannot match shots")

    reuse_count: dict[str, int] = {}
    actual_fandom_counts: dict[str, int] = {}
    total_assigned = 0
    shots: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    song_duration = float(beat_map.get("duration_sec", edit_plan["length_seconds"]))

    shot_counter = 0
    timeline_frame = 0

    for act_idx, act in enumerate(edit_plan["acts"]):
        slots = _beat_slots_for_act(act, beat_map, cfg.min_shot_frames * 2, fps)
        if not slots:
            continue

        fandom_focus = act.get("fandom_focus", {}) or {}
        emotion_goal = act.get("emotional_goal", "")

        act_query_emb = text_embs[act_idx] if text_embs is not None else None

        # Pre-score every source for this act's theme (CLIP).
        source_theme: dict[str, tuple[float, float, Any]] = {}
        for s in sources:
            best_time, best_score, all_scores = _theme_score_for_source(s, act_query_emb)
            source_theme[s["id"]] = (best_time, best_score, all_scores)

        for slot_idx, slot in enumerate(slots):
            # Score every candidate.
            candidates: list[tuple[float, dict[str, Any]]] = []
            for s in sources:
                source_id = s["id"]
                used = reuse_count.get(source_id, 0)
                if used >= cfg.max_reuse_per_source:
                    continue

                best_time, theme, scores = source_theme[source_id]
                fandom = s.get("fandom", "Unknown")

                # Fandom score against running totals.
                current_total = max(1, total_assigned)
                current_shares = {
                    k: v / current_total for k, v in actual_fandom_counts.items()
                }
                f_score = _fandom_score(fandom, fandom_focus, current_shares)

                mood_tags = _source_mood_tags(s)
                e_score = _emotion_score(mood_tags, emotion_goal)

                duration_frames = max(cfg.min_shot_frames, int(slot["duration_sec"] * fps))
                b_score = _beat_sync_score(duration_frames, slot["duration_sec"], fps, cfg.min_shot_frames, cfg.max_shot_frames)

                combined = (
                    cfg.w_theme * theme
                    + cfg.w_fandom * f_score
                    + cfg.w_emotion * e_score
                    + cfg.w_beat_sync * b_score
                )

                # Prefer non-overlapping times within the same source.
                if scores is not None and used > 0:
                    combined *= 0.95 ** used

                description = _describe_shot(s, best_time, mood_tags)
                cliche = is_cliche(description, fandom=fandom, threshold=cfg.cliche_threshold)
                if cliche is not None:
                    if cfg.exclude_cliche:
                        rejected.append({
                            "source_id": source_id,
                            "source_timecode": _sec_to_tc(best_time),
                            "reason": f"cliche: {cliche.phrase}",
                        })
                        continue
                    # Penalize but keep. QA gate will require override_reason.
                    combined *= 0.5

                shot_data = {
                    "_source": s,
                    "_fandom": fandom,
                    "_best_time": best_time,
                    "_duration_frames": duration_frames,
                    "_mood_tags": mood_tags,
                    "_description": description,
                    "_scores": {
                        "theme_fit": round(theme * 5, 2),
                        "fandom_balance": round(f_score * 5, 2),
                        "emotion": round(e_score * 5, 2),
                        "beat_sync_score": round(b_score * 5, 2),
                    },
                    "_cliche": cliche,
                    "_combined": combined,
                }
                candidates.append((combined, shot_data))

            if not candidates:
                # Nothing available (everything capped on reuse). Skip.
                continue

            candidates.sort(key=lambda x: -x[0])
            _, best = candidates[0]
            source = best["_source"]
            source_id = source["id"]

            shot_counter += 1
            shot_id = f"act{act['number']}-shot-{shot_counter:03d}"
            characters_present = [
                c["character"] for c in source.get("characters_present", [])
                if c.get("confidence", 0.0) >= 0.5
            ]

            shot: dict[str, Any] = {
                "id": shot_id,
                "act": int(act["number"]),
                "start_frame": int(timeline_frame),
                "duration_frames": int(best["_duration_frames"]),
                "source_id": source_id,
                "source_timecode": _sec_to_tc(best["_best_time"]),
                "role": _shot_role_for_act(int(act["number"]), slot_idx),
                "mood_tags": best["_mood_tags"],
                "scores": best["_scores"],
                "transition_to_next": "hard_cut",
                "safe_area_ok": True,
                "cliche_flag": best["_cliche"] is not None,
                "reuse_index": reuse_count.get(source_id, 0),
                "description": best["_description"],
                "characters": characters_present,
                "fandom": best["_fandom"],
                "beat_sync": {
                    "type": slot["beat_type"],
                    "index": slot["beat_index"],
                    "time_sec": slot["start_sec"],
                },
            }

            if best["_cliche"] is not None and not cfg.exclude_cliche:
                # Schema allows missing override_reason, but QA gate will flag
                # it. Leaving the field off is intentional: downstream is where
                # the human provides the reason via the editor.
                pass

            shots.append(shot)
            reuse_count[source_id] = reuse_count.get(source_id, 0) + 1
            actual_fandom_counts[best["_fandom"]] = (
                actual_fandom_counts.get(best["_fandom"], 0) + 1
            )
            total_assigned += 1
            timeline_frame += int(best["_duration_frames"])

    # Assemble output.
    fandom_quota: dict[str, dict[str, float]] = {}
    for act in edit_plan["acts"]:
        if act.get("fandom_focus"):
            fandom_quota[str(act["number"])] = dict(act["fandom_focus"])

    out: dict[str, Any] = {
        "schema_version": 1,
        "project_slug": edit_plan["project_slug"],
        "fps": fps,
        "resolution": resolution,
        "song_duration_sec": song_duration,
        "shots": shots,
        "rejected": rejected,
        "generated_at": _now_iso(),
        "generator": f"ff match shots ({__version__})",
    }
    if fandom_quota:
        out["fandom_quota"] = fandom_quota

    validate(out, "shot-list")
    return out


# ---------------------------------------------------------------------------
# File-level entry
# ---------------------------------------------------------------------------


def match_shots_from_files(
    *,
    project_dir: Path,
    beat_map_path: Path,
    source_catalog_path: Path,
    edit_plan_path: Path,
    output_path: Path,
    config: MatchConfig | None = None,
) -> dict[str, Any]:
    """Convenience wrapper: load artifacts, match, write shot-list.json."""
    beat_map = json.loads(beat_map_path.read_text(encoding="utf-8"))
    source_catalog = json.loads(source_catalog_path.read_text(encoding="utf-8"))
    edit_plan = json.loads(edit_plan_path.read_text(encoding="utf-8"))
    shot_list = match_shots(
        beat_map=beat_map,
        source_catalog=source_catalog,
        edit_plan=edit_plan,
        config=config,
    )
    validate_and_write(shot_list, "shot-list", output_path)
    return shot_list


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def _sec_to_tc(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _source_mood_tags(source: dict[str, Any]) -> list[str]:
    """Pull mood tags from derived scenes (dominant color → crude mood) and
    from source-level metadata if present. In practice a catalog entry may
    already carry mood_tags via the clip catalog; here we just synthesize
    something useful from what we have."""
    tags: list[str] = []
    fandom = source.get("fandom", "").lower()
    if "marvel" in fandom or "mcu" in fandom:
        tags.append("hype")
    if "star wars" in fandom:
        tags.append("cinematic")
    if "horror" in source.get("source_type", ""):
        tags.append("tense")
    # Low-res sources often feel gritty.
    w = source.get("media", {}).get("width", 0)
    if w and w < 1280:
        tags.append("vintage")
    return list(dict.fromkeys(tags))


def _describe_shot(source: dict[str, Any], time_sec: float, moods: list[str]) -> str:
    title = source.get("title") or source.get("id")
    tc = _sec_to_tc(time_sec)
    mood_str = ", ".join(moods) if moods else "neutral"
    return f"{title} @ {tc} ({mood_str})"
