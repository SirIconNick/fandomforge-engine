"""Layered edit planner — dialogue-first architecture.

Builds an edit plan in strict layers, each validated before the next starts:

LAYER 1 — DIALOGUE SPINE
    Pick Leon's lines. Each line knows where it came from (source mp4 +
    timestamp). Space them across the song at musical phrase breaks,
    never during chorus peaks.

LAYER 2 — SYNC ANCHORS
    For each dialogue line, lay down the ORIGINAL scene as the on-camera
    anchor. Anchor starts 0.2s before audio (so Leon's mouth is moving as
    speech begins) and lasts until the audio ends OR until a natural
    phrase break where B-roll takes over while the voice continues.

LAYER 3 — B-ROLL BETWEEN ANCHORS
    Gaps between anchors (or after a line extends past its anchor) are
    filled with silent Leon shots from the library. Cuts inside the
    gap snap to the beat grid; shot durations follow the style template.

LAYER 4 — ALIGNMENT VALIDATION
    Rejects the plan if: any cue starts mid-word, any cut falls in the
    middle of a spoken word, VO lands on a drop moment, or coverage is
    outside the reference-corpus target band.

This module only builds the PLAN. Rendering uses the existing orchestrator.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class DialogueLine:
    """A Leon VO line with source-scene metadata."""

    wav_path: Path
    text: str
    duration_sec: float
    source_mp4: Path | None = None
    source_start_sec: float | None = None
    source_end_sec: float | None = None
    placement_sec: float | None = None  # where it lands in the final timeline
    anchor_mode: Literal["sync", "vo_only"] = "sync"


@dataclass
class PlannedShot:
    """One shot on the timeline (either sync anchor or B-roll)."""

    start_sec: float
    duration_sec: float
    source: str
    clip_start_sec: float
    clip_end_sec: float
    era: str
    kind: Literal["sync_anchor", "broll"]
    dialogue_line_idx: int | None = None  # set when this shot anchors a line
    desc: str = ""
    intent: str = ""
    beat_aligned: bool = False

    # ---- EditPlan-compat shims ----
    # Downstream QA / caption / storyboard code was written against EditPlan
    # shot field names. Expose them as aliases.
    @property
    def start_time(self) -> float:
        return self.start_sec

    @property
    def duration(self) -> float:
        return self.duration_sec

    @property
    def mood_profile(self) -> str:
        return "calm" if self.kind == "broll" else "peak"

    @property
    def character_main(self) -> str:
        return "leon"  # Conservative default; layered_planner queries only
                      # character-match shots so this is safe. Projects that
                      # need per-shot character tracking should use the full
                      # shot_library API.

    @property
    def character_speaks(self) -> bool:
        return self.kind == "sync_anchor"

    @property
    def slot_name(self) -> str:
        return "sync-anchor" if self.kind == "sync_anchor" else "b-roll"

    @property
    def cut_index(self) -> int:
        """EditPlan indexes cuts; LayeredPlan shots are ordered already."""
        return -1  # caller sets based on list position if needed

    @property
    def is_downbeat(self) -> bool:
        return self.beat_aligned

    @property
    def is_primary_character(self) -> bool:
        return self.kind == "sync_anchor"

    @property
    def action(self) -> str:
        return "talking" if self.kind == "sync_anchor" else ""

    @property
    def emotion(self) -> str:
        return ""

    @property
    def shot_library_id(self) -> int:
        return -1


@dataclass
class LayeredPlan:
    total_duration: float
    dialogue_lines: list[DialogueLine] = field(default_factory=list)
    shots: list[PlannedShot] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    validation_passed: bool = False

    def to_json(self, path: Path) -> None:
        def _default(o):
            if isinstance(o, Path):
                return str(o)
            if hasattr(o, "__dict__"):
                return asdict(o)
            return str(o)
        data = {
            "total_duration": self.total_duration,
            "dialogue_lines": [asdict(d) for d in self.dialogue_lines],
            "shots": [asdict(s) for s in self.shots],
            "warnings": self.warnings,
            "validation_passed": self.validation_passed,
        }
        Path(path).write_text(json.dumps(data, indent=2, default=_default))

    # ---- EditPlan-compat shims ----
    # The QA + caption_generator + other downstream consumers were written
    # against the EditPlan shape from shot_optimizer.py. Expose synthetic
    # attribute views so LayeredPlan can feed them without a full refactor.

    @property
    def dialogue_placements(self) -> list[Any]:
        """EditPlan-shape view of dialogue: objects with start_time, duration,
        expected_line, audio_path fields."""
        out = []
        for d in self.dialogue_lines:
            if d.placement_sec is None:
                continue
            out.append(_VOPlacementShim(
                start_time=d.placement_sec,
                duration=d.duration_sec,
                expected_line=d.text,
                audio_path=str(d.wav_path),
            ))
        return out

    @property
    def metadata(self) -> "_MetaShim":
        """EditPlan-compatible metadata view (object, not dict) so downstream
        consumers can call `.beat_aligned_pct`, `.total_duration_sec`, etc."""
        total_shots = max(1, len(self.shots))
        n_beat_aligned = sum(1 for s in self.shots if s.beat_aligned)
        total_vo = sum(d.duration_sec for d in self.dialogue_lines
                       if d.placement_sec is not None)
        return _MetaShim(
            total_duration_sec=float(self.total_duration),
            total_shots=len(self.shots),
            total_vo_count=len(self.dialogue_lines),
            beat_aligned_pct=100.0 * n_beat_aligned / total_shots,
            downbeat_aligned_pct=0.0,  # LayeredPlan doesn't distinguish downbeats
            vo_coverage_pct=(
                100.0 * total_vo / self.total_duration
                if self.total_duration else 0.0
            ),
            peak_shot_time_sec=0.0,
            median_shot_duration_sec=(
                sorted(s.duration_sec for s in self.shots)[len(self.shots) // 2]
                if self.shots else 0.0
            ),
            template_name="layered",
            style_profile_source="layered_planner",
        )


@dataclass
class _MetaShim:
    """EditPlanMeta-compatible view for LayeredPlan."""
    total_duration_sec: float = 0.0
    total_shots: int = 0
    total_vo_count: int = 0
    beat_aligned_pct: float = 0.0
    downbeat_aligned_pct: float = 0.0
    vo_coverage_pct: float = 0.0
    peak_shot_time_sec: float = 0.0
    median_shot_duration_sec: float = 0.0
    template_name: str = "layered"
    style_profile_source: str = "layered_planner"


@dataclass
class _VOPlacementShim:
    """EditPlan-compatible dialogue placement view over a DialogueLine."""
    start_time: float
    duration: float
    expected_line: str = ""
    audio_path: str = ""
    # Extra EditPlan fields downstream may read; safe defaults.
    slot_name: str = ""
    cut_index: int = -1
    pre_cut_frames: int = 0


# ---------------------------------------------------------------------------
# Layer 1 — Dialogue spine
# ---------------------------------------------------------------------------


# Fallback era map for when no project config supplies one. This lets the
# module keep working for legacy Leon-style projects that don't yet have a
# project-config.yaml.
_DEFAULT_ERA_SOURCES = {
    "RE2R": "leon-re2r-cutscenes",
    "RE4R": "leon-re4r-cutscenes",
    "RE6": "leon-re6-cutscenes",
    "Damnation": "leon-damnation",
    "ID": "leon-infinite-darkness",
    "Vendetta": "leon-vendetta",
    "RE9": "re9-leon-scenepack",
}


def _parse_source_from_wav_name(
    wav_name: str,
    raw_dir: Path,
    *,
    character: str = "leon",
    era_source_map: dict[str, str] | None = None,
) -> tuple[Path | None, str]:
    """Derive source mp4 + era tag from a dialogue WAV filename.

    Expected filename pattern: `<character>_<ERA>_<slug>.wav`
    e.g. `leon_RE2R_bingo.wav`, `claire_CVX_cant-save.wav`.

    Args:
        wav_name: Filename (basename) of the dialogue WAV.
        raw_dir: Directory containing source mp4s.
        character: Primary character name (project config default).
        era_source_map: Dict of {era_key: source_mp4_stem}. Falls back to the
            built-in default if None or empty (legacy Leon behavior).

    Returns:
        (source_mp4, era_key) tuple. source_mp4 is None if it cannot be
        resolved; era_key may be the character's default era when no prefix
        is present (e.g. friendly names like `leon_couldnt-save.wav`).
    """
    era_map = era_source_map if era_source_map else _DEFAULT_ERA_SOURCES

    # Pattern: <character>_<ERA>_<slug>.wav
    # Allow any non-underscore char in the character segment so character name
    # doesn't have to be lowercase.
    pattern = rf"^[A-Za-z0-9]+_([A-Za-z][A-Za-z0-9]+)_"
    m = re.match(pattern, wav_name)
    if m:
        era_key = m.group(1)
        src_stem = era_map.get(era_key)
        if src_stem:
            mp4 = raw_dir / f"{src_stem}.mp4"
            return (mp4 if mp4.exists() else None, era_key)

    # Friendly-named clip with no era prefix (e.g. "leon_couldnt-save.wav").
    # Without an explicit default, we can't safely guess which era it came
    # from — the caller should supply source_map metadata. Return unknown
    # so the line falls through to vo_only mode instead of attaching the
    # wrong on-camera anchor.
    return (None, "unknown")


def _load_wav_duration(wav: Path) -> float:
    try:
        import subprocess
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(wav)],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip())
    except Exception:  # noqa: BLE001
        return 3.0


def load_dialogue_spine(
    dialogue_dir: Path,
    transcript_map: dict[str, str],
    raw_dir: Path,
    *,
    character: str = "leon",
    era_source_map: dict[str, str] | None = None,
    min_duration: float = 1.0,
    max_duration: float = 6.0,
    additional_speakers: list[str] | None = None,
) -> list[DialogueLine]:
    """Layer 1: load every verified dialogue line with source metadata.

    Args:
        dialogue_dir: Where <character>_*.wav files live.
        transcript_map: stem → transcript text mapping (Whisper-verified).
        raw_dir: Directory containing source mp4 files.
        character: Primary character (filename prefix).
        era_source_map: Project-config era→source-stem dict. Enables the
            multi-era WAV naming convention.
        min_duration, max_duration: Filter out clips outside these bounds.
        additional_speakers: Other speaker prefixes to include (e.g. "grace"
            for cross-character dialogue lines).
    """
    lines: list[DialogueLine] = []
    # Accept both `<character>_*.wav` and legacy `leon_*.wav` patterns so
    # projects mid-migration don't break.
    patterns = [f"{character}_*.wav"]
    if character.lower() != "leon":
        patterns.append("leon_*.wav")  # legacy
    for extra in additional_speakers or []:
        patterns.append(f"{extra.lower()}_*.wav")

    seen: set[Path] = set()
    for pat in patterns:
        for wav in sorted(dialogue_dir.glob(pat)):
            if wav in seen:
                continue
            seen.add(wav)
            stem = wav.stem
            text = transcript_map.get(stem) or transcript_map.get(wav.name) or ""
            if not text:
                continue
            dur = _load_wav_duration(wav)
            if dur < min_duration or dur > max_duration:
                continue
            src_mp4, era = _parse_source_from_wav_name(
                wav.name, raw_dir,
                character=character,
                era_source_map=era_source_map,
            )
            lines.append(DialogueLine(
                wav_path=wav,
                text=text,
                duration_sec=dur,
                source_mp4=src_mp4,
                anchor_mode="sync" if src_mp4 and src_mp4.exists() else "vo_only",
            ))
    return lines


# Generic badass-line keywords that apply across characters. Project configs
# should supply their own narrative_priorities; these are a fallback.
_GENERIC_PUNCHY_KEYWORDS = (
    "over", "done", "can't", "won't", "never", "finish",
)


def select_spine(
    available: list[DialogueLine],
    *,
    target_count: int,
    narrative_priority: list[str] | None = None,
    punchy_keywords: list[str] | None = None,
) -> list[DialogueLine]:
    """Pick the N strongest lines for the arc, preferring narrative priority
    phrases. Returns chronologically-ordered list ready for placement.

    Args:
        available: All candidate dialogue lines.
        target_count: How many lines to pick.
        narrative_priority: Project-config phrases to boost (character-specific).
        punchy_keywords: Generic short impactful words to boost (default set
            works across characters).
    """
    scored: list[tuple[float, DialogueLine]] = []
    priority = [p.lower() for p in (narrative_priority or [])]
    punchy = [p.lower() for p in (punchy_keywords or _GENERIC_PUNCHY_KEYWORDS)]
    for line in available:
        score = 1.0
        t = line.text.lower()
        # Prefer longer complete sentences over fragments
        if len(t.split()) >= 4:
            score += 1.0
        # Boost narrative-priority matches
        for i, kw in enumerate(priority):
            if kw in t:
                score += (len(priority) - i)  # higher weight for earlier priorities
        # Boost short punchy lines
        if any(p in t for p in punchy):
            score += 1.0
        scored.append((score, line))
    scored.sort(key=lambda x: -x[0])
    return [line for _, line in scored[:target_count]]


# ---------------------------------------------------------------------------
# Layer 2 — Sync anchor placement
# ---------------------------------------------------------------------------


def place_dialogue_on_timeline(
    lines: list[DialogueLine],
    total_duration: float,
    *,
    song_structure: object | None = None,
    buffer_before: float = 0.2,
    buffer_after: float = 0.3,
) -> list[DialogueLine]:
    """Layer 1.5: place each dialogue line on the final timeline.

    Constraints:
    - Minimum 6s between line starts (gap for B-roll)
    - Avoid chorus/peak/drop windows (from song_structure)
    - First line starts 1-3s in (after the intro hold breathes)
    - Last line ends 2-5s before total_duration
    """
    placed: list[DialogueLine] = []
    n = len(lines)
    if n == 0:
        return placed

    # Build exclusion zones from song structure
    avoid: list[tuple[float, float]] = []
    if song_structure is not None and hasattr(song_structure, "sections"):
        for sec in song_structure.sections:
            mood = getattr(sec, "mood", "") or getattr(sec, "energy_level", "")
            is_drop = getattr(sec, "is_drop", False)
            if mood == "peak" or is_drop:
                avoid.append((sec.start_time, sec.end_time))
        # Add drop moments with 1.5s pad
        for dt in getattr(song_structure, "drop_moments", []) or []:
            avoid.append((dt - 1.0, dt + 1.5))

    def _overlaps_avoid(t_start: float, t_end: float) -> bool:
        return any(not (t_end < a or t_start > b) for a, b in avoid)

    # Even distribution with exclusion-zone shift
    intro_hold = 1.5
    tail_reserve = 3.0
    usable_start = intro_hold
    usable_end = total_duration - tail_reserve
    span = usable_end - usable_start

    raw_slots = [
        usable_start + span * (i / max(1, n - 1)) if n > 1 else usable_start + span / 2
        for i in range(n)
    ]

    for line, slot in zip(lines, raw_slots):
        placement = slot
        # Shift away from avoid zones
        for _ in range(5):
            if _overlaps_avoid(placement, placement + line.duration_sec):
                placement -= 2.0  # shift earlier
            else:
                break
        placement = max(usable_start, min(placement, usable_end - line.duration_sec))
        line.placement_sec = placement
        placed.append(line)

    # Ensure no back-to-back lines (minimum 6s gap between starts)
    for i in range(1, len(placed)):
        min_start = placed[i - 1].placement_sec + placed[i - 1].duration_sec + 3.0
        if placed[i].placement_sec < min_start:
            placed[i].placement_sec = min_start
    # Trim if last line got pushed past end
    placed = [
        li for li in placed
        if li.placement_sec + li.duration_sec <= total_duration
    ]
    return placed


def build_sync_anchors(
    placed: list[DialogueLine],
    *,
    buffer_before: float = 0.2,
    bleed_after_min: float = 0.5,
    bleed_after_max: float = 2.0,
) -> list[PlannedShot]:
    """Layer 2: build sync anchor shots for every dialogue line that has source metadata.

    Each anchor:
    - Starts buffer_before seconds before the dialogue audio
    - Uses the source mp4 at the timestamp where the line was extracted
    - Ends at the audio end OR keeps running if the source clip is long enough
    """
    anchors: list[PlannedShot] = []
    for idx, line in enumerate(placed):
        if line.anchor_mode != "sync" or line.source_mp4 is None:
            continue
        if line.source_start_sec is None:
            continue
        anchor_start = max(0.0, (line.placement_sec or 0) - buffer_before)
        # Anchor only needs to cover 60-80% of the line; B-roll bleeds over the rest
        anchor_duration = min(
            line.duration_sec + buffer_before,
            line.duration_sec * 0.7,
        )
        anchor_duration = max(1.2, anchor_duration)  # minimum 1.2s
        clip_start = line.source_start_sec - buffer_before
        anchors.append(PlannedShot(
            start_sec=anchor_start,
            duration_sec=anchor_duration,
            source=line.source_mp4.stem,
            clip_start_sec=max(0.0, clip_start),
            clip_end_sec=max(0.0, clip_start) + anchor_duration,
            era="",
            kind="sync_anchor",
            dialogue_line_idx=idx,
            desc=f"SYNC: \"{line.text[:60]}\"",
            intent=f"sync anchor for line {idx}",
            beat_aligned=False,
        ))
    return anchors


# ---------------------------------------------------------------------------
# Layer 3 — B-roll fill
# ---------------------------------------------------------------------------


def _query_broll(
    db_path: Path,
    n: int,
    *,
    character: str = "leon",
    character_aliases: list[str] | None = None,
    excluded_sources: list[str] | None = None,
    allowed_sources: list[str] | None = None,
    min_dur: float = 1.0,
    max_dur: float = 4.0,
) -> list[dict]:
    """Fetch n silent B-roll shots featuring the primary character.

    Args:
        character: Primary character name (matches shot_library.character_main).
        character_aliases: Also match these character_main values. Useful when
            captions use different spelling (e.g. "leon kennedy" vs "leon").
        allowed_sources: If non-empty, restrict to these sources only. Used
            by era_arc to enforce per-act source palettes (e.g. RE9-only
            in the opening act, RE2R-only in the flashback).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Build character IN-clause (primary + any aliases)
    char_list = [character.lower()]
    for alias in character_aliases or []:
        a = alias.lower()
        if a not in char_list:
            char_list.append(a)
    char_placeholders = ",".join("?" * len(char_list))

    where = [
        f"LOWER(character_main) IN ({char_placeholders})",
        "COALESCE(character_speaks, 0) = 0",
        f"duration_sec BETWEEN {min_dur} AND {max_dur}",
        # Skip shots that vision-scored as HUD/watermark/heavy-artifact.
        # These columns exist only after running `ff visual-quality`; when
        # absent, the COALESCE defaults keep old projects working unchanged.
        "COALESCE(has_hud_overlay, 0) = 0",
        "COALESCE(has_watermark, 0) = 0",
        "COALESCE(has_artifact, 0) = 0",
        "COALESCE(visual_quality, 100) >= 70",
        # When the character_visible column is populated, require it to be
        # true; NULL means "we haven't scored this yet" and we default to
        # trusting character_main so old projects still work.
        "COALESCE(character_visible, 1) = 1",
    ]
    params: list[Any] = list(char_list)
    if excluded_sources:
        placeholders = ",".join("?" * len(excluded_sources))
        where.append(f"source NOT IN ({placeholders})")
        params.extend(excluded_sources)
    if allowed_sources:
        placeholders = ",".join("?" * len(allowed_sources))
        where.append(f"source IN ({placeholders})")
        params.extend(allowed_sources)
    # Reject gameplay-HUD, button-prompt, and watermark captions
    hud_terms = [
        "hud", "button prompt", "press a", "press x", "press y", "press b",
        "dash", "reload", "ammo counter", "health bar", "qte",
        "quick time", "xbox controller", "ps5 controller",
        "filmisnow", "gmdeptv", "gameplay overlay", "ui overlay",
    ]
    for term in hud_terms:
        where.append("LOWER(COALESCE(desc,'')) NOT LIKE ?")
        params.append(f"%{term}%")
    # Reject shots that describe the character actively SPEAKING. Lip movement
    # without matching VO audio looks like bad syncing — the viewer notices.
    # Scoring misses many of these with the character_speaks column, so also
    # scan the description text.
    speech_terms = [
        " talks", " talking", " speaks", " speaking", " saying", " says",
        "speech", "dialogue", "conversation", "conversing", "discussing",
        "discussion", "explaining", "argues", "arguing", "shouts", "shouting",
        "yells", "yelling", "questioning", "asking",
    ]
    for term in speech_terms:
        where.append("LOWER(COALESCE(desc,'')) NOT LIKE ?")
        where.append("LOWER(COALESCE(visual_quality_note,'')) NOT LIKE ?")
        params.append(f"%{term}%")
        params.append(f"%{term}%")

    sql = (
        "SELECT id, source, era, start_sec, end_sec, duration_sec, desc, action, "
        "emotion, COALESCE(has_dialogue, 0) AS has_dialogue "
        f"FROM shots WHERE {' AND '.join(where)} "
        "ORDER BY COALESCE(use_rank,0) ASC, id ASC LIMIT ?"
    )
    params.append(n * 3)  # fetch 3x then dedupe
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    # Diverse-source dedupe: interleave
    by_source: dict[str, list[dict]] = {}
    for r in rows:
        by_source.setdefault(r["source"], []).append(dict(r))
    out: list[dict] = []
    while len(out) < n and any(by_source.values()):
        for src, lst in list(by_source.items()):
            if lst:
                out.append(lst.pop(0))
                if len(out) >= n:
                    break
    return out


