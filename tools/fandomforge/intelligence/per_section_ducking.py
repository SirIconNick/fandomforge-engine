"""Per-section song ducking with section-aware duck depths and shelf EQ.

Replaces the flat per-cue duck_db value used by audio_engine.py with a
section-aware duck schedule. The duck depth and any EQ modifications are
determined by WHERE in the song the dialogue cue falls, not just that it
falls somewhere. This preserves musical energy at choruses while achieving
maximum intelligibility during quiet sections.

Duck rules
----------
- Verse with VO:       -15 dB  (standard clear speech)
- Chorus/peak with VO: -8 dB   (let music breathe, speech still readable)
- Breath/breakdown:    -20 dB  (quiet bed, speech is front and center)
- Drop moment with VO: -6 dB + high-shelf -4 dB at 3 kHz (space not kill)
- Bridge with VO:      -12 dB  (normal)
- Pre-chorus with VO:  -13 dB  (slightly heavier than chorus)
- Intro/outro:         -18 dB  (low-energy section default)

Integration with audio_engine.py
---------------------------------
    from tools.fandomforge.intelligence.per_section_ducking import (
        compute_duck_envelope,
        apply_duck_envelope_to_cues,
    )
    from tools.fandomforge.assembly.audio_engine import DialogueCue

    # Given a song structure and your initial list of DialogueCue objects:
    duck_points = compute_duck_envelope(dialogue_cues, song_structure)
    updated_cues = apply_duck_envelope_to_cues(dialogue_cues, duck_points)
    # Pass updated_cues to audio_engine.mix() - each cue now has the
    # section-correct duck_db. The DuckPoint list can also be used to drive
    # more advanced EQ automation in future.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DuckPoint:
    """A single ducking automation point for one dialogue cue window.

    Attributes:
        time_sec: Cue start position in the mix timeline (seconds).
        duck_db: How far to pull the song bed down during this cue window.
            Negative values represent attenuation (e.g. -15 = duck 15 dB).
        shelf_freq: Optional high-shelf EQ frequency in Hz applied to the
            song during this window. 0.0 means no shelf applied.
        shelf_db: Shelf gain in dB (negative = cut). Only meaningful when
            shelf_freq > 0.
        section_label: The song section label this cue falls into.
        section_mood: The mood tag of the host section.
        is_drop_section: True if the cue falls at or immediately after a drop.
    """

    time_sec: float
    duck_db: float
    shelf_freq: float = 0.0
    shelf_db: float = 0.0
    section_label: str = "unknown"
    section_mood: str = "unknown"
    is_drop_section: bool = False


@dataclass
class StyleProfile:
    """Optional caller-supplied style overrides.

    Any field left at its default (None or 0.0) is ignored and the standard
    section-based rule applies.

    Attributes:
        verse_duck_db: Override duck depth for verse sections.
        chorus_duck_db: Override duck depth for chorus sections.
        breakdown_duck_db: Override duck depth for breakdown sections.
        drop_duck_db: Override duck depth for drop sections.
        bridge_duck_db: Override duck depth for bridge sections.
        global_duck_offset_db: Added to every computed duck value. Useful for
            making the whole mix slightly drier or wetter without changing the
            relative per-section ratios.
        shelf_freq_override: If non-zero, overrides the drop-section shelf
            frequency for all sections.
        shelf_db_override: If non-zero, overrides the drop-section shelf gain.
    """

    verse_duck_db: Optional[float] = None
    chorus_duck_db: Optional[float] = None
    breakdown_duck_db: Optional[float] = None
    drop_duck_db: Optional[float] = None
    bridge_duck_db: Optional[float] = None
    global_duck_offset_db: float = 0.0
    shelf_freq_override: float = 0.0
    shelf_db_override: float = 0.0


# ---------------------------------------------------------------------------
# Default duck rules
# ---------------------------------------------------------------------------

# (duck_db, shelf_freq, shelf_db)
# shelf_freq=0 means no EQ shelf is applied
# Tuned for musical blend — song stays clearly audible under VO, like a
# movie trailer mix. Previous values (-15 to -20) muted the track so much
# that VO felt disconnected from the song. Add a ~600 Hz shelf dip on
# music-heavy sections to carve space for VO intelligibility without
# needing deep attenuation.
_DEFAULT_RULES: dict[str, tuple[float, float, float]] = {
    "verse":      (-9.0,  3000.0, -3.0),
    "pre-chorus": (-8.0,  3000.0, -3.0),
    "chorus":     (-5.0,  3000.0, -4.0),
    "bridge":     (-7.0,  3000.0, -3.0),
    "breakdown":  (-12.0, 0.0,    0.0),
    "intro":      (-10.0, 0.0,    0.0),
    "outro":      (-10.0, 0.0,    0.0),
    "unknown":    (-9.0,  3000.0, -3.0),
    # Drop override: applied when the cue sits inside a drop-flagged section
    "_drop":      (-4.0,  3000.0, -4.0),
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_section_for_time(
    time_sec: float,
    sections: list,
) -> Optional[object]:
    """Return the Section that contains time_sec.

    Args:
        time_sec: Timeline position to look up.
        sections: List of Section dataclass instances from song_structure.py.

    Returns:
        The matching Section, or None if not found (time_sec out of range).
    """
    for sec in sections:
        if sec.start_time <= time_sec < sec.end_time:
            return sec
    # Clamp to last section for cues that land on the very last sample
    if sections and time_sec >= sections[-1].start_time:
        return sections[-1]
    return None


def _is_near_drop(
    time_sec: float,
    drop_moments: list[float],
    window_sec: float = 2.0,
) -> bool:
    """Return True if time_sec falls within window_sec of any drop moment.

    Args:
        time_sec: Timeline position.
        drop_moments: List of drop timestamps from SongStructure.
        window_sec: How close to a drop counts as 'at the drop'.

    Returns:
        True when a drop is nearby.
    """
    for drop_t in drop_moments:
        if abs(time_sec - drop_t) <= window_sec:
            return True
    return False


def _apply_style_profile(
    base_duck_db: float,
    shelf_freq: float,
    shelf_db: float,
    section_label: str,
    is_drop: bool,
    profile: Optional[StyleProfile],
) -> tuple[float, float, float]:
    """Merge style profile overrides onto the computed base values.

    Args:
        base_duck_db: Base duck depth from _DEFAULT_RULES.
        shelf_freq: Base shelf frequency from _DEFAULT_RULES.
        shelf_db: Base shelf gain from _DEFAULT_RULES.
        section_label: Section label string.
        is_drop: Whether drop rules were applied.
        profile: Optional StyleProfile with caller overrides.

    Returns:
        Final (duck_db, shelf_freq, shelf_db) tuple.
    """
    if profile is None:
        return base_duck_db, shelf_freq, shelf_db

    label_map: dict[str, Optional[float]] = {
        "verse": profile.verse_duck_db,
        "pre-chorus": profile.verse_duck_db,  # pre-chorus treated like verse
        "chorus": profile.chorus_duck_db,
        "breakdown": profile.breakdown_duck_db,
        "bridge": profile.bridge_duck_db,
    }

    if is_drop and profile.drop_duck_db is not None:
        base_duck_db = profile.drop_duck_db
    elif section_label in label_map and label_map[section_label] is not None:
        base_duck_db = label_map[section_label]  # type: ignore[assignment]

    # Global offset
    final_duck = base_duck_db + profile.global_duck_offset_db

    # Clamp: max attenuation -30 dB, min attenuation 0 dB (never amplify)
    final_duck = max(-30.0, min(0.0, final_duck))

    # Shelf overrides
    if profile.shelf_freq_override > 0:
        shelf_freq = profile.shelf_freq_override
    if profile.shelf_db_override != 0.0:
        shelf_db = profile.shelf_db_override

    return final_duck, shelf_freq, shelf_db


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_duck_envelope(
    dialogue_cues: list,
    song_structure: object,
    style_profile: Optional[StyleProfile] = None,
) -> list[DuckPoint]:
    """Compute a per-cue duck schedule based on song section context.

    Iterates every dialogue cue, finds the song section it falls into, applies
    the appropriate duck depth and optional shelf EQ, then returns a list of
    DuckPoint objects that encode the full automation schedule.

    This function is read-only: it does not modify the incoming cues. Use
    apply_duck_envelope_to_cues() to patch the duck_db values back onto the
    actual DialogueCue objects before passing them to audio_engine.mix().

    Args:
        dialogue_cues: List of DialogueCue objects (from audio_engine.py).
            Each must have a .start_sec attribute and a .duck_db attribute.
        song_structure: SongStructure instance from song_structure.py. Must
            have .sections (list of Section), .drop_moments (list of float),
            and .transitions attributes.
        style_profile: Optional caller-supplied overrides. When None, the
            standard section-based defaults are used.

    Returns:
        List of DuckPoint objects in the same order as dialogue_cues.
    """
    sections = getattr(song_structure, "sections", [])
    drop_moments = getattr(song_structure, "drop_moments", [])
    results: list[DuckPoint] = []

    for cue in dialogue_cues:
        cue_time = float(getattr(cue, "start_sec", 0.0))

        section = _find_section_for_time(cue_time, sections)
        if section is None:
            logger.warning(
                "No section found for cue at %.2fs; using 'unknown' defaults.",
                cue_time,
            )
            section_label = "unknown"
            section_mood = "unknown"
            section_is_drop = False
        else:
            section_label = str(getattr(section, "label", "unknown"))
            section_mood = str(getattr(section, "mood", "unknown"))
            section_is_drop = bool(getattr(section, "is_drop", False))

        # Check drop proximity (cue within 2s of any drop moment)
        near_drop = _is_near_drop(cue_time, drop_moments, window_sec=2.0)
        use_drop_rules = section_is_drop or near_drop

        if use_drop_rules:
            base_duck, shelf_freq, shelf_db = _DEFAULT_RULES["_drop"]
        else:
            base_duck, shelf_freq, shelf_db = _DEFAULT_RULES.get(
                section_label, _DEFAULT_RULES["unknown"]
            )

        final_duck, final_shelf_freq, final_shelf_db = _apply_style_profile(
            base_duck, shelf_freq, shelf_db,
            section_label, use_drop_rules,
            style_profile,
        )

        dp = DuckPoint(
            time_sec=cue_time,
            duck_db=final_duck,
            shelf_freq=final_shelf_freq,
            shelf_db=final_shelf_db,
            section_label=section_label,
            section_mood=section_mood,
            is_drop_section=use_drop_rules,
        )
        results.append(dp)

        logger.debug(
            "Cue t=%.2fs section=%s mood=%s drop=%s => duck=%.1f dB  shelf=%.0f/%.1f",
            cue_time, section_label, section_mood, use_drop_rules,
            final_duck, final_shelf_freq, final_shelf_db,
        )

    return results


def apply_duck_envelope_to_cues(
    dialogue_cues: list,
    duck_points: list[DuckPoint],
) -> list:
    """Patch duck_db values from DuckPoint list back onto DialogueCue objects.

    Returns a new list of cues (shallow-copied via dataclasses.replace or
    attribute assignment). The original list is not mutated.

    Args:
        dialogue_cues: Original list of DialogueCue objects.
        duck_points: DuckPoint list returned by compute_duck_envelope().
            Must be the same length and order as dialogue_cues.

    Returns:
        List of cue objects with updated duck_db values. Objects are the
        same type as input cues; duck_db is set directly.

    Raises:
        ValueError: If lengths do not match.
    """
    if len(dialogue_cues) != len(duck_points):
        raise ValueError(
            f"dialogue_cues length ({len(dialogue_cues)}) must equal "
            f"duck_points length ({len(duck_points)})"
        )

    import copy
    updated: list = []
    for cue, dp in zip(dialogue_cues, duck_points):
        cue_copy = copy.copy(cue)
        cue_copy.duck_db = dp.duck_db
        updated.append(cue_copy)
        logger.debug(
            "Patched cue t=%.2fs: duck_db %.1f -> %.1f  (section=%s)",
            dp.time_sec, getattr(cue, "duck_db", 0.0), dp.duck_db, dp.section_label,
        )

    return updated


def log_duck_schedule(duck_points: list[DuckPoint]) -> None:
    """Print a human-readable duck schedule table to the logger at INFO level.

    Args:
        duck_points: List of DuckPoint instances from compute_duck_envelope().
    """
    if not duck_points:
        logger.info("Duck schedule is empty.")
        return

    logger.info("Per-section duck schedule:")
    logger.info(
        "  %-8s  %-14s  %-10s  %-8s  %-8s  %-6s  %s",
        "TIME", "SECTION", "MOOD", "DUCK_dB", "SHELF_HZ", "SH_dB", "DROP?"
    )
    for dp in duck_points:
        shelf_str = f"{dp.shelf_freq:.0f}" if dp.shelf_freq > 0 else "none"
        shelf_db_str = f"{dp.shelf_db:.1f}" if dp.shelf_freq > 0 else "n/a"
        logger.info(
            "  %-8.2f  %-14s  %-10s  %-8.1f  %-8s  %-6s  %s",
            dp.time_sec, dp.section_label, dp.section_mood,
            dp.duck_db, shelf_str, shelf_db_str,
            "YES" if dp.is_drop_section else "no",
        )
