"""Lyric + song-point + narrative sync planner.

Ties together beat-map, (optional) lyric transcript, shot-list, and emotion-arc
into a single `sync-plan.json` that tells the renderer / NLE exporter which
shot should land on which song moment — respecting both musical structure
(drops, breakdowns, buildups, downbeats) and story progression (act ordering,
emotional continuity, fandom balance).

Design intent: these edits should feel like a story, not a clip salad. The
matcher scores every candidate shot against every song-point and picks the
combination that keeps the narrative arc coherent — earlier shots tend to
land in the first act of the song, intense shots land on drops, somber
lyrics pull melancholy shots.

All heuristic. No LLM calls. Fast enough to regenerate every render.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from fandomforge.validation import validate

logger = logging.getLogger(__name__)


# ---------- Lyric extraction ----------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_song_lyrics(
    song_path: Path,
    *,
    model_size: str = "small",
    language: str = "en",
) -> dict[str, Any] | None:
    """Run whisper on the song and return a transcript-schema dict.

    Uses the local whisper install if available. Returns None if whisper isn't
    installed — the caller is expected to degrade gracefully (lyrics are
    optional; song-points still work from beat-map alone).
    """
    try:
        import whisper  # type: ignore
    except ImportError:
        return None

    from fandomforge.ingest import WHISPER_CACHE

    try:
        model = whisper.load_model(model_size, download_root=str(WHISPER_CACHE))
        result = model.transcribe(
            str(song_path),
            language=language,
            verbose=False,
            word_timestamps=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("whisper on song failed: %s", exc)
        return None

    segments: list[dict[str, Any]] = []
    import math as _math

    for i, seg in enumerate(result.get("segments", [])):
        words_raw = seg.get("words") or []
        words: list[dict[str, Any]] = []
        for w in words_raw:
            prob = float(w.get("probability", 0.0))
            words.append({
                "word": str(w.get("word", "")).strip(),
                "start_sec": float(w.get("start", 0.0)),
                "end_sec": float(w.get("end", 0.0)),
                "confidence": max(0.0, min(1.0, prob)),
            })
        avg_logprob = float(seg.get("avg_logprob", 0.0))
        seg_conf = _math.exp(avg_logprob) if avg_logprob < 0 else avg_logprob
        segments.append({
            "id": i,
            "start_sec": float(seg["start"]),
            "end_sec": float(seg["end"]),
            "text": str(seg.get("text", "")).strip(),
            "confidence": max(0.0, min(1.0, seg_conf)),
            "words": words,
        })

    return {
        "schema_version": 1,
        "source_id": f"song:{song_path.stem}",
        "language": result.get("language", language),
        "model": f"whisper-{model_size}",
        "segments": segments,
        "generated_at": _now_iso(),
    }


# ---------- Emotion classification (heuristic lexicon) ----------


# Small sentiment lexicon — intentionally hand-tuned for edit-music emotional
# axes (not the usual NLP pos/neg). Grouped by emotion bucket.
_EMOTION_LEX: dict[str, tuple[str, ...]] = {
    "intense": (
        "fire", "burn", "rage", "fight", "war", "kill", "break", "run",
        "blood", "scream", "storm", "crash", "fall", "drop", "rise",
        "power", "control", "weapon", "destroy", "unleash", "chaos",
    ),
    "somber": (
        "alone", "lost", "gone", "empty", "tired", "cold", "silent",
        "grave", "dark", "rain", "tears", "goodbye", "broken", "forget",
        "regret", "miss", "fade", "end", "dead", "ghost",
    ),
    "hopeful": (
        "rise", "shine", "find", "hope", "light", "home", "new",
        "dream", "dawn", "start", "begin", "free", "fly", "love",
        "trust", "morning",
    ),
    "defiant": (
        "won't", "wont", "never", "stand", "back", "survive", "fight",
        "keep", "still", "again", "try", "push", "no", "stop", "mine",
        "broken", "risen",
    ),
    "tender": (
        "you", "hold", "warm", "close", "soft", "touch", "feel",
        "whisper", "heart", "eyes", "smile", "kiss", "stay",
    ),
}


def classify_emotion(text: str) -> str:
    """Return the best-match emotion bucket for a lyric line.

    Returns 'neutral' when no bucket scores above threshold. Ties break to the
    first bucket in the _EMOTION_LEX iteration order (intense → somber → ...).
    """
    if not text:
        return "neutral"
    tokens = set(re.findall(r"[a-zA-Z']+", text.lower()))
    scores: dict[str, int] = {bucket: 0 for bucket in _EMOTION_LEX}
    for bucket, keywords in _EMOTION_LEX.items():
        for kw in keywords:
            if kw in tokens:
                scores[bucket] += 1

    best_bucket = max(scores, key=lambda k: scores[k])
    return best_bucket if scores[best_bucket] > 0 else "neutral"


# ---------- Lyric segmentation ----------


@dataclass
class LyricSection:
    start_sec: float
    end_sec: float
    text: str
    emotion: str
    confidence: float


def _segment_from_transcript(transcript: dict[str, Any]) -> list[LyricSection]:
    """Collapse whisper segments into lyric sections ready for planning.

    Whisper's default segments are usually already near-phrase-level. We just
    enrich them with an emotion bucket and drop empties.
    """
    out: list[LyricSection] = []
    for seg in transcript.get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        out.append(
            LyricSection(
                start_sec=float(seg["start_sec"]),
                end_sec=float(seg["end_sec"]),
                text=text,
                emotion=classify_emotion(text),
                confidence=float(seg.get("confidence", 0.0)),
            )
        )
    return out


# ---------- Song-point derivation ----------


@dataclass
class SongPoint:
    id: str
    time_sec: float
    end_sec: float | None
    type: str  # lyric | drop | buildup | breakdown | downbeat | custom
    label: str
    emotion: str
    intensity: float


def derive_song_points(
    beat_map: dict[str, Any],
    lyric_sections: list[LyricSection],
    *,
    include_downbeats: bool = False,
) -> list[SongPoint]:
    """Build the unified song-point timeline from beat-map + lyrics.

    Lyric sections become `lyric` points. Drops/buildups/breakdowns become
    their own points. Downbeats are included when requested (busy — usually
    left out unless the caller wants tight per-bar sync).
    """
    points: list[SongPoint] = []

    for i, lyric in enumerate(lyric_sections):
        points.append(
            SongPoint(
                id=f"lyric_{i:03d}",
                time_sec=lyric.start_sec,
                end_sec=lyric.end_sec,
                type="lyric",
                label=lyric.text[:80],
                emotion=lyric.emotion,
                intensity=0.5,
            )
        )

    for i, drop in enumerate(beat_map.get("drops", []) or []):
        points.append(
            SongPoint(
                id=f"drop_{i:03d}",
                time_sec=float(drop.get("time", 0.0)),
                end_sec=None,
                type="drop",
                label=f"{drop.get('type', 'drop')} drop",
                emotion="intense",
                intensity=float(drop.get("intensity", 0.9)),
            )
        )

    for i, bu in enumerate(beat_map.get("buildups", []) or []):
        points.append(
            SongPoint(
                id=f"buildup_{i:03d}",
                time_sec=float(bu.get("start", 0.0)),
                end_sec=float(bu.get("end", 0.0)),
                type="buildup",
                label="buildup",
                emotion="defiant",
                intensity=0.7,
            )
        )

    for i, bd in enumerate(beat_map.get("breakdowns", []) or []):
        points.append(
            SongPoint(
                id=f"break_{i:03d}",
                time_sec=float(bd.get("start", 0.0)),
                end_sec=float(bd.get("end", 0.0)),
                type="breakdown",
                label="breakdown",
                emotion="somber",
                intensity=float(bd.get("intensity", 0.3)),
            )
        )

    if include_downbeats:
        for i, db_time in enumerate(beat_map.get("downbeats", []) or []):
            points.append(
                SongPoint(
                    id=f"db_{i:03d}",
                    time_sec=float(db_time),
                    end_sec=None,
                    type="downbeat",
                    label=f"downbeat {i + 1}",
                    emotion="neutral",
                    intensity=0.4,
                )
            )

    points.sort(key=lambda p: p.time_sec)
    return points


# ---------- Shot matching ----------


# Mapping from shot role / mood tags to the emotion buckets they suit.
_ROLE_EMOTION_HINT: dict[str, tuple[str, ...]] = {
    "hero": ("defiant", "hopeful"),
    "action": ("intense",),
    "reaction": ("tender", "somber"),
    "environment": ("somber", "neutral"),
    "detail": ("tender", "neutral"),
    "motion": ("intense",),
    "gaze": ("tender", "defiant"),
    "cut-on-action": ("intense",),
    "establishing": ("neutral",),
    "transition": ("neutral",),
    "insert": ("neutral",),
    "title": ("neutral",),
}


_MOOD_EMOTION_HINT: dict[str, str] = {
    "badass": "defiant",
    "intense": "intense",
    "combat": "intense",
    "action": "intense",
    "heroic": "defiant",
    "defiant": "defiant",
    "melancholy": "somber",
    "somber": "somber",
    "sad": "somber",
    "mourning": "somber",
    "hopeful": "hopeful",
    "triumph": "hopeful",
    "tender": "tender",
    "love": "tender",
    "calm": "neutral",
    "peaceful": "neutral",
    "flashback": "somber",
    "memory": "somber",
}


def _shot_emotion(shot: dict[str, Any]) -> str:
    """Infer the dominant emotion of a shot from its role + mood tags."""
    votes: dict[str, int] = {}
    role = (shot.get("role") or "").strip()
    for bucket in _ROLE_EMOTION_HINT.get(role, ()):
        votes[bucket] = votes.get(bucket, 0) + 1
    for tag in shot.get("mood_tags") or []:
        t = str(tag).lower().strip()
        if t in _MOOD_EMOTION_HINT:
            bucket = _MOOD_EMOTION_HINT[t]
            votes[bucket] = votes.get(bucket, 0) + 2
    if not votes:
        return "neutral"
    return max(votes, key=lambda k: votes[k])


def _emotion_match_score(song_emotion: str, shot_emotion: str) -> float:
    """How well two emotion buckets go together (0–1)."""
    if song_emotion == "neutral" or shot_emotion == "neutral":
        return 0.5
    if song_emotion == shot_emotion:
        return 1.0
    # Compatibility matrix — which moods rhyme with which.
    compat = {
        ("intense", "defiant"): 0.75,
        ("defiant", "intense"): 0.75,
        ("somber", "tender"): 0.7,
        ("tender", "somber"): 0.7,
        ("hopeful", "defiant"): 0.65,
        ("defiant", "hopeful"): 0.65,
        ("hopeful", "tender"): 0.6,
        ("tender", "hopeful"): 0.6,
    }
    return compat.get((song_emotion, shot_emotion), 0.25)


def _act_for_time(time_sec: float, song_duration: float) -> int:
    """Rough three-act mapping for a given song time."""
    if song_duration <= 0:
        return 1
    t = time_sec / song_duration
    if t < 0.33:
        return 1
    if t < 0.67:
        return 2
    return 3


def _act_alignment_score(shot_act: int, song_act: int) -> float:
    """Reward shots whose act matches the song section they'd land on."""
    diff = abs(int(shot_act) - int(song_act))
    if diff == 0:
        return 1.0
    if diff == 1:
        return 0.5
    return 0.15