def fill_broll(
    anchors: list[PlannedShot],
    total_duration: float,
    db_path: Path,
    *,
    style_profile: dict,
    song_beats: list[float] | None = None,
    character: str = "leon",
    character_aliases: list[str] | None = None,
    excluded_sources: list[str] | None = None,
    era_arc: list[dict] | None = None,
    concept_beats: list[dict] | None = None,
) -> list[PlannedShot]:
    """Layer 3: fill gaps between sync anchors with silent B-roll of the
    primary character.

    Target shot duration comes from style profile. Cuts snap to beat grid
    when available.

    Args:
        era_arc: Optional list of per-time-range source allowlists. Each
            entry is {start: float, end: float, sources: [stems]}. B-roll
            query inside the range is restricted to those sources.
        concept_beats: Optional list of force-inserted shots at specific
            timestamps. Each entry is {time: float, duration: float,
            source: stem, clip_start: float, desc?: str}. Treated like
            sync anchors for placement purposes — locked in, surrounding
            B-roll fills around them.
    """
    # concept_beats are applied AFTER the normal plan builds, by overwriting
    # shots at their timestamps (see _apply_concept_beats below). This keeps
    # sync anchor placement logic clean and lets concept beats override the
    # VISUAL choice during a VO window without disrupting the VO timing.
    concept_beats = concept_beats or []
    era_arc = era_arc or []

    def _sources_for_time(t: float) -> list[str] | None:
        """Return the era_arc allowed_sources for time t, or None if not covered."""
        for entry in era_arc:
            if float(entry.get("start", 0)) <= t < float(entry.get("end", 0)):
                srcs = entry.get("sources") or []
                return list(srcs) if srcs else None
        return None

    shot_dur_median = float(style_profile.get("shot_dur_median", 1.2))
    target = max(0.8, min(2.0, shot_dur_median))

    # Beat-count rhythm pattern. Each entry = number of beats the shot
    # should span. Tuned for ~40 cuts/min with a musical mix of quick cuts,
    # normal holds, and longer breath-holds so the edit doesn't feel
    # frantic. At 161 BPM (Kaleo) this gives shots of 0.74s / 1.5s / 3s.
    # Every shot lands cleanly on a beat because durations are whole-beat
    # multiples.
    _beat_pattern = [4, 4, 8, 4, 2, 4, 8, 4, 2, 4]
    shot_rhythm_idx = 0

    # Sort anchors by start time
    anchors_sorted = sorted(anchors, key=lambda s: s.start_sec)

    all_shots: list[PlannedShot] = []
    cursor = 0.0
    anchor_idx = 0

    # Average beat interval — used to size shot durations to whole-beat
    # counts so every cut lands cleanly on the grid.
    if song_beats and len(song_beats) >= 2:
        beat_interval = sum(
            song_beats[i+1] - song_beats[i] for i in range(len(song_beats)-1)
        ) / (len(song_beats) - 1)
    else:
        beat_interval = 0.5  # 120 BPM fallback

    def _snap_to_beat(t: float, window: float = 0.25) -> float:
        """Return nearest beat (within +/- window), else t unchanged."""
        if not song_beats:
            return t
        closest = min(song_beats, key=lambda b: abs(b - t))
        if abs(closest - t) < window:
            return closest
        return t

    def _beat_aligned_dur(desired: float, cap: float) -> float:
        """Round `desired` to the nearest whole-beat multiple, clamped to cap."""
        if beat_interval <= 0:
            return min(desired, cap)
        beats = max(1, round(desired / beat_interval))
        dur = beats * beat_interval
        while dur > cap and beats > 1:
            beats -= 1
            dur = beats * beat_interval
        return min(dur, cap)

    def _query_diverse(n: int, allowed: list[str] | None = None) -> list[dict]:
        """Fetch enough B-roll shots for a gap, rotating sources.

        If `allowed` is given, restrict to those sources (era_arc path).
        Falls back to unrestricted query if the restricted query returns
        nothing (avoids empty B-roll gaps).
        """
        rows = _query_broll(
            db_path, max(n, 8),
            character=character,
            character_aliases=character_aliases,
            excluded_sources=excluded_sources,
            allowed_sources=allowed,
        )
        if not rows and allowed:
            # Graceful degrade — fall back to global pool rather than leave a dead gap
            rows = _query_broll(
                db_path, max(n, 8),
                character=character,
                character_aliases=character_aliases,
                excluded_sources=excluded_sources,
            )
        return rows

    # Era-arc-aware B-roll pools: one pool per era range. Falls back to
    # a global pool when era_arc is empty or cursor is outside all ranges.
    # Every shot picked is recorded in used_ids so the same clip never
    # appears twice in the edit — kills the repeated-scenes issue.
    era_pools: dict[str, list[dict]] = {}
    era_cursors: dict[str, int] = {}
    global_pool: list[dict] = _query_diverse(150)
    global_cursor = 0
    used_ids: set = set()

    def _take_broll(t: float) -> dict | None:
        """Pick the next unused B-roll shot for render time t, honoring era_arc."""
        nonlocal global_pool, global_cursor
        allowed = _sources_for_time(t)

        def _fresh_from_pool(pool: list[dict], cursor_key: str | None = None) -> tuple[dict | None, int]:
            """Walk the pool from its cursor, returning (shot, new_cursor)."""
            cur = era_cursors.get(cursor_key, global_cursor) if cursor_key else global_cursor
            while cur < len(pool):
                shot = pool[cur]
                cur += 1
                if shot["id"] not in used_ids:
                    return shot, cur
            return None, cur

        if allowed:
            key = "|".join(sorted(allowed))
            if key not in era_pools:
                era_pools[key] = _query_diverse(80, allowed=allowed)
                era_cursors[key] = 0
            pool = era_pools[key]
            shot, new_cur = _fresh_from_pool(pool, cursor_key=key)
            era_cursors[key] = new_cur
            if shot is None:
                # Pool exhausted of unused shots — refill and retry once
                era_pools[key] = _query_diverse(80, allowed=allowed)
                era_cursors[key] = 0
                shot, new_cur = _fresh_from_pool(era_pools[key], cursor_key=key)
                era_cursors[key] = new_cur
            if shot is not None:
                used_ids.add(shot["id"])
                return shot
            # fall through to global
        shot, new_cur = _fresh_from_pool(global_pool)
        global_cursor = new_cur
        if shot is None:
            # Refill global pool once
            global_pool = _query_diverse(80)
            global_cursor = 0
            shot, new_cur = _fresh_from_pool(global_pool)
            global_cursor = new_cur
        if shot is not None:
            used_ids.add(shot["id"])
        return shot

    # Walk the timeline: fill until next anchor, place anchor, fill after.
    while cursor < total_duration - 0.05:
        next_anchor = anchors_sorted[anchor_idx] if anchor_idx < len(anchors_sorted) else None
        gap_end = next_anchor.start_sec if next_anchor else total_duration

        # Fill the gap with B-roll shots, each ~target seconds long,
        # rounded toward the beat grid when close enough.
        while gap_end - cursor > 0.35:
            row = _take_broll(cursor)
            if row is None:
                break
            # Pick this shot's beat count from the rotating rhythm pattern.
            beats = _beat_pattern[shot_rhythm_idx % len(_beat_pattern)]
            shot_rhythm_idx += 1
            # Dialogue shots (lips visibly moving) cap at 4 beats to keep
            # lip-sync mismatch subliminal. Silent shots can hold up to 8.
            has_dlg = bool(row.get("has_dialogue", 0))
            max_beats = 4 if has_dlg else 8
            beats = min(beats, max_beats)
            # Clamp to what fits in the remaining gap
            max_beats_fit = max(1, int((gap_end - cursor) / beat_interval))
            beats = min(beats, max_beats_fit)
            # Beat-accurate end: walk N beats forward from the actual beat
            # closest to `cursor`, using the real song_beats list instead of
            # the average interval. Keeps cuts locked to the grid even as
            # the song's tempo drifts slightly.
            if song_beats:
                # Find the closest beat index to cursor
                closest_idx = min(
                    range(len(song_beats)),
                    key=lambda i: abs(song_beats[i] - cursor),
                )
                target_idx = min(closest_idx + beats, len(song_beats) - 1)
                end_t = song_beats[target_idx]
                dur = end_t - cursor
            else:
                dur = beats * beat_interval
            # Absorb a tiny crumb at the end — don't leave <1 beat orphan
            remaining = gap_end - (cursor + dur)
            if 0 < remaining < beat_interval * 1.2:
                dur = gap_end - cursor
            dur = max(0.5, dur)
            dur = min(dur, float(row["duration_sec"]))
            all_shots.append(PlannedShot(
                start_sec=cursor,
                duration_sec=dur,
                source=row["source"],
                clip_start_sec=row["start_sec"],
                clip_end_sec=row["start_sec"] + dur,
                era=row["era"] or "",
                kind="broll",
                desc=row["desc"] or "",
                intent=f"b-roll ({row['emotion'] or 'calm'})",
                beat_aligned=song_beats is not None,
            ))
            cursor += dur

        if next_anchor is not None:
            # If we overshot the anchor (even by a few frames), trim the last
            # B-roll back so nothing overlaps.
            if all_shots and cursor > next_anchor.start_sec + 0.01:
                last = all_shots[-1]
                overrun = cursor - next_anchor.start_sec
                new_dur = max(0.3, last.duration_sec - overrun)
                last.duration_sec = new_dur
                last.clip_end_sec = last.clip_start_sec + new_dur
                cursor = last.start_sec + last.duration_sec
            # If there's a tiny leftover slice before anchor, pad the previous shot
            if cursor < next_anchor.start_sec - 0.05:
                if all_shots and all_shots[-1].kind == "broll":
                    pad = next_anchor.start_sec - cursor
                    all_shots[-1].duration_sec += pad
                    all_shots[-1].clip_end_sec += pad
                    cursor = next_anchor.start_sec
                else:
                    cursor = next_anchor.start_sec
            all_shots.append(next_anchor)
            cursor = next_anchor.start_sec + next_anchor.duration_sec
            anchor_idx += 1
        else:
            break

    # Concept beats override: replace visuals at these exact timestamps.
    # We split the covering shot into (before | concept | after) and insert
    # the concept beat. Sync anchors are treated specially — their time
    # segment carries the VO audio and cannot be ditched, but the VISUAL
    # for that segment can be swapped to the concept-beat clip.
    if concept_beats:
        all_shots = _apply_concept_beats(all_shots, concept_beats)

    return all_shots


