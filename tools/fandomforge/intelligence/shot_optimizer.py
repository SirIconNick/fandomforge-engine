"""Shot optimizer for FandomForge.

Takes a narrative template, a style profile, a song structure, a shot library,
and a list of dialogue cues. Produces a complete shot list with beat-aligned
cut times, VO placements, and editorial metadata.

Algorithm overview:
1. Divide the target duration into template slots using relative_position
   and duration_pct.
2. For each slot, compute how many cuts to place using style profile medians
   and a jitter factor.
3. Distribute cut times across the slot using a weighted beat grid: 7% land
   on downbeats, 22% land on any beat, the rest land on half-beats or
   slightly off-grid (matching reference stats).
4. For each cut window, query the shot library for candidates matching the
   slot's required_shot_tags. Score candidates and pick the best one,
   avoiding 3-in-a-row same-source runs. Transition cost from
   transition_scorer is applied as an additional penalty per candidate.
5. Place VO cues: only over silent shots, in verse/building sections, 2-4
   frames before a cut when possible. Keep total VO coverage in 22-28%.
6. Ensure the "biggest combat leon shot" lands at the 80% mark.
7. Return an EditPlan with shots, VO placements, and stats.
"""

from __future__ import annotations

import json
import logging
import math
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .narrative_templates import NarrativeTemplate, StorySlot
from .shot_library import Shot, ShotLibrary
from .song_structure import Beat, Section, SongStructure
from . import transition_scorer as _ts

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Transition scorer integration
# ---------------------------------------------------------------------------
# When a previous shot has been placed, we build a lightweight ShotData from
# it and compute the transition cost against each candidate. The cost (0-1,
# where 0 = perfect cut) is scaled and subtracted from the raw shot score.
# Weight chosen so a terrible cut (-10 pts) can be overridden by a strong
# tag match (+20 pts) but not by quality alone.

_TRANSITION_COST_WEIGHT: float = 10.0


# ---------------------------------------------------------------------------
# Style profile constants (derived from aggregated reference stats)
# ---------------------------------------------------------------------------

_SHOT_DUR_MEDIAN_DEFAULT: float = 1.14
_SHOT_DUR_P25_DEFAULT: float = 0.84
_SHOT_DUR_P75_DEFAULT: float = 1.75
_VO_COVERAGE_TARGET_LO: float = 0.22
_VO_COVERAGE_TARGET_HI: float = 0.28
_VO_COVERAGE_TARGET_MID: float = 0.25

# Beat grid distribution targets (ref stats):
#   ~7%  downbeat
#   ~22% any beat (includes downbeats)
#   rest half-beat or off-grid
_DOWNBEAT_PROB: float = 0.07
_BEAT_PROB: float = 0.22

# Frames per second assumed for pre-cut VO placement
_FPS: int = 24
_VO_PRE_CUT_FRAMES: int = 3  # place VO this many frames before cut-out


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ShotRecord:
    """A single placed shot in the edit timeline.

    Attributes:
        cut_index: Zero-based index of this shot in the sequence.
        slot_name: Name of the narrative template slot this shot fills.
        start_time: Timeline position where this shot starts (seconds).
        duration: How long this shot is held on screen (seconds).
        source: Source clip filename, e.g. 'leon-re4r-cutscenes'.
        clip_start_sec: Start point within the source clip (seconds).
        clip_end_sec: End point within the source clip (seconds).
        era: Era tag from the shot library, e.g. 'RE4R-2004'.
        character_main: Primary character in the shot.
        character_speaks: True if the character is talking in this shot.
        action: Action attribute from the shot library.
        emotion: Emotion attribute.
        mood_profile: Slot mood this shot was assigned to.
        beat_aligned: True if this shot's start time was snapped to a beat.
        is_downbeat: True if snapped to a bar downbeat specifically.
        shot_library_id: Row id in the shot library database.
        desc: Free-text description from the shot library.
        intent: One-sentence editorial intent for this placement.
    """

    cut_index: int
    slot_name: str
    start_time: float
    duration: float
    source: str
    clip_start_sec: float
    clip_end_sec: float
    era: str | None
    character_main: str | None
    character_speaks: bool
    action: str | None
    emotion: str | None
    mood_profile: str
    beat_aligned: bool
    is_downbeat: bool
    shot_library_id: int
    desc: str | None
    intent: str


@dataclass
class VOPlacement:
    """A dialogue cue placed over a specific shot.

    Attributes:
        cut_index: Index of the ShotRecord this VO plays over.
        audio_path: Absolute path to the WAV file.
        expected_line: Human-readable transcript of the line.
        start_time: Timeline position where playback begins (seconds).
        duration: Audio duration in seconds.
        pre_cut_frames: How many frames before the cut-out this VO ends.
        slot_name: Template slot this VO is assigned to.
    """

    cut_index: int
    audio_path: str
    expected_line: str
    start_time: float
    duration: float
    pre_cut_frames: int
    slot_name: str


@dataclass
class DialogueCue:
    """A candidate VO line for the optimizer to place.

    Attributes:
        audio_path: Absolute path to the WAV file.
        expected_line: Human-readable transcript. Used as display label.
        duration_sec: Measured duration of the audio in seconds.
    """

    audio_path: str
    expected_line: str
    duration_sec: float