def _intensity_score(song_intensity: float, shot: dict[str, Any]) -> float:
    """Match high-motion shots to high-intensity song points."""
    is_action = (shot.get("role") in ("action", "motion", "cut-on-action")) or bool(
        shot.get("cliche_flag")
    )
    shot_intensity = 0.85 if is_action else 0.35
    return 1.0 - abs(song_intensity - shot_intensity)


@dataclass
class MatchResult:
    shot_id: str
    score: float
    reasons: list[str] = field(default_factory=list)


def _duration_prior_score(shot: dict[str, Any], priors: dict[str, Any] | None) -> float:
    """Reward shots whose duration is close to the learned reference median.

    When reference-priors.json exists, shots matching the median duration
    ± 25% get the full bonus; far-out shots get less. With no priors, returns
    1.0 (neutral — no bias).
    """
    if not priors:
        return 1.0
    fps = 24.0
    shot_dur = float(shot.get("duration_frames") or 0) / fps
    target = float(priors.get("median_shot_duration_sec") or 2.0)
    if shot_dur <= 0 or target <= 0:
        return 0.5
    ratio = min(shot_dur, target) / max(shot_dur, target)
    return max(0.25, ratio)


def _score_shot_for_point(
    shot: dict[str, Any],
    point: SongPoint,
    *,
    song_duration: float,
    used_shots: set[str],
    priors: dict[str, Any] | None = None,
) -> MatchResult:
    reasons: list[str] = []
    shot_emotion = _shot_emotion(shot)
    shot_act = int(shot.get("act") or 1)
    song_act = _act_for_time(point.time_sec, song_duration)

    emo = _emotion_match_score(point.emotion, shot_emotion)
    act = _act_alignment_score(shot_act, song_act)
    intensity = _intensity_score(point.intensity, shot)

    narrative = 1.0
    sf = float(shot.get("start_frame") or 0)
    fps = 24.0
    shot_rel_time = sf / fps
    if song_duration > 0:
        delta = abs(shot_rel_time - point.time_sec) / song_duration
        narrative = max(0.0, 1.0 - delta * 0.75)

    duration_prior = _duration_prior_score(shot, priors)
    reuse_penalty = 0.3 if shot.get("id") in used_shots else 0.0

    score = (
        0.3 * emo
        + 0.2 * intensity
        + 0.15 * act
        + 0.2 * narrative
        + 0.15 * duration_prior
        - reuse_penalty
    )
    score = max(0.0, min(1.0, score))

    if emo >= 0.8:
        reasons.append(f"mood={shot_emotion} matches {point.emotion}")
    elif emo >= 0.6:
        reasons.append(f"mood={shot_emotion} pairs with {point.emotion}")
    if intensity >= 0.8:
        reasons.append("intensity-match")
    if act == 1.0:
        reasons.append(f"act {shot_act} lands in song-act {song_act}")
    if priors and duration_prior >= 0.85:
        reasons.append("duration matches reference priors")
    if reuse_penalty:
        reasons.append("already-used-earlier")

    return MatchResult(
        shot_id=str(shot.get("id") or ""),
        score=round(score, 3),
        reasons=reasons,
    )