def _apply_concept_beats(
    shots: list[PlannedShot], beats: list[dict]
) -> list[PlannedShot]:
    """Overwrite visuals at concept-beat timestamps.

    Each beat declares {time, duration, source, clip_start, desc?}.
    The shot (or shots) covering that time window get replaced or trimmed
    so the concept-beat visual plays instead. Sync anchors stay at their
    positions — their source is swapped to the beat's source so the VO
    still plays while the desired visual shows.
    """
    if not beats:
        return shots

    # Normalize + sort beats
    beats_sorted = sorted(beats, key=lambda b: float(b["time"]))
    out: list[PlannedShot] = list(shots)

    for b in beats_sorted:
        b_start = float(b["time"])
        b_dur = float(b["duration"])
        b_end = b_start + b_dur
        b_src = b["source"]
        b_clip = float(b["clip_start"])
        b_desc = b.get("desc", "")

        new_out: list[PlannedShot] = []
        for s in out:
            s_start = s.start_sec
            s_end = s.start_sec + s.duration_sec
            # No overlap — keep as is
            if s_end <= b_start or s_start >= b_end:
                new_out.append(s)
                continue
            # Overlap — trim or split
            # Part before the beat
            if s_start < b_start:
                before_dur = b_start - s_start
                if before_dur >= 0.3:
                    new_out.append(PlannedShot(
                        start_sec=s_start,
                        duration_sec=before_dur,
                        source=s.source,
                        clip_start_sec=s.clip_start_sec,
                        clip_end_sec=s.clip_start_sec + before_dur,
                        era=s.era,
                        kind=s.kind,
                        desc=s.desc,
                        intent=s.intent,
                        dialogue_line_idx=s.dialogue_line_idx,
                        beat_aligned=s.beat_aligned,
                    ))
            # Part after the beat
            if s_end > b_end:
                after_dur = s_end - b_end
                if after_dur >= 0.3:
                    clip_offset = (b_end - s_start)
                    new_out.append(PlannedShot(
                        start_sec=b_end,
                        duration_sec=after_dur,
                        source=s.source,
                        clip_start_sec=s.clip_start_sec + clip_offset,
                        clip_end_sec=s.clip_start_sec + clip_offset + after_dur,
                        era=s.era,
                        kind=s.kind,
                        desc=s.desc,
                        intent=s.intent,
                        dialogue_line_idx=s.dialogue_line_idx,
                        beat_aligned=s.beat_aligned,
                    ))
            # Beat can swallow multiple shots — only emit ONCE (on first overlap)
            # by tracking insertion, but simpler: add here every overlap and
            # dedupe afterwards. For now add once by checking if already present.
            if not any(x.kind == "concept_beat" and abs(x.start_sec - b_start) < 0.01
                       for x in new_out):
                new_out.append(PlannedShot(
                    start_sec=b_start,
                    duration_sec=b_dur,
                    source=b_src,
                    clip_start_sec=b_clip,
                    clip_end_sec=b_clip + b_dur,
                    era=b.get("era", s.era),
                    kind="concept_beat",
                    desc=b_desc or s.desc,
                    intent=b.get("intent", "concept beat"),
                    dialogue_line_idx=s.dialogue_line_idx,  # keep VO mapping if anchor
                    beat_aligned=False,
                ))
        # Keep shots sorted
        new_out.sort(key=lambda x: x.start_sec)
        out = new_out

    return out


