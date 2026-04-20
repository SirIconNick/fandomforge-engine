"""Clip metadata extractor (Phase 1.3).

Populates the per-shot metadata fields the slot-fit scorer keys off:
    emotional_register, clip_category, action_intensity_pct,
    dialogue_clarity_score, lip_sync_confidence, visual_style,
    audio_type, energy_zone_fit

Heuristic-first: derives everything from data the engine already has
(scene catalog, source profile, source-catalog, whisper transcripts when
present, mood_tags + role on the shot). No vision-LLM call here — that
upgrade lives behind a content-hash cache (amendment A5) in a later pass.

Cross-type by design (amendment A3): no per-edit-type branches inside
the extractor. The data is type-agnostic; the slot-fit scorer applies
type-specific weights downstream.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fandomforge.intelligence.clip_categories import (
    CLIP_CATEGORY_IDS,
    categories_for_zone,
)


# 8-dim register: [grief, triumph, fear, awe, tension, release, sorrow, elation]
EMOTION_DIMS = ("grief", "triumph", "fear", "awe", "tension", "release", "sorrow", "elation")

# Mood tags → emotional register contributions. Crude but consistent.
MOOD_TAG_REGISTER: dict[str, dict[str, float]] = {
    "action": {"tension": 0.7, "triumph": 0.4},
    "combat": {"tension": 0.8, "fear": 0.3, "triumph": 0.5},
    "fight": {"tension": 0.7, "triumph": 0.5},
    "hero-down": {"sorrow": 0.6, "grief": 0.5, "tension": 0.5},
    "preamble": {"awe": 0.4, "tension": 0.4},
    "establishing": {"awe": 0.5},
    "reaction": {"tension": 0.4, "sorrow": 0.3},
    "climax": {"triumph": 0.8, "release": 0.3, "tension": 0.7},
    "rise": {"triumph": 0.6, "awe": 0.5},
    "resolve": {"release": 0.7, "triumph": 0.4},
    "rest": {"release": 0.6},
    "victory": {"triumph": 0.9, "elation": 0.5},
    "death": {"grief": 0.9, "sorrow": 0.7, "fear": 0.4},
    "loss": {"grief": 0.8, "sorrow": 0.7},
    "love": {"elation": 0.6, "awe": 0.4},
    "kiss": {"elation": 0.6, "release": 0.4},
    "fear": {"fear": 0.8, "tension": 0.6},
    "horror": {"fear": 0.9, "tension": 0.7},
    "comedic": {"elation": 0.7, "release": 0.5},
    "funny": {"elation": 0.7, "release": 0.4},
    "sad": {"sorrow": 0.8, "grief": 0.6},
    "tender": {"awe": 0.4, "release": 0.5, "elation": 0.3},
    "epic": {"awe": 0.8, "triumph": 0.6},
    "chase": {"tension": 0.8, "fear": 0.4},
    "explosion": {"awe": 0.5, "tension": 0.7},
    "cut-on-action": {"tension": 0.5, "triumph": 0.3},
    "motion": {"tension": 0.4, "triumph": 0.3},
    "establishing-quiet": {"release": 0.4, "awe": 0.3},
}

ROLE_REGISTER: dict[str, dict[str, float]] = {
    "hero": {"triumph": 0.6, "awe": 0.4},
    "action": {"tension": 0.6, "triumph": 0.3},
    "reaction": {"tension": 0.4, "sorrow": 0.3},
    "environment": {"awe": 0.4},
    "detail": {"tension": 0.3},
    "motion": {"tension": 0.5, "triumph": 0.3},
    "gaze": {"sorrow": 0.4, "awe": 0.3, "tension": 0.4},
    "cut-on-action": {"tension": 0.5, "triumph": 0.3},
    "establishing": {"awe": 0.5},
    "transition": {"tension": 0.3},
    "insert": {"awe": 0.2},
    "title": {"awe": 0.3},
}

# role + mood combos that map to a clip-category
ROLE_TO_CATEGORY: dict[str, str] = {
    "establishing": "establishing",
    "hero": "climactic",
    "action": "action-mid",
    "reaction": "reaction-quiet",
    "environment": "establishing",
    "detail": "texture",
    "motion": "action-mid",
    "gaze": "reaction-emotional",
    "cut-on-action": "action-mid",
    "transition": "transitional",
    "insert": "texture",
    "title": "texture",
}

# audio_type heuristics — uses presence of dialogue clues + source profile + intent
DIALOGUE_HINTS = ("dialogue", "speak", "monologue", "voice", "interview", "talk", "narrate")
SFX_HINTS = ("explosion", "gunshot", "punch", "impact", "crash", "boom")


def _normalize_register(raw: dict[str, float]) -> list[float]:
    vec = [float(raw.get(d, 0.0)) for d in EMOTION_DIMS]
    peak = max(vec) if vec else 0.0
    if peak <= 0:
        return [0.0] * 8
    return [round(v / peak, 3) for v in vec]


def _emotional_register(shot: dict[str, Any]) -> list[float]:
    """Combine role + mood-tag contributions into a normalized 8-dim vector."""
    raw: dict[str, float] = {d: 0.0 for d in EMOTION_DIMS}
    role = shot.get("role") or ""
    for dim, weight in (ROLE_REGISTER.get(role, {})).items():
        raw[dim] = max(raw[dim], weight)
    for tag in (shot.get("mood_tags") or []):
        contrib = MOOD_TAG_REGISTER.get(tag.lower(), {})
        for dim, weight in contrib.items():
            raw[dim] = max(raw[dim], weight)
    return _normalize_register(raw)


def _clip_category(shot: dict[str, Any], emotional_register: list[float]) -> str:
    """Map (role, mood_tags, register) → one of CLIP_CATEGORY_IDS.

    Walks role first (cheap default), then upgrades based on mood tags or
    register peaks. Cross-type: same shot in any edit gets the same category;
    edit_type-specific preference comes via clip-categories.edit_type_bias.
    """
    role = shot.get("role") or ""
    mood_tags = {t.lower() for t in (shot.get("mood_tags") or [])}

    # Strong overrides from mood tags
    if "dialogue" in mood_tags or "monologue" in mood_tags or "speech" in mood_tags:
        return "dialogue-primary"
    if "climax" in mood_tags or "victory" in mood_tags:
        return "climactic"
    if "resolve" in mood_tags or "rest" in mood_tags or "denouement" in mood_tags:
        return "resolution"
    if "explosion" in mood_tags or "impact" in mood_tags:
        return "action-high"

    # Use emotional register peaks for further refinement
    triumph_idx = EMOTION_DIMS.index("triumph")
    fear_idx = EMOTION_DIMS.index("fear")
    grief_idx = EMOTION_DIMS.index("grief")
    sorrow_idx = EMOTION_DIMS.index("sorrow")
    if emotional_register[triumph_idx] >= 0.85:
        return "climactic"
    if emotional_register[grief_idx] >= 0.7 or emotional_register[sorrow_idx] >= 0.7:
        return "reaction-emotional"
    if emotional_register[fear_idx] >= 0.7:
        return "action-high"

    # Role-based fallback
    return ROLE_TO_CATEGORY.get(role, "texture")


def _action_intensity_pct(
    shot: dict[str, Any],
    scene_data: dict[str, Any] | None,
    source_motion_baseline: float | None,
) -> float:
    """Use the scene catalog's motion score for this shot's source+timecode.
    Normalize against the source's median motion to avoid cross-source bias.
    """
    if not scene_data:
        return 50.0
    # Find the scene covering the shot's source_timecode
    src_tc = _parse_tc(shot.get("source_timecode") or "0:00:00")
    scenes = scene_data.get("scenes") or []
    for sc in scenes:
        if float(sc.get("start_sec", 0)) <= src_tc < float(sc.get("end_sec", 0)):
            motion = float(sc.get("motion", 0.0))
            if source_motion_baseline and source_motion_baseline > 0:
                # Normalize so 1.0x baseline = 50pct; 2.0x = 100pct
                pct = min(100.0, (motion / source_motion_baseline) * 50.0)
                return round(pct, 1)
            return round(min(100.0, motion * 100.0), 1)
    return 50.0


def _parse_tc(tc: str) -> float:
    try:
        h, m, s = tc.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except (ValueError, AttributeError):
        return 0.0


def _dialogue_clarity_score(
    shot: dict[str, Any],
    transcript: dict[str, Any] | None,
) -> float | None:
    """Crude SNR proxy from whisper word confidence over the shot's window.
    Returns None when no transcript exists for this source."""
    if not transcript:
        return None
    src_tc = _parse_tc(shot.get("source_timecode") or "0:00:00")
    fps = 24.0  # shot fps not in this scope; close enough
    dur = float(shot.get("duration_frames", 24)) / fps
    end_tc = src_tc + dur
    words = transcript.get("words") or []
    relevant = [w for w in words if src_tc <= float(w.get("start_sec", 0)) < end_tc]
    if not relevant:
        return None
    confs = [float(w.get("confidence", 0.5)) for w in relevant if "confidence" in w]
    if not confs:
        # Words present but no confidences — middling clarity assumption
        return 60.0
    avg_conf = sum(confs) / len(confs)
    return round(avg_conf * 100.0, 1)


def _lip_sync_confidence(
    shot: dict[str, Any],
    transcript: dict[str, Any] | None,
) -> float | None:
    """Heuristic placeholder. Real lip-sync needs face-detect + viseme
    alignment (Phase 6.3). Until then: returns None when no transcript or
    no words in the shot's window; returns 0.5 when there are words (we
    have a *chance* of lip-sync but no proof)."""
    if not transcript:
        return None
    src_tc = _parse_tc(shot.get("source_timecode") or "0:00:00")
    dur = float(shot.get("duration_frames", 24)) / 24.0
    end_tc = src_tc + dur
    words = transcript.get("words") or []
    has_words = any(src_tc <= float(w.get("start_sec", 0)) < end_tc for w in words)
    return 0.5 if has_words else None


def _visual_style(source_profile: dict[str, Any] | None) -> str:
    if not source_profile:
        return "live_action"
    return str(source_profile.get("source_type", "live_action"))


def _audio_type(
    shot: dict[str, Any],
    transcript: dict[str, Any] | None,
    has_dialogue_in_window: bool,
) -> str:
    """Heuristic per-shot audio_type."""
    mood_tags = {t.lower() for t in (shot.get("mood_tags") or [])}
    if has_dialogue_in_window:
        return "dialogue_present"
    if any(h in mood_tags for h in DIALOGUE_HINTS):
        return "dialogue_present"
    if any(h in mood_tags for h in SFX_HINTS):
        return "sfx_only"
    return "scene_audio"  # default to source ambient


def _energy_zone_fit(clip_category: str) -> list[float]:
    """3-tuple [low, mid, high] derived from the category's energy_zone_affinity."""
    affinity = set(categories_for_zone("low") + categories_for_zone("mid") + categories_for_zone("high"))
    # Re-query per zone to know which ones include this category
    score: list[float] = []
    for zone_label in ("low", "mid", "high"):
        affined = set(categories_for_zone(zone_label))
        # Drops, buildups, breakdowns are special — we condense them to
        # high/low for this 3-way scalar fit
        if zone_label == "low":
            affined |= set(categories_for_zone("breakdown"))
        elif zone_label == "high":
            affined |= set(categories_for_zone("drop"))
        score.append(1.0 if clip_category in affined else 0.3)
    return [round(s, 3) for s in score]


