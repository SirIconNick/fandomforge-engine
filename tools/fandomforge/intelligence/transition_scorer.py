"""Transition quality scorer for adjacent shot pairs.

Given two ShotRecord instances (from shot_optimizer.EditPlan) plus their
enriched DB data, score how well they cut together across six visual and
editorial axes:

  1. Motion direction continuity
  2. Gaze direction continuity
  3. Luminance match
  4. Subject size match
  5. Same-source 3-in-a-row penalty
  6. Color palette temperature match

Scores are returned as a TransitionScore (0-1 overall, plus per-factor
breakdown). A SequenceScore covers the full shot list.

Usage in shot_optimizer:
  scored = _score_shot(...)
  scored += transition_scorer.score_transition(prev_shot_data, cand_shot_data).quality * 10
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Weight constants (tuned to fan-edit aesthetics)
# ---------------------------------------------------------------------------

_W_MOTION = 0.20
_W_GAZE = 0.18
_W_LUMINANCE = 0.20
_W_SUBJECT_SIZE = 0.15
_W_SOURCE_RUN = 0.15
_W_COLOR_TEMP = 0.12

assert abs(
    _W_MOTION + _W_GAZE + _W_LUMINANCE + _W_SUBJECT_SIZE + _W_SOURCE_RUN + _W_COLOR_TEMP - 1.0
) < 1e-6, "Transition weights must sum to 1.0"

# ---------------------------------------------------------------------------
# Lookup tables for scoring rules
# ---------------------------------------------------------------------------

# Motion: same direction = very smooth. Adjacent directions = ok.
# Opposite = jarring (but sometimes intentional -- we penalise but not hard-block).
_MOTION_COMPATIBILITY: dict[tuple[str, str], float] = {
    # Same direction
    ("left", "left"): 1.0,
    ("right", "right"): 1.0,
    ("up", "up"): 1.0,
    ("down", "down"): 1.0,
    ("static", "static"): 0.9,
    ("toward", "toward"): 0.9,
    ("away", "away"): 0.9,
    # Adjacent / complementary
    ("static", "left"): 0.7,
    ("static", "right"): 0.7,
    ("static", "up"): 0.7,
    ("static", "down"): 0.7,
    ("left", "static"): 0.7,
    ("right", "static"): 0.7,
    ("up", "static"): 0.7,
    ("down", "static"): 0.7,
    ("toward", "static"): 0.75,
    ("away", "static"): 0.75,
    # Somewhat complementary
    ("left", "up"): 0.55,
    ("left", "down"): 0.55,
    ("right", "up"): 0.55,
    ("right", "down"): 0.55,
    ("up", "left"): 0.55,
    ("up", "right"): 0.55,
    ("down", "left"): 0.55,
    ("down", "right"): 0.55,
    # Depth changes
    ("toward", "away"): 0.45,
    ("away", "toward"): 0.45,
    # Opposite horizontal/vertical: jarring but usable at drops
    ("left", "right"): 0.30,
    ("right", "left"): 0.30,
    ("up", "down"): 0.35,
    ("down", "up"): 0.35,
}

# Gaze: eyeline match. If A looks right, the next shot should show what's to
# the right -- or the subject being looked at from the left side.
_GAZE_COMPATIBILITY: dict[tuple[str, str], float] = {
    ("center", "center"): 1.0,
    ("center", "left"): 0.75,
    ("center", "right"): 0.75,
    ("center", "up"): 0.65,
    ("center", "down"): 0.65,
    ("center", "none"): 0.70,
    ("left", "center"): 0.75,
    ("right", "center"): 0.75,
    # Eye-line match: A looks right, cut to subject on left side = natural
    ("right", "left"): 0.90,
    ("left", "right"): 0.90,
    # Same gaze = continuous
    ("left", "left"): 0.80,
    ("right", "right"): 0.80,
    ("up", "up"): 0.80,
    ("down", "down"): 0.80,
    # Vertical eyeline
    ("up", "down"): 0.60,
    ("down", "up"): 0.60,
    ("up", "center"): 0.65,
    ("down", "center"): 0.65,
    ("up", "left"): 0.50,
    ("up", "right"): 0.50,
    ("down", "left"): 0.50,
    ("down", "right"): 0.50,
    # Off-screen
    ("off_screen", "center"): 0.55,
    ("center", "off_screen"): 0.55,
    ("off_screen", "off_screen"): 0.60,
    ("none", "none"): 0.65,
    ("none", "center"): 0.70,
    ("center", "none"): 0.70,
}

# Color temperature: cool-to-warm and warm-to-cool transitions are jarring unless
# separated by a neutral/desaturated buffer.
_COLOR_TEMP_GROUPS: dict[str, str] = {
    "teal-orange": "mixed",
    "warm": "warm",
    "cool": "cool",
    "desaturated": "neutral",
    "noir": "cool",
}

_COLOR_TEMP_COMPATIBILITY: dict[tuple[str, str], float] = {
    ("warm", "warm"): 1.0,
    ("cool", "cool"): 1.0,
    ("neutral", "neutral"): 1.0,
    ("mixed", "mixed"): 0.85,
    ("warm", "neutral"): 0.80,
    ("neutral", "warm"): 0.80,
    ("cool", "neutral"): 0.80,
    ("neutral", "cool"): 0.80,
    ("mixed", "warm"): 0.70,
    ("mixed", "cool"): 0.70,
    ("warm", "mixed"): 0.70,
    ("cool", "mixed"): 0.70,
    # Hard temp jump
    ("warm", "cool"): 0.40,
    ("cool", "warm"): 0.40,
}

# Lighting-derived luminance proxy
_LIGHTING_LUMINANCE: dict[str, float] = {
    "bright": 0.85,
    "daylight": 0.90,
    "dim": 0.30,
    "noir": 0.15,
    None: 0.50,  # type: ignore[misc]
}

# Shot size mapping from action/desc heuristics
_ACTION_SIZE_RANK: dict[str, int] = {
    "dead": 1,
    "unconscious": 1,
    "wounded": 2,
    "standing": 3,
    "walking": 3,
    "running": 4,
    "fighting": 4,
    "aiming": 2,
    "shooting": 3,
    "holding_gun": 2,
    "talking": 2,
    "pointing": 2,
    "listening": 2,
    "driving": 3,
    "reading": 2,
    None: 3,  # type: ignore[misc]
}

# ---------------------------------------------------------------------------
# ShotData: a dict-like view that captures everything the scorer needs
# ---------------------------------------------------------------------------


@dataclass
class ShotData:
    """Minimal shot descriptor for transition scoring.

    Can be constructed from a ShotRecord or any dict-like source. All fields
    that are unavailable default to None/unknown.

    Attributes:
        shot_id: Library row ID.
        source: Source clip identifier.
        lighting: Lighting tag from the shot library.
        color_palette: Color palette tag.
        action: Action tag.
        emotion: Emotion tag.
        motion_dir: Motion direction from motion_flow analysis.
        motion_kind: Camera/subject/mixed/none.
        gaze_dir: Gaze direction from gaze_detector.
    """

    shot_id: int = -1
    source: str = ""
    lighting: str | None = None
    color_palette: str | None = None
    action: str | None = None
    emotion: str | None = None
    motion_dir: str | None = None
    motion_kind: str | None = None
    gaze_dir: str | None = None

    @classmethod
    def from_shot_record(cls, record: Any) -> "ShotData":
        """Build ShotData from a ShotRecord (shot_optimizer dataclass).

        Falls back gracefully when motion/gaze fields are absent (pre-analysis).
        """
        return cls(
            shot_id=getattr(record, "shot_library_id", -1),
            source=getattr(record, "source", ""),
            lighting=None,
            color_palette=None,
            action=getattr(record, "action", None),
            emotion=getattr(record, "emotion", None),
            motion_dir=getattr(record, "motion_dir", None),
            motion_kind=getattr(record, "motion_kind", None),
            gaze_dir=getattr(record, "gaze_dir", None),
        )

    @classmethod
    def from_db_row(cls, row: dict[str, Any]) -> "ShotData":
        """Build ShotData from a sqlite3 Row or dict.

        Args:
            row: Dict with keys matching the shots table columns.
        """
        return cls(
            shot_id=row.get("id", -1),
            source=row.get("source", ""),
            lighting=row.get("lighting"),
            color_palette=row.get("color_palette"),
            action=row.get("action"),
            emotion=row.get("emotion"),
            motion_dir=row.get("motion_dir"),
            motion_kind=row.get("motion_kind"),
            gaze_dir=row.get("gaze_dir"),
        )


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TransitionScore:
    """Transition quality between two adjacent shots.

    Attributes:
        quality: Overall score in [0, 1]. Higher is better.
        motion: Motion continuity sub-score.
        gaze: Gaze continuity sub-score.
        luminance: Luminance match sub-score.
        subject_size: Subject size match sub-score.
        source_run: Same-source run penalty sub-score.
        color_temp: Color temperature match sub-score.
        notes: Human-readable explanation of the dominant factor.
    """

    quality: float
    motion: float
    gaze: float
    luminance: float
    subject_size: float
    source_run: float
    color_temp: float
    notes: str = ""

    def summary(self) -> str:
        """Short formatted summary line for logging and storyboard display."""
        return (
            f"quality={self.quality:.2f}  "
            f"motion={self.motion:.2f}  gaze={self.gaze:.2f}  "
            f"lum={self.luminance:.2f}  size={self.subject_size:.2f}  "
            f"src={self.source_run:.2f}  color={self.color_temp:.2f}"
        )


@dataclass
class PerTransitionEntry:
    """One scored pair within a SequenceScore.

    Attributes:
        from_index: cut_index of the outgoing shot.
        to_index: cut_index of the incoming shot.
        score: TransitionScore for this pair.
    """

    from_index: int
    to_index: int
    score: TransitionScore


@dataclass
class SequenceScore:
    """Aggregate transition quality for a whole shot sequence.

    Attributes:
        overall: Mean quality across all transitions (0-1).
        per_transition: Individual scored pairs.
        weakest: The PerTransitionEntry with the lowest quality.
        strongest: The PerTransitionEntry with the highest quality.
    """

    overall: float
    per_transition: list[PerTransitionEntry]
    weakest: PerTransitionEntry | None = None
    strongest: PerTransitionEntry | None = None


# ---------------------------------------------------------------------------
# Individual factor scorers
# ---------------------------------------------------------------------------


def _score_motion(a: ShotData, b: ShotData) -> float:
    """Score motion direction continuity between two shots.

    Args:
        a: Outgoing shot data.
        b: Incoming shot data.

    Returns:
        Score in [0, 1].
    """
    dir_a = a.motion_dir or "static"
    dir_b = b.motion_dir or "static"
    key = (dir_a, dir_b)
    if key in _MOTION_COMPATIBILITY:
        return _MOTION_COMPATIBILITY[key]
    # Symmetric lookup
    rev = (dir_b, dir_a)
    if rev in _MOTION_COMPATIBILITY:
        return _MOTION_COMPATIBILITY[rev]
    return 0.50  # unknown combination, neutral


def _score_gaze(a: ShotData, b: ShotData) -> float:
    """Score gaze direction continuity (eyeline match).

    Args:
        a: Outgoing shot data.
        b: Incoming shot data.

    Returns:
        Score in [0, 1].
    """
    g_a = a.gaze_dir or "none"
    g_b = b.gaze_dir or "none"
    key = (g_a, g_b)
    if key in _GAZE_COMPATIBILITY:
        return _GAZE_COMPATIBILITY[key]
    rev = (g_b, g_a)
    if rev in _GAZE_COMPATIBILITY:
        return _GAZE_COMPATIBILITY[rev]
    return 0.60


def _score_luminance(a: ShotData, b: ShotData) -> float:
    """Score luminance continuity from lighting tags.

    A large brightness jump (dark to bright or vice versa) is penalised.

    Args:
        a: Outgoing shot data.
        b: Incoming shot data.

    Returns:
        Score in [0, 1].
    """
    lum_a = _LIGHTING_LUMINANCE.get(a.lighting, 0.50)
    lum_b = _LIGHTING_LUMINANCE.get(b.lighting, 0.50)
    diff = abs(lum_a - lum_b)
    # diff 0 = 1.0, diff 0.5 = 0.5, diff 1.0 = 0.0 (linear)
    return round(1.0 - diff, 4)


def _score_subject_size(a: ShotData, b: ShotData) -> float:
    """Score subject size continuity from action tags.

    Extreme size jumps (extreme close-up to wide) are penalised unless
    intentional (we flag them as low scoring for the optimizer to weigh).

    Args:
        a: Outgoing shot data.
        b: Incoming shot data.

    Returns:
        Score in [0, 1].
    """
    size_a = _ACTION_SIZE_RANK.get(a.action, 3)
    size_b = _ACTION_SIZE_RANK.get(b.action, 3)
    delta = abs(size_a - size_b)
    # 0 = 1.0, 1 = 0.85, 2 = 0.60, 3 = 0.30, 4+ = 0.10
    scores = {0: 1.0, 1: 0.85, 2: 0.60, 3: 0.30}
    return scores.get(delta, 0.10)


def _score_source_run(a: ShotData, b: ShotData, recent_sources: list[str]) -> float:
    """Penalise same-source runs.

    Three or more consecutive shots from the same source reduce variety.

    Args:
        a: Outgoing shot data (the shot just before b).
        b: Incoming shot (candidate).
        recent_sources: Ordered list of sources of the last N shots (most recent last).
            Should include a.source at the end.

    Returns:
        Score in [0, 1]. 1.0 = no penalty, lower = penalised.
    """
    if a.source != b.source:
        return 1.0

    # They match -- check how long the current run is
    run = 1
    for src in reversed(recent_sources):
        if src == b.source:
            run += 1
        else:
            break

    if run == 1:
        return 0.80
    if run == 2:
        return 0.55
    # 3+ in a row: heavily penalised
    return 0.10


def _score_color_temp(a: ShotData, b: ShotData) -> float:
    """Score color temperature compatibility.

    Cool-to-warm and warm-to-cool jumps are penalised. Neutral palette
    acts as a buffer.

    Args:
        a: Outgoing shot data.
        b: Incoming shot data.

    Returns:
        Score in [0, 1].
    """
    temp_a = _COLOR_TEMP_GROUPS.get(a.color_palette or "", "neutral")
    temp_b = _COLOR_TEMP_GROUPS.get(b.color_palette or "", "neutral")
    key = (temp_a, temp_b)
    if key in _COLOR_TEMP_COMPATIBILITY:
        return _COLOR_TEMP_COMPATIBILITY[key]
    rev = (temp_b, temp_a)
    if rev in _COLOR_TEMP_COMPATIBILITY:
        return _COLOR_TEMP_COMPATIBILITY[rev]
    return 0.70


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_transition(
    shot_a: ShotData,
    shot_b: ShotData,
    recent_sources: list[str] | None = None,
) -> TransitionScore:
    """Score the cut quality between two adjacent shots.

    All six factors are weighted and summed into an overall quality score.
    Notes describe the dominant issue if quality is below 0.5.

    Args:
        shot_a: Outgoing shot data.
        shot_b: Incoming shot data.
        recent_sources: Optional list of recent source identifiers (most
            recent last), used for the source-run penalty. If not provided,
            only the immediate A->B pair is considered.

    Returns:
        TransitionScore with quality in [0, 1] and per-factor breakdown.
    """
    if recent_sources is None:
        recent_sources = [shot_a.source]

    motion = _score_motion(shot_a, shot_b)
    gaze = _score_gaze(shot_a, shot_b)
    luminance = _score_luminance(shot_a, shot_b)
    subject_size = _score_subject_size(shot_a, shot_b)
    source_run = _score_source_run(shot_a, shot_b, recent_sources)
    color_temp = _score_color_temp(shot_a, shot_b)

    quality = (
        _W_MOTION * motion
        + _W_GAZE * gaze
        + _W_LUMINANCE * luminance
        + _W_SUBJECT_SIZE * subject_size
        + _W_SOURCE_RUN * source_run
        + _W_COLOR_TEMP * color_temp
    )
    quality = round(min(max(quality, 0.0), 1.0), 4)

    # Build a short diagnostic note for the weakest factor
    factor_scores = {
        "motion": (motion, _W_MOTION),
        "gaze": (gaze, _W_GAZE),
        "luminance": (luminance, _W_LUMINANCE),
        "size": (subject_size, _W_SUBJECT_SIZE),
        "source_run": (source_run, _W_SOURCE_RUN),
        "color": (color_temp, _W_COLOR_TEMP),
    }
    weakest_factor = min(factor_scores, key=lambda k: factor_scores[k][0])
    weakest_val = factor_scores[weakest_factor][0]
    notes = ""
    if quality < 0.55:
        notes = f"weak: {weakest_factor}={weakest_val:.2f}"
    elif quality >= 0.80:
        notes = "smooth cut"

    return TransitionScore(
        quality=quality,
        motion=round(motion, 4),
        gaze=round(gaze, 4),
        luminance=round(luminance, 4),
        subject_size=round(subject_size, 4),
        source_run=round(source_run, 4),
        color_temp=round(color_temp, 4),
        notes=notes,
    )


def score_sequence(shots: list[ShotData]) -> SequenceScore:
    """Score all adjacent pairs in a shot sequence.

    Args:
        shots: Ordered list of ShotData instances representing the full sequence.

    Returns:
        SequenceScore with overall quality and per-transition breakdown.
    """
    if len(shots) < 2:
        return SequenceScore(
            overall=1.0,
            per_transition=[],
            weakest=None,
            strongest=None,
        )

    transitions: list[PerTransitionEntry] = []
    recent_sources: list[str] = []

    for i in range(len(shots) - 1):
        a = shots[i]
        b = shots[i + 1]

        ts = score_transition(a, b, recent_sources=list(recent_sources))
        entry = PerTransitionEntry(
            from_index=i,
            to_index=i + 1,
            score=ts,
        )
        transitions.append(entry)
        recent_sources.append(a.source)

    overall = sum(e.score.quality for e in transitions) / len(transitions)

    weakest = min(transitions, key=lambda e: e.score.quality)
    strongest = max(transitions, key=lambda e: e.score.quality)

    return SequenceScore(
        overall=round(overall, 4),
        per_transition=transitions,
        weakest=weakest,
        strongest=strongest,
    )


def transition_cost(shot_a: ShotData, shot_b: ShotData, recent_sources: list[str] | None = None) -> float:
    """Return a cost value in [0, 1] where 0 is perfect and 1 is terrible.

    Convenience wrapper for use inside shot_optimizer scoring loops.
    cost = 1 - quality.

    Args:
        shot_a: Outgoing shot data.
        shot_b: Incoming shot data.
        recent_sources: Optional recent-sources list.

    Returns:
        Cost in [0, 1].
    """
    ts = score_transition(shot_a, shot_b, recent_sources=recent_sources)
    return round(1.0 - ts.quality, 4)