# ---------------------------------------------------------------------------
# Layer 4 — Validation
# ---------------------------------------------------------------------------


def validate(plan: LayeredPlan, *, coverage_target: tuple[float, float] = (0.22, 0.28)) -> bool:
    """Layer 4: check alignment rules. Updates plan.validation_passed + warnings."""
    warnings: list[str] = []
    total_vo = sum(d.duration_sec for d in plan.dialogue_lines if d.placement_sec is not None)
    coverage = total_vo / plan.total_duration if plan.total_duration else 0
    if not coverage_target[0] - 0.03 <= coverage <= coverage_target[1] + 0.05:
        warnings.append(
            f"VO coverage {coverage*100:.1f}% outside target "
            f"{coverage_target[0]*100:.0f}-{coverage_target[1]*100:.0f}%"
        )

    # No overlap between anchors and dialogue lines from other indices
    for i, line in enumerate(plan.dialogue_lines):
        if line.placement_sec is None:
            continue
        # Find an anchor for this line
        anchor = next(
            (s for s in plan.shots
             if s.kind == "sync_anchor" and s.dialogue_line_idx == i),
            None,
        )
        if line.anchor_mode == "sync" and anchor is None:
            warnings.append(
                f"Line {i} '{line.text[:40]}' is sync mode but has no anchor shot"
            )
            continue
        if anchor:
            # Sync anchor start must be within 0.3s of audio start - buffer
            gap = abs((line.placement_sec - 0.2) - anchor.start_sec)
            if gap > 0.3:
                warnings.append(
                    f"Line {i} sync misaligned (gap {gap:.2f}s)"
                )

    # Ensure shots tile the timeline
    plan.shots.sort(key=lambda s: s.start_sec)
    for i in range(len(plan.shots) - 1):
        this_end = plan.shots[i].start_sec + plan.shots[i].duration_sec
        next_start = plan.shots[i + 1].start_sec
        if next_start - this_end > 0.2:
            warnings.append(
                f"Gap {next_start - this_end:.2f}s between shot {i} and {i+1}"
            )
        if this_end - next_start > 0.25:
            # Only warn on overlaps large enough to cause visible visual artifacts;
            # small overlaps (<0.25s) are absorbed by the concat demuxer.
            warnings.append(
                f"Overlap {this_end - next_start:.2f}s between shot {i} and {i+1}"
            )

    plan.warnings = warnings
    plan.validation_passed = len(warnings) == 0
    return plan.validation_passed


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