def enrich_shot(
    shot: dict[str, Any],
    *,
    scene_data: dict[str, Any] | None = None,
    transcript: dict[str, Any] | None = None,
    source_profile: dict[str, Any] | None = None,
    source_motion_baseline: float | None = None,
) -> dict[str, Any]:
    """Return a copy of `shot` with the Phase 1.3 metadata fields populated.
    Idempotent: existing fields aren't overwritten unless they're missing.
    """
    out = dict(shot)
    if "emotional_register" not in out:
        out["emotional_register"] = _emotional_register(shot)
    if "clip_category" not in out:
        out["clip_category"] = _clip_category(shot, out["emotional_register"])
    if "action_intensity_pct" not in out:
        out["action_intensity_pct"] = _action_intensity_pct(
            shot, scene_data, source_motion_baseline,
        )
    if "dialogue_clarity_score" not in out:
        clarity = _dialogue_clarity_score(shot, transcript)
        if clarity is not None:
            out["dialogue_clarity_score"] = clarity
        else:
            out["dialogue_clarity_score"] = None
    if "lip_sync_confidence" not in out:
        lip = _lip_sync_confidence(shot, transcript)
        if lip is not None:
            out["lip_sync_confidence"] = lip
        else:
            out["lip_sync_confidence"] = None
    if "visual_style" not in out:
        out["visual_style"] = _visual_style(source_profile)
    if "audio_type" not in out:
        has_words = (
            out.get("dialogue_clarity_score") is not None
            and out["dialogue_clarity_score"] > 0
        )
        out["audio_type"] = _audio_type(shot, transcript, has_words)
    if "energy_zone_fit" not in out:
        out["energy_zone_fit"] = _energy_zone_fit(out["clip_category"])
    return out