def match_shots_to_song_points(
    song_points: list[SongPoint],
    shots: list[dict[str, Any]],
    *,
    song_duration: float,
    top_k: int = 3,
    priors: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """For each song-point, pick the top-k shots that best match.

    Tracks which shots have been picked so the score penalizes re-use
    — the matcher tells a story instead of looping the same shot.

    If `priors` (a reference-priors payload) is provided, the scorer biases
    toward shots whose duration matches the learned reference median.
    """
    used: set[str] = set()
    out: list[dict[str, Any]] = []
    priors_section: dict[str, Any] | None = None
    if priors and isinstance(priors.get("priors"), dict):
        priors_section = priors["priors"]

    for point in song_points:
        ranked = [
            _score_shot_for_point(
                shot, point, song_duration=song_duration,
                used_shots=used, priors=priors_section,
            )
            for shot in shots
            if shot.get("id")
        ]
        ranked.sort(key=lambda m: m.score, reverse=True)
        top = ranked[:top_k]
        if top:
            used.add(top[0].shot_id)

        pt_dict: dict[str, Any] = {
            "id": point.id,
            "time_sec": round(point.time_sec, 3),
            "type": point.type,
            "label": point.label,
            "emotion": point.emotion,
            "intensity": round(point.intensity, 3),
            "recommended_shots": [
                {"shot_id": m.shot_id, "score": m.score, "reasons": m.reasons}
                for m in top
                if m.shot_id
            ],
        }
        if point.end_sec is not None:
            pt_dict["end_sec"] = round(point.end_sec, 3)
        out.append(pt_dict)

    return out


# ---------- Entry point ----------


def build_sync_plan(
    *,
    project_slug: str,
    beat_map: dict[str, Any],
    shot_list: dict[str, Any],
    lyrics_transcript: dict[str, Any] | None = None,
    include_downbeats: bool = False,
    top_k: int = 3,
    reference_priors: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce a schema-valid sync-plan.json dict.

    When `reference_priors` is provided (loaded via
    `reference_library.load_priors`), the matcher biases toward shot durations
    typical of the reference corpus — closer to how real fandom edits cut.
    """
    song_duration = float(beat_map.get("duration_sec") or 0.0)
    lyric_sections: list[LyricSection] = (
        _segment_from_transcript(lyrics_transcript) if lyrics_transcript else []
    )
    song_points = derive_song_points(
        beat_map, lyric_sections, include_downbeats=include_downbeats
    )

    # When the corpus has enough S-tier edits, prefer their signature over
    # the all-videos average. Falls back gracefully when no tier breakdown
    # exists (unscored corpora, small corpora).
    effective_priors = reference_priors
    if reference_priors and isinstance(reference_priors.get("priors"), dict):
        tiered = reference_priors["priors"].get("s_tier_only")
        if isinstance(tiered, dict) and tiered.get("video_count", 0) >= 5:
            # Wrap as a full reference-priors envelope so the existing
            # consumer in match_shots_to_song_points works unchanged.
            effective_priors = {"priors": tiered, "tag": reference_priors.get("tag", "") + "+S-tier"}

    shots = shot_list.get("shots") or []
    point_dicts = match_shots_to_song_points(
        song_points,
        shots,
        song_duration=song_duration,
        top_k=top_k,
        priors=effective_priors,
    )

    plan: dict[str, Any] = {
        "schema_version": 1,
        "project_slug": project_slug,
        "song_title": str(beat_map.get("song") or ""),
        "lyrics": [
            {
                "start_sec": round(ls.start_sec, 3),
                "end_sec": round(ls.end_sec, 3),
                "text": ls.text,
                "emotion": ls.emotion,
                "confidence": round(ls.confidence, 3),
            }
            for ls in lyric_sections
        ],
        "song_points": point_dicts,
        "generated_at": _now_iso(),
        "generator": "ff sync plan",
    }
    validate(plan, "sync-plan")
    return plan


def write_sync_plan(plan: dict[str, Any], project_dir: Path) -> Path:
    """Persist sync-plan.json under <project>/data/."""
    out = project_dir / "data" / "sync-plan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return out


__all__ = [
    "LyricSection",
    "MatchResult",
    "SongPoint",
    "build_sync_plan",
    "classify_emotion",
    "derive_song_points",
    "extract_song_lyrics",
    "match_shots_to_song_points",
    "write_sync_plan",
]