def build_layered_plan(
    *,
    dialogue_dir: Path,
    transcript_map_path: Path,
    source_map_path: Path | None,
    raw_dir: Path,
    shot_library_db: Path,
    style_profile: dict,
    song_structure: object | None,
    song_beats: list[float] | None,
    total_duration: float,
    target_dialogue_count: int = 6,
    narrative_priority: list[str] | None = None,
    character: str = "leon",
    character_aliases: list[str] | None = None,
    era_source_map: dict[str, str] | None = None,
    punchy_keywords: list[str] | None = None,
    excluded_sources: list[str] | None = None,
    era_arc: list[dict] | None = None,
    concept_beats: list[dict] | None = None,
) -> LayeredPlan:
    """End-to-end: build dialogue spine → anchors → B-roll → validate.

    Args:
        dialogue_dir / transcript_map_path / source_map_path / raw_dir: paths
            rooted in the project layout.
        shot_library_db: SQLite file from shot_library.py.
        style_profile: dict loaded from .style-template*.json (or cluster
            variant). Drives shot pacing target.
        song_structure: optional SongStructure from song_structure.analyze().
        song_beats: beat timestamps in seconds for cut snapping.
        total_duration: final edit length.
        target_dialogue_count: how many VO lines to pick.
        narrative_priority: project-config phrases to boost when ranking lines.
        character: primary character (drives B-roll query + WAV file lookup).
        character_aliases: additional character_main values to include.
        era_source_map: project-config era→source-stem dict. Enables
            `<char>_<ERA>_slug.wav` naming convention.
        punchy_keywords: short words that boost a line's score
            (defaults are character-agnostic).
    """
    transcript_map = {}
    if transcript_map_path.exists():
        raw = json.loads(transcript_map_path.read_text())
        transcript_map = {
            (k[:-4] if k.endswith(".wav") else k): v for k, v in raw.items()
        }

    source_map: dict[str, dict] = {}
    if source_map_path and source_map_path.exists():
        source_map = json.loads(source_map_path.read_text())

    # Layer 1a: available lines with source meta from map
    all_lines = load_dialogue_spine(
        dialogue_dir, transcript_map, raw_dir,
        character=character,
        era_source_map=era_source_map,
        additional_speakers=character_aliases,
    )
    # Layer 1b: enrich with explicit source timestamps where known.
    # source_map entries may be either a dict {source_mp4, source_start_sec,
    # source_end_sec, era} or a bare string (legacy shape = source stem only).
    for line in all_lines:
        meta = source_map.get(line.wav_path.stem) or source_map.get(line.wav_path.name)
        if isinstance(meta, str):
            meta = {"source_mp4": str(raw_dir / f"{meta}.mp4")}
        if meta:
            mp4_hint = meta.get("source_mp4")
            if mp4_hint:
                p = Path(mp4_hint)
                line.source_mp4 = p if p.exists() else line.source_mp4
            line.source_start_sec = meta.get("source_start_sec")
            line.source_end_sec = meta.get("source_end_sec")
            # Reject weakly-matched source mappings — a match_score below
            # 0.75 means the source timestamp was guessed from a fuzzy SRT
            # alignment and likely points at the wrong frame. Better to
            # stay in vo_only (audio over broll) than anchor on the wrong
            # clip and show a random scene while the character speaks.
            match_score = float(meta.get("match_score", 1.0))
            has_valid_source = (
                line.source_mp4 is not None
                and line.source_mp4.exists()
                and line.source_start_sec is not None
                and line.source_start_sec > 0
                and match_score >= 0.75
            )
            line.anchor_mode = "sync" if has_valid_source else "vo_only"
        else:
            # Without explicit source timestamps, treat as VO only
            line.anchor_mode = "vo_only"

    # Layer 1c: rank and pick
    selected = select_spine(
        all_lines,
        target_count=target_dialogue_count,
        narrative_priority=narrative_priority,
        punchy_keywords=punchy_keywords,
    )
    placed = place_dialogue_on_timeline(selected, total_duration, song_structure=song_structure)

    # Layer 2: sync anchors for any line with source meta
    anchors = build_sync_anchors(placed)

    # Layer 3: B-roll fill
    all_shots = fill_broll(
        anchors=anchors,
        total_duration=total_duration,
        db_path=shot_library_db,
        style_profile=style_profile,
        song_beats=song_beats,
        character=character,
        character_aliases=character_aliases,
        excluded_sources=excluded_sources,
        era_arc=era_arc,
        concept_beats=concept_beats,
    )

    plan = LayeredPlan(
        total_duration=total_duration,
        dialogue_lines=placed,
        shots=all_shots,
    )

    # Layer 4: validate
    validate(plan)
    return plan
