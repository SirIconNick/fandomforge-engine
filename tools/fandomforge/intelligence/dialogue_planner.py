"""Dialogue planner for FandomForge.

Specialised VO placement that refines the initial pass from shot_optimizer.
Reads the full edit plan and re-assigns dialogue cues according to rules
derived from 146-reference statistical analysis (stored in .style-template.json).

Rules enforced:
- Total VO coverage target: 22-28% of edit duration. The planner aims for the
  exact median from the style_profile (default 25.4%) rather than just any
  value inside the range.
- VO starts 2-4 frames BEFORE the cut to the shot it plays over. Placement is
  anchored at ``cut_in_time - pre_cut_frames / fps``, where pre_cut_frames is
  drawn uniformly from [2, 4].
- VO must land on shots where character_speaks is False.
- VO must NOT overlap any section whose is_drop is True or whose mood is "peak".
  Drop moments from the SongStructure are also used as hard exclusion zones.
- No back-to-back VO lines. At least one shot gap must separate consecutive
  placements.
- Short lines (< 2 s) may cover a single shot; long lines (3-5 s) may span
  1-2 consecutive silent shots.
- When the target coverage cannot be reached with single-shot placements, the
  planner activates span mode: a long cue is placed at the start of a valid
  anchor shot and allowed to run across the next silent non-peak shot.

Usage::

    from fandomforge.intelligence.dialogue_planner import plan_dialogue
    from fandomforge.intelligence.shot_optimizer import EditPlan, DialogueCue
    from fandomforge.intelligence.song_structure import SongStructure

    placements = plan_dialogue(
        edit_plan=my_edit_plan,
        song_structure=song,
        available_cues=my_cues,
        style_profile=profile,
    )
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .shot_optimizer import (
    DialogueCue,
    EditPlan,
    ShotRecord,
    _compute_vo_coverage,
    _section_at_time,
)
from .song_structure import SongStructure

logger = logging.getLogger(__name__)

# Frames per second assumed for pre-cut offset calculation.
_FPS: int = 24

# Overlap-exclusion window around each drop moment (seconds each side).
_DROP_EXCLUSION_PAD: float = 0.5

# Short / long line boundary in seconds.
_SHORT_LINE_MAX: float = 2.0
_LONG_LINE_MIN: float = 3.0

# Default VO coverage target when style_profile has no value.
_VO_COVERAGE_DEFAULT: float = 0.254

# Tolerable deviation from the exact coverage target before we stop trying
# to add more cues (as a fraction of total duration).
_COVERAGE_TOLERANCE: float = 0.01


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------


@dataclass
class DialoguePlacement:
    """A single refined VO cue placement produced by the dialogue planner.

    Attributes:
        cue_wav_path: Absolute path to the WAV file.
        start_sec: Timeline position where audio playback begins.
        end_sec: Timeline position where audio playback ends.
        duck_db: How many dB to duck the music under this cue. Positive value.
        gain_db: Additional gain applied to the dialogue before ducking.
        shot_indexes_covered: Cut-index list of every shot this cue overlaps.
        expected_line: Human-readable transcript of the dialogue line.
        pre_cut_frames: How many frames before the anchor shot's cut-in this
            cue ends. Value in [2, 4].
    """

    cue_wav_path: str
    start_sec: float
    end_sec: float
    duck_db: float
    gain_db: float
    shot_indexes_covered: list[int]
    expected_line: str
    pre_cut_frames: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drop_exclusion_zones(song: SongStructure) -> list[tuple[float, float]]:
    """Build a list of (start, end) windows that are off-limits for VO.

    Includes drop moments +/- _DROP_EXCLUSION_PAD and every section whose
    mood is 'peak' or whose is_drop flag is True.

    Args:
        song: Analysed SongStructure.

    Returns:
        Sorted, merged list of (start, end) exclusion windows in seconds.
    """
    zones: list[tuple[float, float]] = []

    for moment in song.drop_moments:
        zones.append((
            max(0.0, moment - _DROP_EXCLUSION_PAD),
            moment + _DROP_EXCLUSION_PAD,
        ))

    for section in song.sections:
        if section.mood == "peak" or section.is_drop:
            zones.append((section.start_time, section.end_time))

    # Merge overlapping zones.
    zones.sort()
    merged: list[tuple[float, float]] = []
    for start, end in zones:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _overlaps_exclusion(
    start: float,
    end: float,
    exclusion_zones: list[tuple[float, float]],
) -> bool:
    """Return True if [start, end] overlaps any exclusion zone.

    Args:
        start: Interval start in seconds.
        end: Interval end in seconds.
        exclusion_zones: Merged list from _drop_exclusion_zones().

    Returns:
        True when overlap exists.
    """
    for zone_start, zone_end in exclusion_zones:
        if start < zone_end and end > zone_start:
            return True
    return False


def _shot_is_eligible(
    shot: ShotRecord,
    song: SongStructure,
    exclusion_zones: list[tuple[float, float]],
) -> bool:
    """Return True when a shot can host a VO cue.

    Eligibility requirements:
    - character_speaks is False.
    - Shot does not fall inside an exclusion zone.
    - Song section at the shot start is not 'peak' and is not a drop.
    - Section label is one of: intro, verse, pre-chorus, breakdown.

    Args:
        shot: ShotRecord to evaluate.
        song: SongStructure for section lookup.
        exclusion_zones: Pre-computed exclusion windows.

    Returns:
        True if eligible.
    """
    if shot.character_speaks:
        return False

    shot_end = shot.start_time + shot.duration
    if _overlaps_exclusion(shot.start_time, shot_end, exclusion_zones):
        return False

    section = _section_at_time(shot.start_time, song.sections)
    if section is None:
        return False
    if section.mood == "peak" or section.is_drop:
        return False
    if section.label not in ("intro", "verse", "pre-chorus", "breakdown"):
        return False

    return True


def _anchor_placement(
    cue: DialogueCue,
    anchor_shot: ShotRecord,
    pre_cut_frames: int,
) -> tuple[float, float]:
    """Compute VO start/end times anchored to a shot's cut-in.

    The VO is placed so it ends ``pre_cut_frames / FPS`` seconds before the
    shot's cut-in point (start_time). Playback begins ``duration_sec`` before
    that anchored end.

    Args:
        cue: The dialogue cue to place.
        anchor_shot: The shot the VO is anchored to.
        pre_cut_frames: Frames before cut-in where VO ends (2-4).

    Returns:
        Tuple of (start_sec, end_sec).
    """
    pre_cut_sec = pre_cut_frames / _FPS
    anchor_time = anchor_shot.start_time
    end_sec = anchor_time - pre_cut_sec
    start_sec = end_sec - cue.duration_sec
    return round(start_sec, 4), round(end_sec, 4)


def _span_available_sec(
    anchor_shot: ShotRecord,
    shots: list[ShotRecord],
    song: SongStructure,
    exclusion_zones: list[tuple[float, float]],
    pre_cut_frames: int,
) -> tuple[float, list[int]]:
    """Compute how many seconds of eligible span start at anchor_shot.

    Walks forward from anchor_shot through consecutive eligible shots to find
    total silent room for a spanning VO placement.

    Args:
        anchor_shot: First shot in the potential span.
        shots: Full ordered shot list.
        song: SongStructure for section checks.
        exclusion_zones: Pre-computed exclusion windows.
        pre_cut_frames: Frames before final cut-out to reserve.

    Returns:
        Tuple of (available_seconds, list_of_covered_cut_indexes).
    """
    covered_indexes: list[int] = [anchor_shot.cut_index]
    span_end = anchor_shot.start_time + anchor_shot.duration

    # Walk forward through immediately following eligible shots.
    anchor_order = next(
        (i for i, s in enumerate(shots) if s.cut_index == anchor_shot.cut_index),
        None,
    )
    if anchor_order is None:
        reserve = pre_cut_frames / _FPS
        available = max(0.0, span_end - anchor_shot.start_time - reserve)
        return round(available, 4), covered_indexes

    j = anchor_order + 1
    while j < len(shots):
        next_shot = shots[j]
        if _shot_is_eligible(next_shot, song, exclusion_zones):
            covered_indexes.append(next_shot.cut_index)
            span_end = next_shot.start_time + next_shot.duration
            j += 1
            # One extra shot is enough for most long lines (3-5 s).
            if len(covered_indexes) >= 2:
                break
        else:
            break

    reserve = pre_cut_frames / _FPS
    available = max(0.0, span_end - anchor_shot.start_time - reserve)
    return round(available, 4), covered_indexes


# ---------------------------------------------------------------------------
# Duck / gain helpers
# ---------------------------------------------------------------------------


def _duck_db_for_cue(cue: DialogueCue, style_profile: dict[str, Any]) -> float:
    """Return how many dB the music should be ducked under this cue.

    Base duck is -8 dB for short lines, -10 dB for long lines. If the style
    profile carries a 'vo_duck_db' key, that overrides the default.

    Args:
        cue: DialogueCue being placed.
        style_profile: Dict from .style-template.json.

    Returns:
        Positive dB value for the duck amount.
    """
    default_duck = 8.0 if cue.duration_sec < _SHORT_LINE_MAX else 10.0
    return float(style_profile.get("vo_duck_db", default_duck))


def _gain_db_for_cue(cue: DialogueCue, style_profile: dict[str, Any]) -> float:
    """Return how many dB to boost the dialogue level.

    Default 0 dB (no boost). If style_profile carries 'vo_gain_db', use that.

    Args:
        cue: DialogueCue being placed.
        style_profile: Dict from .style-template.json.

    Returns:
        Gain in dB (may be 0).
    """
    return float(style_profile.get("vo_gain_db", 0.0))


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------


def plan_dialogue(
    edit_plan: EditPlan,
    song_structure: SongStructure,
    available_cues: list[DialogueCue],
    style_profile: dict[str, Any],
    *,
    seed: int | None = None,
) -> list[DialoguePlacement]:
    """Produce refined VO placements for an existing edit plan.

    This is a complete re-pass over the shot list. It discards any VO
    placements the shot_optimizer already made and produces a fresh ordered
    list that obeys all statistical rules from the reference analysis.

    Algorithm:
    1. Compute VO coverage target from style_profile.
    2. Build drop/peak exclusion zones from the SongStructure.
    3. Filter the shot list to eligible anchor candidates.
    4. Assign cues in a single pass:
       a. Short cues (< 2 s) get single-shot placement.
       b. Long cues (>= 3 s) try to span 1-2 shots.
    5. Enforce no back-to-back rule by tracking the last shot index used.
    6. If coverage falls below the low bound after the first pass, activate
       span mode: try every remaining eligible shot as a span anchor.
    7. Return the final DialoguePlacement list sorted by start_sec.

    Args:
        edit_plan: Completed EditPlan from shot_optimizer.plan_edit().
        song_structure: Analysed SongStructure for section/drop data.
        available_cues: Candidate DialogueCue list. May include cues that
            were already used in edit_plan.dialogue_placements; this function
            treats them as a fresh pool.
        style_profile: Dict loaded from .style-template.json. Reads:
            - vo_coverage_pct_median (float, percentage)
            - vo_duck_db (optional float)
            - vo_gain_db (optional float)
        seed: Optional RNG seed for reproducibility.

    Returns:
        Sorted list of DialoguePlacement instances.

    Raises:
        ValueError: If edit_plan contains no shots.
    """
    shots = edit_plan.shots
    if not shots:
        raise ValueError("edit_plan contains no shots.")

    rng = random.Random(seed)
    total_duration = edit_plan.metadata.total_duration_sec

    # --- Coverage target ---------------------------------------------------
    raw_pct = float(style_profile.get("vo_coverage_pct_median", _VO_COVERAGE_DEFAULT * 100))
    coverage_target = raw_pct / 100.0
    coverage_low = 0.22
    coverage_high = 0.28
    target_sec = total_duration * coverage_target

    logger.info(
        "Dialogue planner: target coverage %.1f%% (%.1f s of %.1f s total)",
        coverage_target * 100,
        target_sec,
        total_duration,
    )

    # --- Exclusion zones ---------------------------------------------------
    exclusion_zones = _drop_exclusion_zones(song_structure)

    # --- Eligible shots ----------------------------------------------------
    eligible = [
        s for s in shots
        if _shot_is_eligible(s, song_structure, exclusion_zones)
    ]

    if not eligible:
        logger.warning("No eligible shots found for VO placement.")
        return []

    # --- Cue pool ----------------------------------------------------------
    remaining_cues: list[DialogueCue] = list(available_cues)
    rng.shuffle(remaining_cues)

    placements: list[DialoguePlacement] = []
    used_cut_set: set[int] = set()
    last_placed_cut_index: int = -999

    def current_coverage() -> float:
        if not placements:
            return 0.0
        intervals = sorted((p.start_sec, p.end_sec) for p in placements)
        merged: list[tuple[float, float]] = []
        cs, ce = intervals[0]
        for s, e in intervals[1:]:
            if s <= ce:
                ce = max(ce, e)
            else:
                merged.append((cs, ce))
                cs, ce = s, e
        merged.append((cs, ce))
        covered = sum(e - s for s, e in merged)
        return min(covered / total_duration, 1.0)

    # --- First pass: single-shot and short-span placements ----------------
    for anchor_shot in eligible:
        if not remaining_cues:
            break

        cov = current_coverage()
        if cov >= coverage_target + _COVERAGE_TOLERANCE:
            break

        cut_idx = anchor_shot.cut_index

        # Skip if already hosting VO or if it's adjacent to the last placed shot.
        if cut_idx in used_cut_set:
            continue
        if cut_idx <= last_placed_cut_index + 1:
            continue

        # Pick a cue that fits within the shot's available window.
        pre_cut_frames = rng.randint(2, 4)
        shot_available = anchor_shot.duration - (pre_cut_frames / _FPS)

        chosen_cue: DialogueCue | None = None
        for cue in remaining_cues:
            if cue.duration_sec <= shot_available and cue.duration_sec <= _SHORT_LINE_MAX:
                chosen_cue = cue
                break

        if chosen_cue is None:
            # Try a long cue via span mode on this shot.
            span_sec, span_indexes = _span_available_sec(
                anchor_shot, shots, song_structure, exclusion_zones, pre_cut_frames
            )
            for cue in remaining_cues:
                if _LONG_LINE_MIN <= cue.duration_sec <= span_sec:
                    chosen_cue = cue
                    if chosen_cue is not None:
                        start_sec = anchor_shot.start_time
                        end_sec = start_sec + chosen_cue.duration_sec
                        placements.append(DialoguePlacement(
                            cue_wav_path=chosen_cue.audio_path,
                            start_sec=round(start_sec, 4),
                            end_sec=round(end_sec, 4),
                            duck_db=_duck_db_for_cue(chosen_cue, style_profile),
                            gain_db=_gain_db_for_cue(chosen_cue, style_profile),
                            shot_indexes_covered=list(span_indexes),
                            expected_line=chosen_cue.expected_line,
                            pre_cut_frames=pre_cut_frames,
                        ))
                        used_cut_set.update(span_indexes)
                        last_placed_cut_index = max(span_indexes)
                        remaining_cues.remove(chosen_cue)
                    break
            continue

        # Single-shot placement.
        start_sec, end_sec = _anchor_placement(chosen_cue, anchor_shot, pre_cut_frames)

        # Validate that the computed window is still inside the edit and
        # does not fall into an exclusion zone.
        if start_sec < 0.0:
            start_sec = 0.0
            end_sec = start_sec + chosen_cue.duration_sec

        if _overlaps_exclusion(start_sec, end_sec, exclusion_zones):
            continue

        placements.append(DialoguePlacement(
            cue_wav_path=chosen_cue.audio_path,
            start_sec=round(start_sec, 4),
            end_sec=round(end_sec, 4),
            duck_db=_duck_db_for_cue(chosen_cue, style_profile),
            gain_db=_gain_db_for_cue(chosen_cue, style_profile),
            shot_indexes_covered=[cut_idx],
            expected_line=chosen_cue.expected_line,
            pre_cut_frames=pre_cut_frames,
        ))
        used_cut_set.add(cut_idx)
        last_placed_cut_index = cut_idx
        remaining_cues.remove(chosen_cue)

    # --- Second pass: span mode when below coverage low bound -------------
    if current_coverage() < coverage_low and remaining_cues:
        logger.info(
            "Coverage at %.1f%% below low bound %.1f%%. Activating span mode.",
            current_coverage() * 100,
            coverage_low * 100,
        )
        for anchor_shot in eligible:
            if not remaining_cues:
                break
            if current_coverage() >= coverage_target + _COVERAGE_TOLERANCE:
                break

            cut_idx = anchor_shot.cut_index
            if cut_idx in used_cut_set:
                continue
            if cut_idx <= last_placed_cut_index + 1:
                continue

            pre_cut_frames = rng.randint(2, 4)
            span_sec, span_indexes = _span_available_sec(
                anchor_shot, shots, song_structure, exclusion_zones, pre_cut_frames
            )

            chosen_cue = None
            for cue in remaining_cues:
                if cue.duration_sec <= span_sec:
                    chosen_cue = cue
                    break

            if chosen_cue is None:
                continue

            start_sec = anchor_shot.start_time
            end_sec = start_sec + chosen_cue.duration_sec

            if _overlaps_exclusion(start_sec, end_sec, exclusion_zones):
                continue

            placements.append(DialoguePlacement(
                cue_wav_path=chosen_cue.audio_path,
                start_sec=round(start_sec, 4),
                end_sec=round(end_sec, 4),
                duck_db=_duck_db_for_cue(chosen_cue, style_profile),
                gain_db=_gain_db_for_cue(chosen_cue, style_profile),
                shot_indexes_covered=list(span_indexes),
                expected_line=chosen_cue.expected_line,
                pre_cut_frames=pre_cut_frames,
            ))
            used_cut_set.update(span_indexes)
            last_placed_cut_index = max(span_indexes)
            remaining_cues.remove(chosen_cue)

    placements.sort(key=lambda p: p.start_sec)

    final_cov = current_coverage()
    logger.info(
        "Dialogue planner complete: %d cues placed, %.1f%% coverage "
        "(target %.1f%%, range [%.1f%%, %.1f%%])",
        len(placements),
        final_cov * 100,
        coverage_target * 100,
        coverage_low * 100,
        coverage_high * 100,
    )
    if final_cov < coverage_low:
        logger.warning(
            "VO coverage %.1f%% is below low bound %.1f%%. "
            "Insufficient eligible cues or shots.",
            final_cov * 100,
            coverage_low * 100,
        )
    elif final_cov > coverage_high:
        logger.warning(
            "VO coverage %.1f%% exceeds high bound %.1f%%. "
            "Consider trimming cues.",
            final_cov * 100,
            coverage_high * 100,
        )

    return placements


# ---------------------------------------------------------------------------
# Human-readable summary
# ---------------------------------------------------------------------------


def print_dialogue_plan(placements: list[DialoguePlacement], total_sec: float) -> None:
    """Print a readable summary of dialogue placements to stdout.

    Args:
        placements: Output of plan_dialogue().
        total_sec: Total edit duration for coverage calculation.
    """
    def fmt(t: float) -> str:
        m = int(t) // 60
        s = t - m * 60
        return f"{m}:{s:05.2f}"

    bar = "=" * 72
    thin = "-" * 72

    covered = 0.0
    intervals = sorted((p.start_sec, p.end_sec) for p in placements)
    if intervals:
        merged: list[tuple[float, float]] = []
        cs, ce = intervals[0]
        for s, e in intervals[1:]:
            if s <= ce:
                ce = max(ce, e)
            else:
                merged.append((cs, ce))
                cs, ce = s, e
        merged.append((cs, ce))
        covered = sum(e - s for s, e in merged)

    coverage_pct = covered / total_sec * 100 if total_sec > 0 else 0.0

    print(bar)
    print(f"  DIALOGUE PLAN  ({len(placements)} cues, {coverage_pct:.1f}% VO coverage)")
    print(bar)
    for p in placements:
        dur = p.end_sec - p.start_sec
        shots_str = ", ".join(str(i) for i in p.shot_indexes_covered)
        print(
            f"  {fmt(p.start_sec)} -> {fmt(p.end_sec)}  "
            f"({dur:.2f}s)  duck={p.duck_db:.0f}dB  "
            f"shots=[{shots_str}]"
        )
        print(f"     \"{p.expected_line}\"")
    print(bar)
