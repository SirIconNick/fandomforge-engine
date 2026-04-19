"""Song-to-footage compatibility scorer.

Given one or more candidate songs and a shot library, this module scores how
well each song fits the available footage by examining four dimensions:

1. arc_match      -- Does the song's emotional arc (quiet->build->peak->breath)
                     match the library's emotional distribution? Scored by
                     comparing per-section mood proportions against library
                     emotion bucket counts.

2. mood_coverage  -- Can every section mood in the song be served by at least
                     N shots in the library with a matching or compatible emotion?
                     Scored as fraction of song sections that have sufficient shots.

3. pace_match     -- Does the song BPM pair well with the pace implied by the
                     shot library? Derived from the motion_mag column (when
                     present) or from action tag distribution as a proxy.

4. coverage_sufficiency -- Is there enough total content in the library to fill
                     the song at a reasonable target shot duration? Scored as
                     min(1, available_screen_seconds / required_screen_seconds).

Use case: user has 10 candidate songs. rank_songs() returns them sorted by
overall fit score, highest first.

Integration:
    from tools.fandomforge.intelligence.song_footage_matcher import rank_songs
    from tools.fandomforge.intelligence.song_structure import analyze

    scores = rank_songs(
        song_paths=["/path/to/song1.mp3", "/path/to/song2.mp3"],
        library_db="/path/to/shot_library.db",
        desired_character="leon",
    )
    for song_path, score in scores:
        print(f"{Path(song_path).name}: {score.overall:.2f}")
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MatchScore:
    """Compatibility score between a candidate song and a shot library.

    Attributes:
        song_path: Absolute path to the song file that was scored.
        overall: Weighted composite score in [0.0, 1.0].
        arc_match: How well the song's energy arc mirrors the library's
            emotional distribution. 1.0 = perfect match.
        mood_coverage: Fraction of song sections whose mood is adequately
            covered by matching library shots. 1.0 = all sections covered.
        pace_match: How well the song BPM pairs with the library's implied
            action pace. 1.0 = ideal pairing.
        coverage_sufficiency: Whether the library has enough screen time to
            fill the song at the target shot duration. 1.0 = sufficient.
        song_bpm: Detected song BPM.
        song_duration_sec: Song duration in seconds.
        library_shot_count: Number of shots in the library for the character.
        library_total_duration_sec: Total screen time available in the library.
        notes: List of human-readable diagnostic notes.
    """

    song_path: str
    overall: float = 0.0
    arc_match: float = 0.0
    mood_coverage: float = 0.0
    pace_match: float = 0.0
    coverage_sufficiency: float = 0.0
    song_bpm: float = 0.0
    song_duration_sec: float = 0.0
    library_shot_count: int = 0
    library_total_duration_sec: float = 0.0
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Weights for the composite score
_W_ARC: float = 0.30
_W_MOOD: float = 0.25
_W_PACE: float = 0.20
_W_COVERAGE: float = 0.25

# Target shot duration in seconds (how long we hold on each shot on average)
_DEFAULT_SHOT_DURATION_SEC: float = 2.0

# Minimum shots per song section mood before coverage is considered adequate
_MIN_SHOTS_PER_SECTION: int = 3

# BPM ranges and implied action pace labels
_BPM_PACE_MAP: list[tuple[float, float, str]] = [
    (0,    70,  "very_slow"),   # ballads, ambient
    (70,   90,  "slow"),        # slow emotional
    (90,   110, "moderate"),    # mid-tempo
    (110,  135, "energetic"),   # action/pop
    (135,  160, "fast"),        # aggressive/metal
    (160,  999, "very_fast"),   # hyper
]

# Emotion -> mood bucket mapping (for arc comparison)
_EMOTION_TO_MOOD: dict[str, str] = {
    "tense":       "building",
    "grim":        "quiet",
    "calm":        "quiet",
    "still":       "quiet",
    "quiet":       "quiet",
    "vulnerable":  "quiet",
    "emotional":   "building",
    "brutal":      "peak",
    "chaotic":     "peak",
    "warm":        "building",
}

# Section label -> mood bucket (from song_structure.py conventions)
_SECTION_TO_MOOD: dict[str, str] = {
    "intro":      "quiet",
    "verse":      "building",
    "pre-chorus": "building",
    "chorus":     "peak",
    "bridge":     "breakdown",
    "breakdown":  "breakdown",
    "outro":      "quiet",
    "unknown":    "building",
}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _load_shots_from_db(
    library_db: str | Path,
    character: Optional[str] = None,
) -> list[dict]:
    """Load shots from the SQLite shot library, optionally filtered by character.

    Args:
        library_db: Path to the SQLite database used by shot_library.py.
        character: Character name to filter on (e.g. 'leon'). When None,
            all shots are returned.

    Returns:
        List of dicts with keys: id, source, era, start_sec, end_sec,
        duration_sec, action, emotion, motion_mag (may be None).

    Raises:
        FileNotFoundError: If the database file does not exist.
    """
    db_path = Path(library_db).resolve()
    if not db_path.exists():
        raise FileNotFoundError(f"Shot library database not found: {db_path}")

    rows: list[dict] = []
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check which columns exist (motion_mag may be from a later migration)
        cursor.execute("PRAGMA table_info(shots)")
        col_names = {row[1] for row in cursor.fetchall()}
        has_motion_mag = "motion_mag" in col_names

        select_cols = (
            "id, source, era, start_sec, end_sec, duration_sec, "
            "action, emotion, character_main"
        )
        if has_motion_mag:
            select_cols += ", motion_mag"

        if character:
            cursor.execute(
                f"SELECT {select_cols} FROM shots WHERE character_main = ? "
                "ORDER BY start_sec",
                (character.lower(),),
            )
        else:
            cursor.execute(
                f"SELECT {select_cols} FROM shots ORDER BY start_sec"
            )

        for row in cursor.fetchall():
            d = dict(row)
            if "motion_mag" not in d:
                d["motion_mag"] = None
            rows.append(d)

    logger.debug(
        "Loaded %d shots from %s (character=%s)", len(rows), db_path.name, character
    )
    return rows


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------


def _bpm_to_pace_label(bpm: float) -> str:
    """Return a pace label for a given BPM value.

    Args:
        bpm: Song tempo in beats per minute.

    Returns:
        One of 'very_slow', 'slow', 'moderate', 'energetic', 'fast', 'very_fast'.
    """
    for lo, hi, label in _BPM_PACE_MAP:
        if lo <= bpm < hi:
            return label
    return "energetic"


def _library_pace_label(shots: list[dict]) -> str:
    """Estimate the library's action pace from motion_mag or action tags.

    When motion_mag is available, the mean is used. Otherwise, falls back to
    counting high-action tags (aiming, shooting, running, fighting).

    Args:
        shots: List of shot dicts from _load_shots_from_db().

    Returns:
        A pace label string.
    """
    motion_values = [
        s["motion_mag"] for s in shots
        if s.get("motion_mag") is not None
    ]

    if motion_values:
        mean_motion = sum(motion_values) / len(motion_values)
        # Normalise to [0, 1] assuming max reasonable motion_mag ~ 100
        normalised = min(1.0, mean_motion / 80.0)
        if normalised < 0.20:
            return "slow"
        if normalised < 0.40:
            return "moderate"
        if normalised < 0.65:
            return "energetic"
        return "fast"

    # Fallback: count active vs. passive action tags
    high_action_tags = {"aiming", "shooting", "running", "fighting", "driving"}
    low_action_tags = {"standing", "watching", "listening", "sitting", "none"}
    high = sum(1 for s in shots if s.get("action", "") in high_action_tags)
    low = sum(1 for s in shots if s.get("action", "") in low_action_tags)
    total = max(1, high + low)
    action_ratio = high / total

    if action_ratio >= 0.55:
        return "fast"
    if action_ratio >= 0.35:
        return "energetic"
    if action_ratio >= 0.20:
        return "moderate"
    return "slow"


def _pace_match_score(song_bpm: float, library_pace: str) -> float:
    """Score how well the song BPM matches the library's action pace.

    Perfect match = song pace label equals library pace label (1.0).
    Adjacent labels = 0.6. Two steps away = 0.3. Three+ = 0.1.

    Args:
        song_bpm: Song tempo in BPM.
        library_pace: Library pace label from _library_pace_label().

    Returns:
        Match score in [0.0, 1.0].
    """
    song_pace = _bpm_to_pace_label(song_bpm)
    pace_order = ["very_slow", "slow", "moderate", "energetic", "fast", "very_fast"]

    try:
        si = pace_order.index(song_pace)
        li = pace_order.index(library_pace)
    except ValueError:
        return 0.5  # unknown label, neutral score

    diff = abs(si - li)
    score_map = {0: 1.0, 1: 0.70, 2: 0.40, 3: 0.20, 4: 0.10, 5: 0.05}
    return score_map.get(diff, 0.05)


def _build_mood_buckets(shots: list[dict]) -> dict[str, int]:
    """Count shots per mood bucket using the emotion->mood mapping.

    Args:
        shots: List of shot dicts.

    Returns:
        Dict of mood_label -> count. Keys: 'quiet', 'building', 'peak', 'breakdown'.
    """
    buckets: dict[str, int] = {"quiet": 0, "building": 0, "peak": 0, "breakdown": 0}
    for s in shots:
        emo = (s.get("emotion") or "").lower()
        mood = _EMOTION_TO_MOOD.get(emo)
        if mood and mood in buckets:
            buckets[mood] += 1
    return buckets


def _arc_match_score(
    sections: list,
    library_buckets: dict[str, int],
    total_shots: int,
) -> float:
    """Compare song section arc proportions against library mood proportions.

    Uses cosine similarity between two normalised 4-bucket vectors:
    (quiet, building, peak, breakdown).

    Args:
        sections: List of Section objects from SongStructure.sections.
        library_buckets: Mood bucket counts from _build_mood_buckets().
        total_shots: Total number of shots in the library (for normalisation).

    Returns:
        Arc match score in [0.0, 1.0]. 1.0 = identical proportions.
    """
    if not sections or total_shots == 0:
        return 0.5

    mood_labels = ["quiet", "building", "peak", "breakdown"]

    # Song duration per mood bucket (proportions by section duration)
    song_dur_per_mood: dict[str, float] = {m: 0.0 for m in mood_labels}
    total_song_dur = 0.0
    for sec in sections:
        section_label = str(getattr(sec, "label", "unknown"))
        section_dur = float(getattr(sec, "duration", 0.0))
        mood = _SECTION_TO_MOOD.get(section_label, "building")
        song_dur_per_mood[mood] = song_dur_per_mood.get(mood, 0.0) + section_dur
        total_song_dur += section_dur

    if total_song_dur <= 0:
        return 0.5

    song_vec = [song_dur_per_mood[m] / total_song_dur for m in mood_labels]
    lib_vec = [library_buckets.get(m, 0) / total_shots for m in mood_labels]

    # Cosine similarity
    dot = sum(a * b for a, b in zip(song_vec, lib_vec))
    mag_s = sum(x ** 2 for x in song_vec) ** 0.5
    mag_l = sum(x ** 2 for x in lib_vec) ** 0.5
    if mag_s < 1e-8 or mag_l < 1e-8:
        return 0.5
    return min(1.0, dot / (mag_s * mag_l))


def _mood_coverage_score(
    sections: list,
    library_buckets: dict[str, int],
    min_shots: int = _MIN_SHOTS_PER_SECTION,
) -> float:
    """Compute what fraction of song sections have enough library shots.

    A section is 'covered' if its mood bucket has >= min_shots shots in the library.

    Args:
        sections: List of Section objects from SongStructure.
        library_buckets: Mood bucket counts from _build_mood_buckets().
        min_shots: Minimum shots required to cover a section's mood.

    Returns:
        Coverage fraction in [0.0, 1.0].
    """
    if not sections:
        return 0.0

    covered = 0
    for sec in sections:
        section_label = str(getattr(sec, "label", "unknown"))
        mood = _SECTION_TO_MOOD.get(section_label, "building")
        if library_buckets.get(mood, 0) >= min_shots:
            covered += 1

    return covered / len(sections)


def _coverage_sufficiency_score(
    song_duration_sec: float,
    library_total_duration_sec: float,
    target_shot_dur_sec: float = _DEFAULT_SHOT_DURATION_SEC,
    reuse_factor: float = 1.5,
) -> float:
    """Score whether the library has enough content to fill the song.

    Required screen time = song_duration / target_shot_dur * target_shot_dur
                         = song_duration_sec (trivially).
    But we also account for the fact that editors often want a pool 1.5x
    the actual needed duration to have selection choices.

    Args:
        song_duration_sec: Song duration in seconds.
        library_total_duration_sec: Total shot screen time in the library.
        target_shot_dur_sec: Assumed average shot hold duration.
        reuse_factor: How many times bigger the pool should be vs. the
            minimum needed. Default 1.5 (50% extra for selection choices).

    Returns:
        Score in [0.0, 1.0]. 1.0 means fully sufficient with selection slack.
    """
    if song_duration_sec <= 0:
        return 0.0

    required = song_duration_sec * reuse_factor
    if library_total_duration_sec <= 0:
        return 0.0

    ratio = library_total_duration_sec / required
    return float(min(1.0, ratio))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_match(
    song_path: str | Path,
    shot_library_db: str | Path,
    desired_character: str = "leon",
    target_shot_duration_sec: float = _DEFAULT_SHOT_DURATION_SEC,
    weights: Optional[dict[str, float]] = None,
) -> MatchScore:
    """Score how well a single song matches the shot library.

    Loads the song structure, loads shots for the desired character, then
    computes arc_match, mood_coverage, pace_match, and coverage_sufficiency
    scores. Combines them into a weighted overall score.

    Args:
        song_path: Path to the candidate song (any audio format librosa supports).
        shot_library_db: Path to the SQLite shot library database.
        desired_character: Character name to filter library shots by.
        target_shot_duration_sec: Average shot duration assumed for coverage calc.
        weights: Optional override dict for score weights. Keys:
            'arc', 'mood', 'pace', 'coverage'. Values are floats; need not sum
            to 1.0 (they are normalised internally).

    Returns:
        MatchScore dataclass with all subscores and diagnostics.

    Raises:
        FileNotFoundError: If the song file or library database does not exist.
    """
    from tools.fandomforge.intelligence.song_structure import analyze

    song_path_str = str(Path(song_path).resolve())

    if not Path(song_path_str).exists():
        raise FileNotFoundError(f"Song file not found: {song_path_str}")

    ms = MatchScore(song_path=song_path_str)

    # Load shots from library
    try:
        shots = _load_shots_from_db(shot_library_db, character=desired_character)
    except FileNotFoundError:
        ms.notes.append(f"Library database not found: {shot_library_db}")
        return ms

    if not shots:
        ms.notes.append(
            f"No shots found for character '{desired_character}' in library. "
            "Coverage will be 0."
        )

    ms.library_shot_count = len(shots)
    ms.library_total_duration_sec = sum(s.get("duration_sec", 0.0) for s in shots)

    # Analyze song structure
    logger.info("Analyzing song structure: %s", Path(song_path_str).name)
    try:
        structure = analyze(song_path_str)
    except Exception as exc:
        ms.notes.append(f"Song structure analysis failed: {exc}")
        logger.error("Song analysis failed for %s: %s", song_path_str, exc)
        return ms

    ms.song_bpm = structure.tempo
    ms.song_duration_sec = structure.duration
    sections = structure.sections

    # Library mood buckets
    library_buckets = _build_mood_buckets(shots)
    library_pace = _library_pace_label(shots)

    # Compute subscores
    ms.arc_match = _arc_match_score(sections, library_buckets, max(1, len(shots)))
    ms.mood_coverage = _mood_coverage_score(sections, library_buckets)
    ms.pace_match = _pace_match_score(structure.tempo, library_pace)
    ms.coverage_sufficiency = _coverage_sufficiency_score(
        structure.duration,
        ms.library_total_duration_sec,
        target_shot_dur_sec=target_shot_duration_sec,
    )

    # Weighted overall score
    if weights:
        w_arc = weights.get("arc", _W_ARC)
        w_mood = weights.get("mood", _W_MOOD)
        w_pace = weights.get("pace", _W_PACE)
        w_cov = weights.get("coverage", _W_COVERAGE)
    else:
        w_arc, w_mood, w_pace, w_cov = _W_ARC, _W_MOOD, _W_PACE, _W_COVERAGE

    w_total = w_arc + w_mood + w_pace + w_cov
    if w_total > 0:
        ms.overall = (
            ms.arc_match * w_arc
            + ms.mood_coverage * w_mood
            + ms.pace_match * w_pace
            + ms.coverage_sufficiency * w_cov
        ) / w_total
    else:
        ms.overall = 0.0

    ms.overall = round(min(1.0, max(0.0, ms.overall)), 4)

    # Diagnostic notes
    ms.notes.append(
        f"BPM={structure.tempo:.1f} ({_bpm_to_pace_label(structure.tempo)}) "
        f"vs library pace={library_pace}"
    )
    ms.notes.append(
        f"Sections: {len(sections)} | "
        f"Library shots: {len(shots)} | "
        f"Library duration: {ms.library_total_duration_sec:.0f}s"
    )
    ms.notes.append(
        f"Mood buckets: quiet={library_buckets['quiet']} "
        f"building={library_buckets['building']} "
        f"peak={library_buckets['peak']} "
        f"breakdown={library_buckets['breakdown']}"
    )

    if ms.coverage_sufficiency < 0.8:
        required = structure.duration * 1.5
        ms.notes.append(
            f"Coverage WARNING: library has {ms.library_total_duration_sec:.0f}s, "
            f"need ~{required:.0f}s for comfortable selection."
        )

    if ms.mood_coverage < 0.7:
        ms.notes.append(
            "Mood coverage WARNING: some song sections lack enough matching shots. "
            "Consider adding more footage."
        )

    logger.info(
        "score_match: %s  overall=%.3f  arc=%.3f  mood=%.3f  pace=%.3f  cov=%.3f",
        Path(song_path_str).name,
        ms.overall, ms.arc_match, ms.mood_coverage, ms.pace_match, ms.coverage_sufficiency,
    )
    return ms


def rank_songs(
    song_paths: list[str | Path],
    library_db: str | Path,
    desired_character: str = "leon",
    target_shot_duration_sec: float = _DEFAULT_SHOT_DURATION_SEC,
    weights: Optional[dict[str, float]] = None,
) -> list[tuple[str, MatchScore]]:
    """Score and rank multiple candidate songs against the shot library.

    Args:
        song_paths: List of song file paths to evaluate.
        library_db: Path to the SQLite shot library database.
        desired_character: Character name for library filtering.
        target_shot_duration_sec: Average shot duration for coverage calculation.
        weights: Optional dict of score weights. See score_match() for keys.

    Returns:
        List of (song_path_str, MatchScore) tuples sorted by overall score
        descending (best match first). Songs that fail analysis are included
        at the bottom with overall=0.

    Raises:
        FileNotFoundError: If library_db does not exist.
    """
    if not song_paths:
        return []

    results: list[tuple[str, MatchScore]] = []
    for song in song_paths:
        song_str = str(Path(song).resolve())
        logger.info("Scoring: %s", Path(song_str).name)
        try:
            ms = score_match(
                song,
                library_db,
                desired_character=desired_character,
                target_shot_duration_sec=target_shot_duration_sec,
                weights=weights,
            )
        except Exception as exc:
            logger.error("Failed to score %s: %s", song_str, exc, exc_info=True)
            ms = MatchScore(song_path=song_str)
            ms.notes.append(f"Scoring error: {exc}")

        results.append((song_str, ms))

    results.sort(key=lambda x: x[1].overall, reverse=True)

    logger.info("rank_songs: ranked %d songs", len(results))
    for rank, (path, ms) in enumerate(results, start=1):
        logger.info(
            "  #%d  %-40s  overall=%.3f  arc=%.3f  mood=%.3f  pace=%.3f  cov=%.3f",
            rank, Path(path).name, ms.overall,
            ms.arc_match, ms.mood_coverage, ms.pace_match, ms.coverage_sufficiency,
        )

    return results


def print_ranking(ranked: list[tuple[str, MatchScore]]) -> None:
    """Print a formatted ranking table to stdout.

    Args:
        ranked: Output from rank_songs().
    """
    if not ranked:
        print("No songs to rank.")
        return

    bar = "=" * 80
    thin = "-" * 80
    print(bar)
    print("  SONG-TO-FOOTAGE MATCH RANKING")
    print(bar)
    print(
        f"  {'#':<3} {'SONG':<36} {'OVERALL':>7}  "
        f"{'ARC':>5}  {'MOOD':>5}  {'PACE':>5}  {'COV':>5}"
    )
    print(thin)
    for rank, (path, ms) in enumerate(ranked, start=1):
        name = Path(path).name[:34]
        print(
            f"  {rank:<3} {name:<36} {ms.overall:>7.3f}  "
            f"{ms.arc_match:>5.3f}  {ms.mood_coverage:>5.3f}  "
            f"{ms.pace_match:>5.3f}  {ms.coverage_sufficiency:>5.3f}"
        )
    print(bar)
    print()
    print("  TOP PICK NOTES:")
    if ranked:
        _path, top = ranked[0]
        for note in top.notes:
            print(f"  - {note}")
    print(bar)