def enrich_shot_list(
    shot_list: dict[str, Any],
    project_dir: Path,
) -> dict[str, Any]:
    """Walk every shot in a shot-list.json, enrich with Phase 1.3 metadata,
    return the new dict. Reads scene + transcript + source-profile data
    from the project's standard locations.
    """
    out = dict(shot_list)
    enriched_shots: list[dict[str, Any]] = []

    # Cache per-source data so we don't reload N times
    scenes_cache: dict[str, dict[str, Any]] = {}
    transcripts_cache: dict[str, dict[str, Any]] = {}
    profiles_cache: dict[str, dict[str, Any]] = {}
    motion_baselines: dict[str, float] = {}

    scenes_dir = project_dir / "data" / "scenes"
    transcripts_dir = project_dir / "data" / "transcripts"
    profiles_dir = project_dir / "data" / "source-profiles"

    def _load_json(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    for shot in shot_list.get("shots") or []:
        sid = shot.get("source_id") or ""
        safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", sid)
        if sid not in scenes_cache:
            scenes_cache[sid] = _load_json(scenes_dir / f"{sid}.json") or {}
            scenes = scenes_cache[sid].get("scenes") or []
            motions = [float(s.get("motion", 0)) for s in scenes if "motion" in s]
            motion_baselines[sid] = (
                sorted(motions)[len(motions) // 2] if motions else 0.0
            )
        if sid not in transcripts_cache:
            transcripts_cache[sid] = _load_json(transcripts_dir / f"{sid}.json") or {}
        if sid not in profiles_cache:
            profiles_cache[sid] = _load_json(profiles_dir / f"{safe_id}.json") or {}
            # Try the b2-hash style id too in case the catalog used a different id
            if not profiles_cache[sid]:
                # Iterate all profiles and try to match (best-effort)
                if profiles_dir.exists():
                    for p in profiles_dir.glob("*.json"):
                        d = _load_json(p) or {}
                        if d.get("source_id") == sid:
                            profiles_cache[sid] = d
                            break

        enriched = enrich_shot(
            shot,
            scene_data=scenes_cache.get(sid) or None,
            transcript=transcripts_cache.get(sid) or None,
            source_profile=profiles_cache.get(sid) or None,
            source_motion_baseline=motion_baselines.get(sid) or None,
        )
        enriched_shots.append(enriched)

    out["shots"] = enriched_shots
    return out


def coverage_report(shot_list: dict[str, Any]) -> dict[str, Any]:
    """Return % of shots that have each Phase 1.3 metadata field populated."""
    shots = shot_list.get("shots") or []
    n = len(shots) or 1
    fields = (
        "emotional_register", "clip_category", "action_intensity_pct",
        "dialogue_clarity_score", "lip_sync_confidence",
        "visual_style", "audio_type", "energy_zone_fit",
    )
    out: dict[str, Any] = {"total_shots": len(shots)}
    for f in fields:
        present = sum(
            1 for s in shots
            if f in s and s[f] is not None and s[f] != "" and s[f] != []
        )
        out[f] = round(present / n, 3)
    return out


__all__ = [
    "EMOTION_DIMS",
    "enrich_shot",
    "enrich_shot_list",
    "coverage_report",
]