@dataclass
class EditPlanMeta:
    """Statistics and metadata for a completed edit plan.

    Attributes:
        template_name: Name of the NarrativeTemplate used.
        total_duration_sec: Total edit duration in seconds.
        total_shots: Number of placed shots.
        total_vo_placements: Number of VO cues placed.
        vo_coverage_pct: Fraction of edit runtime covered by VO audio.
        beat_aligned_pct: Fraction of shots that start on a beat.
        downbeat_aligned_pct: Fraction of shots on a bar downbeat.
        big_hit_time: Timeline position of the largest peak-energy shot.
        style_template_path: Path used for the style profile.
        song_path: Audio file analysed for this plan.
        shots_per_source: How many shots came from each source.
        shots_per_era: How many shots came from each era.
    """

    template_name: str
    total_duration_sec: float
    total_shots: int
    total_vo_placements: int
    vo_coverage_pct: float
    beat_aligned_pct: float
    downbeat_aligned_pct: float
    big_hit_time: float
    style_template_path: str
    song_path: str
    shots_per_source: dict[str, int]
    shots_per_era: dict[str, int]


@dataclass
class EditPlan:
    """The complete output of the shot optimizer.

    Attributes:
        shots: Ordered list of ShotRecord instances.
        dialogue_placements: VO cues mapped to specific shots.
        metadata: Summary statistics for the plan.
    """

    shots: list[ShotRecord]
    dialogue_placements: list[VOPlacement]
    metadata: EditPlanMeta

    def to_json(self, path: str | Path) -> None:
        """Serialise the plan to a JSON file.

        Args:
            path: Output path. Parent must exist.
        """
        data = asdict(self)
        Path(path).write_text(json.dumps(data, indent=2))
        logger.info("EditPlan saved to %s", path)

    @classmethod
    def from_json(cls, path: str | Path) -> EditPlan:
        """Load a previously saved EditPlan from JSON.

        Auto-detects LayeredPlan shape (start_sec/duration_sec/dialogue_lines)
        and converts it to EditPlan fields so downstream exporters (NLE,
        storyboard, etc.) work with either plan format.

        Args:
            path: JSON file path.

        Returns:
            Reconstructed EditPlan.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        raw = json.loads(Path(path).read_text())
        # LayeredPlan shape detection: uses `dialogue_lines` and shot fields
        # like `start_sec`/`duration_sec` instead of `start_time`/`duration`.
        if "dialogue_lines" in raw:
            return cls._from_layered_json(raw)
        shots = [ShotRecord(**s) for s in raw["shots"]]
        placements = [VOPlacement(**v) for v in raw["dialogue_placements"]]
        meta = EditPlanMeta(**raw["metadata"])
        return cls(shots=shots, dialogue_placements=placements, metadata=meta)

    @classmethod
    def _from_layered_json(cls, raw: dict) -> EditPlan:
        """Convert a LayeredPlan JSON dict to EditPlan shape."""
        shots: list[ShotRecord] = []
        for i, s in enumerate(raw.get("shots", [])):
            start = float(s.get("start_sec", 0.0))
            dur = float(s.get("duration_sec", 0.0))
            cs = float(s.get("clip_start_sec", 0.0))
            ce = float(s.get("clip_end_sec", cs + dur))
            shots.append(ShotRecord(
                cut_index=i,
                slot_name=s.get("kind", "broll"),
                start_time=start,
                duration=dur,
                source=s.get("source", ""),
                clip_start_sec=cs,
                clip_end_sec=ce,
                era=s.get("era", "") or None,
                character_main=None,
                character_speaks=False,
                action=None,
                emotion=s.get("intent", "").split("(")[-1].rstrip(")") if "(" in s.get("intent", "") else None,
                mood_profile=s.get("intent", ""),
                beat_aligned=bool(s.get("beat_aligned", False)),
                is_downbeat=False,
                shot_library_id=-1,
                desc=s.get("desc"),
                intent=s.get("intent", ""),
            ))
        placements: list[VOPlacement] = []
        for d in raw.get("dialogue_lines", []):
            placement = d.get("placement_sec")
            if placement is None:
                continue
            # Find the nearest shot at this time (used as cut_index)
            cut_idx = 0
            for i, sh in enumerate(shots):
                if sh.start_time <= placement:
                    cut_idx = i
            placements.append(VOPlacement(
                cut_index=cut_idx,
                audio_path=d.get("wav_path", ""),
                expected_line=d.get("text", ""),
                start_time=float(placement),
                duration=float(d.get("duration_sec", 0.0)),
                pre_cut_frames=0,
                slot_name=d.get("anchor_mode", "vo"),
            ))
        total_dur = float(raw.get("total_duration", 0.0))
        beat_aligned_count = sum(1 for s in shots if s.beat_aligned)
        sources: dict[str, int] = {}
        eras: dict[str, int] = {}
        for s in shots:
            sources[s.source] = sources.get(s.source, 0) + 1
            if s.era:
                eras[s.era] = eras.get(s.era, 0) + 1
        meta = EditPlanMeta(
            template_name=raw.get("template", "LayeredPlan"),
            total_duration_sec=total_dur,
            total_shots=len(shots),
            total_vo_placements=len(placements),
            vo_coverage_pct=0.0,
            beat_aligned_pct=(100.0 * beat_aligned_count / max(1, len(shots))),
            downbeat_aligned_pct=0.0,
            big_hit_time=0.0,
            style_template_path="",
            song_path=raw.get("song_path", ""),
            shots_per_source=sources,
            shots_per_era=eras,
        )
        return cls(shots=shots, dialogue_placements=placements, metadata=meta)


# ---------------------------------------------------------------------------
# Beat grid utilities
# ---------------------------------------------------------------------------

def _build_beat_grid(song: SongStructure) -> list[tuple[float, bool, bool]]:
    """Return a flat list of (time, is_beat, is_downbeat) grid points.

    Includes full beats and half-beat positions (midpoints between adjacent
    beats). Half-beats are marked is_beat=False, is_downbeat=False.

    Args:
        song: Analysed SongStructure.

    Returns:
        List of (time, is_beat, is_downbeat), sorted by time.
    """
    beat_times = [b.time for b in song.beats]
    downbeat_set = set(song.downbeats)

    grid: list[tuple[float, bool, bool]] = []

    for i, bt in enumerate(beat_times):
        is_db = bt in downbeat_set
        grid.append((round(bt, 4), True, is_db))

        # Insert a half-beat between this beat and the next
        if i + 1 < len(beat_times):
            half = round((bt + beat_times[i + 1]) / 2.0, 4)
            grid.append((half, False, False))

    grid.sort(key=lambda x: x[0])
    return grid


def _snap_to_grid(
    target_time: float,
    grid: list[tuple[float, bool, bool]],
    window: float = 0.15,
    prefer_beat: bool = True,
) -> tuple[float, bool, bool]:
    """Snap a target time to the nearest grid point within a window.

    Args:
        target_time: Desired cut time in seconds.
        grid: Full beat grid from _build_beat_grid().
        window: Maximum allowed snap distance in seconds.
        prefer_beat: If True, prefer full beats over half-beats when both
            are within window distance.

    Returns:
        Tuple of (snapped_time, is_beat, is_downbeat). If no grid point is
        within window, returns (target_time, False, False).
    """
    candidates = [g for g in grid if abs(g[0] - target_time) <= window]
    if not candidates:
        return round(target_time, 4), False, False

    if prefer_beat:
        full_beats = [c for c in candidates if c[1]]
        pool = full_beats if full_beats else candidates
    else:
        pool = candidates

    best = min(pool, key=lambda c: abs(c[0] - target_time))
    return best


# ---------------------------------------------------------------------------
# Shot scoring
# ---------------------------------------------------------------------------

def _score_shot(
    shot: Shot,
    slot: StorySlot,
    recent_sources: list[str],
    already_used_ids: set[int],
    prev_shot_record: "ShotRecord | None" = None,
) -> float:
    """Score a shot candidate for a given slot.

    Higher is better. Returns a float in [0, 100].

    Scoring factors:
    - +30 points if the shot has not been used in this plan yet.
    - +20 if quality_score >= 0.7.
    - +20 tag overlap: each matching required_shot_tag = +4 (max 20).
    - -40 if the source is the same as the most recent 2 shots
      (penalises 3-in-a-row same source).
    - -100 if the source appears 3 times at the end of recent_sources
      (hard block).
    - -30 if the shot ID was already used in this plan.
    - +5 if use_rank == 0 (never used in any plan).
    - up to -10 from transition_scorer cost when prev_shot_record is provided.

    Args:
        shot: Candidate Shot from the library.
        slot: The StorySlot we are filling.
        recent_sources: Sources of the last few placed shots (latest last).
        already_used_ids: Set of shot IDs already placed in this plan.
        prev_shot_record: The most recently placed ShotRecord, used to compute
            the transition cost against this candidate. None for the first shot.

    Returns:
        Score value.
    """
    score = 50.0

    # Penalise repeats within this plan
    if shot.id in already_used_ids:
        score -= 30.0

    # Quality bonus
    if shot.quality_score is not None and shot.quality_score >= 0.7:
        score += 20.0

    # Tag overlap bonus
    shot_tags = {
        shot.character_main,
        shot.action,
        shot.emotion,
        shot.setting,
        shot.era,
    } - {None}
    tag_hits = sum(1 for t in slot.required_shot_tags if t in shot_tags)
    score += tag_hits * 4.0

    # Same-source penalty
    if len(recent_sources) >= 1 and recent_sources[-1] == shot.source:
        score -= 20.0
    if len(recent_sources) >= 2 and recent_sources[-2] == shot.source:
        score -= 20.0

    # Hard block: same source 3 in a row
    if len(recent_sources) >= 2:
        last3 = recent_sources[-2:]
        if all(s == shot.source for s in last3):
            return -999.0

    # Virgin shot bonus
    if shot.use_rank == 0:
        score += 5.0

    # Transition quality penalty (integrates motion, gaze, luminance, size, color)
    if prev_shot_record is not None:
        try:
            shot_a = _ts.ShotData.from_shot_record(prev_shot_record)
            shot_b = _ts.ShotData(
                shot_id=shot.id,
                source=shot.source,
                lighting=shot.lighting,
                color_palette=shot.color_palette,
                action=shot.action,
                emotion=shot.emotion,
                motion_dir=None,
                motion_kind=None,
                gaze_dir=None,
            )
            cost = _ts.transition_cost(shot_a, shot_b, recent_sources=recent_sources[-5:])
            score -= cost * _TRANSITION_COST_WEIGHT
        except Exception:
            pass  # Transition scoring is best-effort; never break selection

    return score


def _build_intent(shot: Shot, slot: StorySlot) -> str:
    """Generate a one-sentence editorial intent string for a shot placement.

    Args:
        shot: The placed Shot.
        slot: The StorySlot it fills.

    Returns:
        Short human-readable intent string.
    """
    char = shot.character_main or "character"
    action = shot.action or "present"
    emotion = shot.emotion or slot.mood_profile
    slot_label = slot.name.replace("-", " ")
    return (
        f"{slot_label}: {char} {action} ({emotion}) "
        f"from {shot.era or shot.source}"
    )


# ---------------------------------------------------------------------------
# VO placement logic
# ---------------------------------------------------------------------------

def _compute_vo_coverage(
    placements: list[VOPlacement],
    total_duration: float,
) -> float:
    """Return fraction of the edit runtime covered by VO audio.

    Accounts for overlapping VO windows by merging intervals.

    Args:
        placements: All placed VO cues.
        total_duration: Total edit duration in seconds.

    Returns:
        VO coverage as a fraction 0.0 to 1.0.
    """
    if not placements or total_duration <= 0:
        return 0.0

    intervals = sorted((v.start_time, v.start_time + v.duration) for v in placements)
    merged: list[tuple[float, float]] = []
    cur_start, cur_end = intervals[0]
    for s, e in intervals[1:]:
        if s <= cur_end:
            cur_end = max(cur_end, e)
        else:
            merged.append((cur_start, cur_end))
            cur_start, cur_end = s, e
    merged.append((cur_start, cur_end))

    covered = sum(e - s for s, e in merged)
    return min(covered / total_duration, 1.0)


def _section_at_time(time: float, sections: list[Section]) -> Section | None:
    """Return the song section containing the given time.

    Args:
        time: Timeline position in seconds.
        sections: Section list from SongStructure.

    Returns:
        Matching Section or None.
    """
    for sec in sections:
        if sec.start_time <= time < sec.end_time:
            return sec
    return None


# ---------------------------------------------------------------------------
# Main optimizer
# ---------------------------------------------------------------------------

def plan_edit(
    template: NarrativeTemplate,
    style_profile: dict[str, Any],
    song: SongStructure,
    library: ShotLibrary,
    dialogue_cues: list[DialogueCue],
    total_duration: float | None = None,
    seed: int | None = None,
) -> EditPlan:
    """Build a complete shot list from template, song structure, and library.

    Algorithm:
    1. Resolve total_duration (default: min(90s, song.duration)).
    2. Build a beat grid with half-beat positions.
    3. Map each template slot to an absolute time window.
    4. For each slot, compute target cut count from style profile medians.
    5. For each cut, pick a time on the beat grid (respecting downbeat/beat/off
       distribution), then query and score library shots.
    6. After all shots are placed, identify and verify the 80% peak shot.
    7. Place VO cues over silent shots in verse sections, respecting the
       22-28% coverage target.
    8. Compute final statistics and return the EditPlan.

    Args:
        template: NarrativeTemplate defining the story structure.
        style_profile: Dict loaded from .style-template.json. Expected keys:
            shot_dur_median, shot_dur_p25, shot_dur_p75, beat_alignment_pct,
            downbeat_alignment_pct.
        song: Analysed SongStructure from song_structure.py.
        library: Opened ShotLibrary instance.
        dialogue_cues: List of DialogueCue candidates. The optimizer picks
            from these for VO slots.
        total_duration: Target edit length in seconds. If None, defaults to
            the lesser of 90.0 seconds and song.duration.
        seed: Optional random seed for reproducible plans.

    Returns:
        EditPlan containing shots, VO placements, and metadata.
    """
    rng = random.Random(seed)

    # -- 1. Resolve duration -----------------------------------------------
    if total_duration is None:
        total_duration = min(90.0, song.duration)
    total_duration = min(total_duration, song.duration)

    logger.info(
        "Planning edit: template=%s  duration=%.1fs  library=%d shots",
        template.name,
        total_duration,
        len(library.search(limit=1)),  # lightweight existence check
    )

    # -- Read style profile values -----------------------------------------
    shot_dur_median = float(style_profile.get("shot_dur_median", _SHOT_DUR_MEDIAN_DEFAULT))
    shot_dur_p25 = float(style_profile.get("shot_dur_p25", _SHOT_DUR_P25_DEFAULT))
    shot_dur_p75 = float(style_profile.get("shot_dur_p75", _SHOT_DUR_P75_DEFAULT))

    # -- 2. Beat grid -------------------------------------------------------
    beat_grid = _build_beat_grid(song)
    beat_times_only = [g[0] for g in beat_grid if g[1]]  # full beats only

    # -- 3. Map template slots to absolute time windows --------------------
    @dataclass
    class SlotWindow:
        slot: StorySlot
        t_start: float
        t_end: float
        duration: float

    slot_windows: list[SlotWindow] = []
    for slot in template.slots:
        t0 = slot.relative_position * total_duration
        t1 = min((slot.relative_position + slot.duration_pct) * total_duration, total_duration)
        slot_windows.append(SlotWindow(slot=slot, t_start=t0, t_end=t1, duration=t1 - t0))

    # -- 4 & 5. Place shots -----------------------------------------------
    shots: list[ShotRecord] = []
    recently_used_sources: list[str] = []
    used_shot_ids: set[int] = set()
    cut_idx = 0
    peak_shot_candidate: tuple[int, float] | None = None  # (cut_index, score)

    # Pre-fetch all leon shots with high tags for the 80% peak shot
    peak_time_target = total_duration * 0.80

    for sw in slot_windows:
        slot = sw.slot
        slot_dur = sw.duration

        # Target number of cuts for this slot
        target_cuts = int(round(slot_dur / shot_dur_median))
        min_cuts, max_cuts = slot.ideal_cut_count
        target_cuts = max(min_cuts, min(max_cuts, target_cuts))
        target_cuts = max(1, target_cuts)

        # Distribute cut start times across the slot
        cut_start_times: list[float] = []
        if target_cuts == 1:
            cut_start_times = [sw.t_start]
        else:
            step = slot_dur / target_cuts
            for i in range(target_cuts):
                t = sw.t_start + i * step
                # Small jitter (up to 15% of step) so it doesn't feel mechanical
                jitter = rng.uniform(-0.15 * step, 0.15 * step)
                t = max(sw.t_start, min(sw.t_end - 0.3, t + jitter))
                cut_start_times.append(round(t, 4))

        # For each cut, snap to beat grid and pick a shot
        for i, t_raw in enumerate(cut_start_times):
            # Determine snap strategy based on distribution targets
            roll = rng.random()
            if roll < _DOWNBEAT_PROB:
                # Try to snap to a downbeat
                snapped_time, is_beat, is_db = _snap_to_grid(
                    t_raw, beat_grid, window=0.20, prefer_beat=True
                )
                # Verify it's actually a downbeat
                if not is_db:
                    snapped_time, is_beat, is_db = t_raw, False, False
            elif roll < _BEAT_PROB:
                # Snap to any beat
                snapped_time, is_beat, is_db = _snap_to_grid(
                    t_raw, beat_grid, window=0.15, prefer_beat=True
                )
            else:
                # Off-grid or half-beat (the majority of cuts)
                half_beat_grid = [(g[0], g[1], g[2]) for g in beat_grid if not g[1]]
                if half_beat_grid:
                    snapped_time, is_beat, is_db = _snap_to_grid(
                        t_raw, half_beat_grid, window=0.18, prefer_beat=False
                    )
                else:
                    snapped_time, is_beat, is_db = t_raw, False, False

            # Shot duration: the gap to the next cut (or slot end)
            if i + 1 < len(cut_start_times):
                shot_dur = max(0.3, cut_start_times[i + 1] - snapped_time)
            else:
                shot_dur = max(0.3, sw.t_end - snapped_time)

            # Clamp shot duration to style profile range with some tolerance
            shot_dur = min(shot_dur, shot_dur_p75 * 2.5)

            # Query library
            not_speaking_required = not slot.character_allowed_to_speak

            # Build excluded tags filter
            excluded = set(slot.excluded_shot_tags)
            excluded_sources: list[str] = []

            # Hard block same source 3-in-a-row
            if len(recently_used_sources) >= 2:
                last3 = recently_used_sources[-2:]
                if len(set(last3)) == 1:
                    excluded_sources.append(last3[0])

            # Try a few query strategies from most specific to fallback
            candidates: list[Shot] = []

            # Primary query: required tags + not_speaking constraint
            character_filter = next(
                (t for t in slot.required_shot_tags if t in {"leon", "grace", "claire", "enemy", "victor", "ashley"}),
                None,
            )
            emotion_filter = next(
                (t for t in slot.required_shot_tags if t in {"tense", "calm", "brutal", "warm", "grim", "chaotic", "vulnerable", "emotional", "quiet", "still"}),
                None,
            )
            action_filter = next(
                (t for t in slot.required_shot_tags if t in {"aiming", "walking", "shooting", "standing", "fighting", "running", "wounded", "holding_gun"}),
                None,
            )

            if not candidates:
                candidates = library.search(
                    character=character_filter,
                    emotion=emotion_filter,
                    action=action_filter,
                    not_speaking=not_speaking_required,
                    exclude_sources=excluded_sources or None,
                    limit=30,
                )

            # Fallback: just character + not_speaking
            if not candidates:
                candidates = library.search(
                    character=character_filter,
                    not_speaking=not_speaking_required,
                    exclude_sources=excluded_sources or None,
                    limit=30,
                )

            # Final fallback: anything not from blocked sources
            if not candidates:
                candidates = library.search(
                    exclude_sources=excluded_sources or None,
                    limit=30,
                )

            # Score and pick best (transition cost applied when a previous shot exists)
            _prev_rec = shots[-1] if shots else None
            scored = [
                (shot, _score_shot(shot, slot, recently_used_sources[-5:], used_shot_ids, _prev_rec))
                for shot in candidates
            ]
            scored.sort(key=lambda x: -x[1])

            # Pick from top 3 to add variety
            top_pool = scored[:3]
            if not top_pool:
                logger.warning(
                    "No shot candidates for slot '%s' cut %d at %.2fs",
                    slot.name, i, snapped_time,
                )
                continue

            chosen_shot, _ = rng.choice(top_pool)

            # Build editorial intent
            intent = _build_intent(chosen_shot, slot)

            record = ShotRecord(
                cut_index=cut_idx,
                slot_name=slot.name,
                start_time=round(snapped_time, 4),
                duration=round(shot_dur, 4),
                source=chosen_shot.source,
                clip_start_sec=round(chosen_shot.start_sec, 4),
                clip_end_sec=round(chosen_shot.end_sec, 4),
                era=chosen_shot.era,
                character_main=chosen_shot.character_main,
                character_speaks=chosen_shot.character_speaks,
                action=chosen_shot.action,
                emotion=chosen_shot.emotion,
                mood_profile=slot.mood_profile,
                beat_aligned=is_beat,
                is_downbeat=is_db,
                shot_library_id=chosen_shot.id,
                desc=chosen_shot.desc,
                intent=intent,
            )

            shots.append(record)

            # Track for peak shot scoring: prefer leon + combat shots near 80%
            closeness = abs(snapped_time - peak_time_target)
            combat_tags = {"fighting", "shooting", "aiming", "brutal", "chaotic"}
            has_combat = bool(
                {chosen_shot.action, chosen_shot.emotion} & combat_tags
            )
            is_leon = chosen_shot.character_main == "leon"
            if is_leon and has_combat:
                existing_score = peak_shot_candidate[1] if peak_shot_candidate else 999.0
                if closeness < existing_score:
                    peak_shot_candidate = (cut_idx, closeness)

            recently_used_sources.append(chosen_shot.source)
            used_shot_ids.add(chosen_shot.id)
            cut_idx += 1

    # -- 6. Verify 80% peak shot -------------------------------------------
    # If we have a candidate, mark it in the intent. If not, scan for the
    # best available shot near peak_time_target.
    if shots:
        if peak_shot_candidate:
            peak_idx = peak_shot_candidate[0]
            if 0 <= peak_idx < len(shots):
                shots[peak_idx] = ShotRecord(
                    **{
                        **asdict(shots[peak_idx]),
                        "intent": shots[peak_idx].intent + " [PEAK HIT @80%]",
                    }
                )
        else:
            # Find closest shot to 80% mark
            closest = min(shots, key=lambda s: abs(s.start_time - peak_time_target))
            idx = shots.index(closest)
            shots[idx] = ShotRecord(
                **{
                    **asdict(shots[idx]),
                    "intent": shots[idx].intent + " [PEAK HIT @80%]",
                }
            )

    # -- 7. Place VO cues --------------------------------------------------
    vo_placements: list[VOPlacement] = []
    remaining_cues = list(dialogue_cues)
    rng.shuffle(remaining_cues)

    target_vo_sec = total_duration * _VO_COVERAGE_TARGET_MID

    for shot_rec in shots:
        if not remaining_cues:
            break

        # Check coverage budget
        current_coverage_sec = sum(v.duration for v in vo_placements)
        if current_coverage_sec >= target_vo_sec * 1.1:
            break

        # VO rules: silent shot, verse/building section, not peak mood
        if shot_rec.character_speaks:
            continue
        if shot_rec.mood_profile == "peak":
            continue

        # Check song section: verse or building only, not during drops
        section = _section_at_time(shot_rec.start_time, song.sections)
        if section is None:
            continue
        if section.mood in ("peak",) or section.is_drop:
            continue
        if section.label not in ("intro", "verse", "pre-chorus", "breakdown"):
            continue

        # Pick the next cue that fits within the shot duration
        chosen_cue = None
        for cue in remaining_cues:
            # Leave 2-4 frames gap before cut-out
            available_dur = shot_rec.duration - (_VO_PRE_CUT_FRAMES / _FPS)
            if cue.duration_sec <= available_dur:
                chosen_cue = cue
                break

        if chosen_cue is None:
            continue

        # Place at start of shot or pushed back so it ends 3 frames before cut
        cut_out_time = shot_rec.start_time + shot_rec.duration
        vo_end_time = cut_out_time - (_VO_PRE_CUT_FRAMES / _FPS)
        vo_start_time = max(shot_rec.start_time, vo_end_time - chosen_cue.duration_sec)

        vo_placements.append(VOPlacement(
            cut_index=shot_rec.cut_index,
            audio_path=chosen_cue.audio_path,
            expected_line=chosen_cue.expected_line,
            start_time=round(vo_start_time, 4),
            duration=round(chosen_cue.duration_sec, 4),
            pre_cut_frames=_VO_PRE_CUT_FRAMES,
            slot_name=shot_rec.slot_name,
        ))
        remaining_cues.remove(chosen_cue)

    # Second pass: span-over approach.
    # When coverage is still below target, allow a VO cue to start anywhere on a
    # silent non-peak shot and span across the cut into subsequent silent shots.
    # The cue is anchored to the first eligible shot's cut_index for display
    # purposes, but its audio runs until it naturally ends.
    if _compute_vo_coverage(vo_placements, total_duration) < _VO_COVERAGE_TARGET_LO:
        # Build an index of which cut_indices already have VO
        vo_cut_set = {v.cut_index for v in vo_placements}

        i = 0
        while i < len(shots) and remaining_cues:
            coverage = _compute_vo_coverage(vo_placements, total_duration)
            if coverage >= _VO_COVERAGE_TARGET_HI:
                break

            shot_rec = shots[i]

            # Only start spans on silent, non-peak shots in soft sections
            if shot_rec.character_speaks or shot_rec.mood_profile == "peak":
                i += 1
                continue
            if shot_rec.cut_index in vo_cut_set:
                i += 1
                continue

            section = _section_at_time(shot_rec.start_time, song.sections)
            if section is None or section.is_drop or section.mood == "peak":
                i += 1
                continue

            # How much silent space is available starting at this shot?
            # Walk forward through silent non-peak shots to see how far we can span.
            span_end = shot_rec.start_time + shot_rec.duration
            j = i + 1
            while j < len(shots):
                nxt = shots[j]
                nxt_section = _section_at_time(nxt.start_time, song.sections)
                nxt_ok = (
                    not nxt.character_speaks
                    and nxt.mood_profile != "peak"
                    and nxt_section is not None
                    and not nxt_section.is_drop
                    and nxt_section.mood != "peak"
                )
                if nxt_ok:
                    span_end = nxt.start_time + nxt.duration
                    j += 1
                    # One span window is enough for one cue
                    break
                else:
                    break

            span_available = span_end - shot_rec.start_time - (_VO_PRE_CUT_FRAMES / _FPS)

            # Find the shortest cue that fits in the span
            chosen_cue = None
            for cue in remaining_cues:
                if cue.duration_sec <= span_available:
                    chosen_cue = cue
                    break

            if chosen_cue is None:
                i += 1
                continue

            # Anchor VO at the start of the span shot
            vo_start_time = shot_rec.start_time
            vo_placements.append(VOPlacement(
                cut_index=shot_rec.cut_index,
                audio_path=chosen_cue.audio_path,
                expected_line=chosen_cue.expected_line,
                start_time=round(vo_start_time, 4),
                duration=round(chosen_cue.duration_sec, 4),
                pre_cut_frames=_VO_PRE_CUT_FRAMES,
                slot_name=shot_rec.slot_name,
            ))
            vo_cut_set.add(shot_rec.cut_index)
            remaining_cues.remove(chosen_cue)
            i = j  # skip past the shots this span covered

    # -- 8. Compute final stats -------------------------------------------
    beat_aligned = sum(1 for s in shots if s.beat_aligned)
    downbeat_aligned = sum(1 for s in shots if s.is_downbeat)
    n = len(shots)

    sources: dict[str, int] = {}
    eras: dict[str, int] = {}
    for s in shots:
        sources[s.source] = sources.get(s.source, 0) + 1
        era_key = s.era or "unknown"
        eras[era_key] = eras.get(era_key, 0) + 1

    vo_coverage = _compute_vo_coverage(vo_placements, total_duration)
    big_hit_shot = next(
        (s for s in shots if "[PEAK HIT @80%]" in s.intent), None
    )
    big_hit_time = big_hit_shot.start_time if big_hit_shot else peak_time_target

    meta = EditPlanMeta(
        template_name=template.name,
        total_duration_sec=round(total_duration, 4),
        total_shots=n,
        total_vo_placements=len(vo_placements),
        vo_coverage_pct=round(vo_coverage * 100, 2),
        beat_aligned_pct=round(beat_aligned / max(n, 1) * 100, 2),
        downbeat_aligned_pct=round(downbeat_aligned / max(n, 1) * 100, 2),
        big_hit_time=round(big_hit_time, 4),
        style_template_path=str(style_profile.get("_source_path", "")),
        song_path=song.audio_path,
        shots_per_source=sources,
        shots_per_era=eras,
    )

    logger.info(
        "Plan complete: %d shots, %d VO, %.1f%% VO coverage, %.1f%% beat aligned",
        n, len(vo_placements), meta.vo_coverage_pct, meta.beat_aligned_pct,
    )

    return EditPlan(shots=shots, dialogue_placements=vo_placements, metadata=meta)


# ---------------------------------------------------------------------------
# Human-readable plan printer
# ---------------------------------------------------------------------------

def print_plan(plan: EditPlan) -> None:
    """Print the edit plan to stdout in a human-readable format.

    Shows a beat-by-beat shot sequence with timecodes, source, duration,
    VO overlays, and editorial intent.

    Args:
        plan: Completed EditPlan from plan_edit().
    """
    meta = plan.metadata

    def fmt(t: float) -> str:
        m = int(t) // 60
        s = t - m * 60
        return f"{m}:{s:05.2f}"

    bar = "=" * 80
    thin = "-" * 80

    print(bar)
    print(f"  EDIT PLAN: {meta.template_name}")
    print(f"  Duration : {fmt(meta.total_duration_sec)} ({meta.total_duration_sec:.1f}s)")
    print(f"  Shots    : {meta.total_shots}")
    print(f"  VO cues  : {meta.total_vo_placements}  ({meta.vo_coverage_pct:.1f}% coverage)")
    print(f"  Beat aligned: {meta.beat_aligned_pct:.1f}%  "
          f"Downbeat: {meta.downbeat_aligned_pct:.1f}%")
    print(f"  Peak hit @ {fmt(meta.big_hit_time)}")
    print(bar)
    print()

    # Build VO index by cut_index
    vo_by_cut: dict[int, list[VOPlacement]] = {}
    for v in plan.dialogue_placements:
        vo_by_cut.setdefault(v.cut_index, []).append(v)

    current_slot = ""
    for shot in plan.shots:
        # Slot header
        if shot.slot_name != current_slot:
            current_slot = shot.slot_name
            print(thin)
            print(f"  SLOT: {current_slot.upper()}")
            print(thin)

        # Beat marker
        beat_tag = ""
        if shot.is_downbeat:
            beat_tag = " [DB]"
        elif shot.beat_aligned:
            beat_tag = " [B]"

        # Speaking tag
        spk_tag = " [SPK]" if shot.character_speaks else ""

        source_short = shot.source[:22] if shot.source else "?"
        char = (shot.character_main or "?")[:8]
        action = (shot.action or "?")[:12]
        emotion = (shot.emotion or "?")[:10]
        era = (shot.era or "?")[:12]

        print(
            f"  [{shot.cut_index:03d}] {fmt(shot.start_time)}  "
            f"dur={shot.duration:4.2f}s{beat_tag}{spk_tag}  "
            f"{source_short:<22}  {char:<8} / {action:<12} / {emotion:<10}  "
            f"{era}"
        )
        print(f"        {shot.intent}")

        # VO overlays
        if shot.cut_index in vo_by_cut:
            for vo in vo_by_cut[shot.cut_index]:
                wav_name = Path(vo.audio_path).name
                print(
                    f"         >> VO @ {fmt(vo.start_time)} "
                    f"dur={vo.duration:.2f}s  [{wav_name}]"
                    f"  \"{vo.expected_line}\""
                )
        print()

    print(bar)
    print("  SOURCE BREAKDOWN")
    print(thin)
    for src, count in sorted(meta.shots_per_source.items(), key=lambda x: -x[1]):
        pct = count / max(meta.total_shots, 1) * 100
        bar_len = int(pct / 2)
        print(f"  {src:<35} {count:3d}  ({pct:5.1f}%)  {'|' * bar_len}")

    print()
    print("  ERA BREAKDOWN")
    print(thin)
    for era, count in sorted(meta.shots_per_era.items(), key=lambda x: -x[1]):
        pct = count / max(meta.total_shots, 1) * 100
        print(f"  {era:<20} {count:3d}  ({pct:5.1f}%)")

    print(bar)


# ---------------------------------------------------------------------------
# Convenience: load dialogue cues from a directory
# ---------------------------------------------------------------------------

def load_dialogue_cues(
    dialogue_dir: Path,
    pattern: str = "*.wav",
    expected_lines: dict[str, str] | None = None,
) -> list[DialogueCue]:
    """Scan a directory for WAV files and build DialogueCue objects.

    Duration is measured with librosa. If librosa is not available, a
    fallback of 3.0 seconds is used.

    Args:
        dialogue_dir: Directory to scan.
        pattern: Glob pattern for WAV files.
        expected_lines: Optional mapping of filename stem to transcript text.
            If not provided, the stem is used as the expected_line value.

    Returns:
        List of DialogueCue instances, one per matching file.
    """
    cues: list[DialogueCue] = []
    wav_files = sorted(dialogue_dir.glob(pattern))

    for wav_path in wav_files:
        stem = wav_path.stem
        line = (expected_lines or {}).get(stem, stem.replace("-", " ").replace("_", " "))

        # Measure duration
        dur = 3.0
        try:
            import librosa  # type: ignore
            y, sr = librosa.load(str(wav_path), sr=None)
            dur = float(len(y) / sr)
        except Exception as exc:
            logger.warning("Could not measure duration of %s: %s", wav_path.name, exc)

        cues.append(DialogueCue(
            audio_path=str(wav_path.resolve()),
            expected_line=line,
            duration_sec=round(dur, 4),
        ))

    return cues


# ---------------------------------------------------------------------------
# CLI test entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    # Standalone CLI defaults — override via env vars:
    #   FF_PROJECT=/path/to/project  FF_SONG=song.mp3  python -m ... shot_optimizer
    import os as _os
    _PROJECT = Path(_os.environ.get(
        "FF_PROJECT",
        "/Users/damato/Video Project/projects/leon-badass-monologue",
    ))
    _SONG_FILE = _os.environ.get("FF_SONG", "in-the-end-tommee.mp3")
    _SONG_STEM = _SONG_FILE.rsplit(".", 1)[0]
    _SONG_JSON = _PROJECT / "raw" / f"{_SONG_STEM}.song_structure.json"
    _SONG_MP3 = _PROJECT / "raw" / _SONG_FILE
    _STYLE_JSON = _PROJECT / ".style-template.json"
    _DB = _PROJECT / ".shot-library.db"
    _DIALOGUE_DIR = _PROJECT / "dialogue"
    _OUTPUT_JSON = _PROJECT / ".edit-plan-v1.json"

    # Load or analyse song structure
    if _SONG_JSON.exists():
        logger.info("Loading existing song structure from %s", _SONG_JSON)
        song = SongStructure.from_json(_SONG_JSON)
    else:
        logger.info("Analysing song (this takes ~30 seconds) ...")
        from .song_structure import analyze as _analyze
        song = _analyze(str(_SONG_MP3))
        song.to_json(_SONG_JSON)
        logger.info("Song structure saved to %s", _SONG_JSON)

    # Load style profile
    style_profile: dict[str, Any] = json.loads(_STYLE_JSON.read_text())
    style_profile["_source_path"] = str(_STYLE_JSON)

    # Load shot library
    library = ShotLibrary(_DB)
    stats = library.stats()
    logger.info(
        "Shot library: %d total shots, %d sources",
        stats["total"],
        len(stats["by_source"]),
    )

    # Load dialogue cues
    expected: dict[str, str] = {
        "leon_couldnt-save": "I couldn't save them. Any of them.",
        "leon_going-after-victor": "We're going after Victor.",
        "leon_going-to-destroy": "We're going to destroy it.",
        "leon_had-enough": "I've had enough of this.",
        "leon_here-now": "I'm here now. That's what matters.",
        "leon_intro-dso": "DSO operative Leon Kennedy.",
        "leon_its-over-victor": "It's over, Victor.",
        "leon_lets-do-this": "Let's do this.",
        "leon_line-1": "This ends now.",
        "leon_line-2": "There's no coming back from this.",
        "leon_line-3": "I won't let you hurt anyone else.",
        "leon_six-survivors": "Six survivors. That's all that was left.",
        "leon_umbrella-gideon": "Umbrella, Gideon -- doesn't matter who's behind this.",
    }
    dialogue_cues = load_dialogue_cues(_DIALOGUE_DIR, pattern="leon_*.wav", expected_lines=expected)
    logger.info("Loaded %d dialogue cues", len(dialogue_cues))

    # Get HauntedVeteran template
    from .narrative_templates import get_template
    template = get_template("HauntedVeteran")
    warnings = template.validate()
    if warnings:
        for w in warnings:
            logger.warning("Template warning: %s", w)

    # Run optimizer: 90 second edit
    logger.info("Running shot optimizer ...")
    plan = plan_edit(
        template=template,
        style_profile=style_profile,
        song=song,
        library=library,
        dialogue_cues=dialogue_cues,
        total_duration=90.0,
        seed=42,
    )

    # Print human-readable plan
    print_plan(plan)

    # Save JSON
    plan.to_json(_OUTPUT_JSON)
    print(f"\nPlan saved to: {_OUTPUT_JSON}")

    library.close()
