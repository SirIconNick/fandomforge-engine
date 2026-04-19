"""Per-source color grader for FandomForge multi-era edits.

Analyses the average LAB color of each raw source clip (shadows, midtones,
highlights) using ffmpeg signalstats, then generates a per-source offset LUT
that shifts the source toward a common cinematic target palette. For Leon
Kennedy edits, six source-specific recipes are hardcoded with knowledge of
each era's look.

Algorithm:
1. For each source directory, run ffmpeg signalstats on a representative
   sample of frames and record average Y, U, V in three luminance zones
   (shadows < 85, midtones 85-170, highlights > 170 on 0-255 scale).
2. Convert the YUV means to LAB for perceptual delta computation.
3. Pick a target palette: either the built-in teal-orange cinematic grade,
   or a user-supplied reference image measured the same way.
4. For each source, compute per-zone LAB offsets from source to target,
   then bake those offsets into a 3D .cube LUT via lut.py's generator.
5. Intensity is read from the style_profile (key: color_lut_intensity,
   default 0.50) and stored with the LUT path in the ColorPlan.

Leon-specific presets are applied when the source name matches a known era.
Each preset overrides the computed offsets with tuned artistic values.

Usage::

    from fandomforge.intelligence.color_grader import (
        analyze_sources, generate_matching_luts, build_color_plan
    )

    source_stats = analyze_sources(raw_dir="/path/to/raw")
    lut_map = generate_matching_luts(source_stats, target_palette=None)
    color_plan = build_color_plan(edit_plan, lut_map)
"""

from __future__ import annotations

import logging
import math
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Video extensions we recognise as source material.
# ---------------------------------------------------------------------------

_VIDEO_EXTS: set[str] = {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".webm", ".mxf"}

# ---------------------------------------------------------------------------
# LAB target for the default teal-orange cinematic grade.
# These are representative LAB means for a well-graded cinematic frame in
# each zone (approximate perceptual targets, not rigid constraints).
# ---------------------------------------------------------------------------

_TARGET_TEAL_ORANGE: dict[str, tuple[float, float, float]] = {
    "shadows":    (28.0, -6.0,  -8.0),   # L*, a*, b*
    "midtones":   (52.0,  1.0,   4.0),
    "highlights": (78.0,  4.0,  14.0),
}

# ---------------------------------------------------------------------------
# Leon-specific era recipes.
# Each entry has shadow/mid/highlight shifts expressed as LAB DELTAS to add
# on top of the source's own measured LAB means, PLUS saturation and contrast
# multipliers and an intensity override.
# ---------------------------------------------------------------------------

_LEON_ERA_RECIPES: dict[str, dict[str, Any]] = {
    # RE2 Remake (1998 setting): crushed shadows, desaturated cold look.
    # Goal: warm up, push toward teal-orange.
    "RE2R": {
        "shadow_shift":    (-0.03,  0.02,  0.09),   # RGB shifts for LUT generation
        "mid_shift":       ( 0.03,  0.01, -0.02),
        "highlight_shift": ( 0.07,  0.04, -0.05),
        "saturation":      1.12,
        "contrast":        1.05,
        "intensity":       0.55,
        "note": "Crushed shadows, desaturated. Warm up, push teal-orange.",
    },
    # RE4 Remake (2004/2023): already warm highlights. Slight cool-down and match.
    "RE4R": {
        "shadow_shift":    (-0.02, -0.01,  0.05),
        "mid_shift":       (-0.01,  0.0,  -0.01),
        "highlight_shift": ( 0.02,  0.01, -0.04),
        "saturation":      0.97,
        "contrast":        1.04,
        "intensity":       0.50,
        "note": "Warm highlights already. Slight cool-down to match master.",
    },
    # RE6 (2013): cold sterile lab lighting. Push warm.
    "RE6": {
        "shadow_shift":    (-0.02,  0.02,  0.06),
        "mid_shift":       ( 0.04,  0.02, -0.01),
        "highlight_shift": ( 0.07,  0.04, -0.03),
        "saturation":      1.08,
        "contrast":        1.06,
        "intensity":       0.55,
        "note": "Cold lab. Push warm, add contrast.",
    },
    # Damnation / Vendetta CGI films: hot saturation. Desaturate 15%.
    "Damnation": {
        "shadow_shift":    (-0.01,  0.0,   0.02),
        "mid_shift":       ( 0.01,  0.0,  -0.01),
        "highlight_shift": ( 0.03,  0.01, -0.02),
        "saturation":      0.85,
        "contrast":        1.03,
        "intensity":       0.50,
        "note": "CGI hot saturation. Desaturate 15% and mild teal-orange.",
    },
    "Vendetta": {
        "shadow_shift":    (-0.04, -0.01,  0.04),
        "mid_shift":       ( 0.02,  0.01, -0.01),
        "highlight_shift": ( 0.04,  0.02, -0.03),
        "saturation":      0.85,
        "contrast":        1.06,
        "intensity":       0.50,
        "note": "CGI hot saturation. Desaturate, teal shadow, warm highlight.",
    },
    # Infinite Darkness (2021): flat, low-contrast streaming look.
    # Add contrast, push warmth.
    "ID": {
        "shadow_shift":    (-0.03,  0.01,  0.06),
        "mid_shift":       ( 0.03,  0.02, -0.01),
        "highlight_shift": ( 0.06,  0.03, -0.04),
        "saturation":      1.05,
        "contrast":        1.15,
        "intensity":       0.55,
        "note": "Flat Netflix look. Add contrast, push warmth.",
    },
    # RE9 / Village era forward (2026): already graded cinematic. Touch lightly.
    "RE9": {
        "shadow_shift":    (-0.01,  0.0,   0.01),
        "mid_shift":       ( 0.0,   0.0,   0.0),
        "highlight_shift": ( 0.01,  0.0,  -0.01),
        "saturation":      1.00,
        "contrast":        1.01,
        "intensity":       0.20,
        "note": "Already cinematic. Barely touch at 20% intensity.",
    },
}

# Regex patterns to detect each era from a source directory or filename.
_LEON_ERA_PATTERNS: list[tuple[str, str]] = [
    (r"re2r|re2[-_]remake|resident[-_]evil[-_]2", "RE2R"),
    (r"re4r|re4[-_]remake|resident[-_]evil[-_]4", "RE4R"),
    (r"\bre6\b|resident[-_]evil[-_]6", "RE6"),
    (r"damnation", "Damnation"),
    (r"vendetta", "Vendetta"),
    (r"infinite[-_]darkness|leon[-_]id\b|leon[-_]infinite", "ID"),
    (r"\bre9\b|resident[-_]evil[-_]9|village", "RE9"),
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ZoneLAB:
    """Mean LAB values for a luminance zone.

    Attributes:
        L: Luminance component mean.
        a: Green-red component mean.
        b: Blue-yellow component mean.
        n_frames: Number of frames averaged.
    """

    L: float
    a: float
    b: float
    n_frames: int = 0


@dataclass
class ColorStats:
    """Measured LAB statistics for one source.

    Attributes:
        source_name: Identifier matching a key in the edit plan.
        shadows: Zone LAB for luminance < 85 (0-255 scale).
        midtones: Zone LAB for luminance 85-170.
        highlights: Zone LAB for luminance > 170.
        mean_saturation: Average saturation across all frames (0-255 approx).
        sample_frame_count: How many frames were sampled.
        detected_era: Leon era key if auto-detected, else None.
    """

    source_name: str
    shadows: ZoneLAB
    midtones: ZoneLAB
    highlights: ZoneLAB
    mean_saturation: float = 0.0
    sample_frame_count: int = 0
    detected_era: str | None = None


@dataclass
class SourceLUTEntry:
    """A per-source LUT assignment.

    Attributes:
        source_name: Source identifier.
        lut_path: Absolute path to the generated .cube file.
        intensity: Blend intensity (0.0-1.0) for this source.
        era: Leon era tag if applicable.
        note: Human-readable description of the grade applied.
    """

    source_name: str
    lut_path: str
    intensity: float
    era: str | None
    note: str


@dataclass
class ShotColorNote:
    """Color grading instructions for a single shot in the edit.

    Attributes:
        cut_index: ShotRecord.cut_index this note applies to.
        source: Source identifier.
        lut_path: Absolute path to the .cube LUT for this shot.
        intensity: Blend intensity to use when applying the LUT.
        era: Leon era tag if applicable.
    """

    cut_index: int
    source: str
    lut_path: str
    intensity: float
    era: str | None


@dataclass
class ColorPlan:
    """Complete color grading plan for an edit.

    Attributes:
        lut_entries: One entry per unique source.
        shot_notes: Per-shot grading instructions, ordered by cut_index.
        target_palette: Name of the target palette used (e.g. 'teal-orange').
    """

    lut_entries: list[SourceLUTEntry]
    shot_notes: list[ShotColorNote]
    target_palette: str


# ---------------------------------------------------------------------------
# LAB conversion helpers
# ---------------------------------------------------------------------------


def _yuv_to_rgb(y: float, u: float, v: float) -> tuple[float, float, float]:
    """Convert YUV (0-255 range, BT.601) to linear RGB (0-1 range).

    Args:
        y: Luma component 0-255.
        u: U component 0-255 (Cb, shifted so 128=neutral).
        v: V component 0-255 (Cr, shifted so 128=neutral).

    Returns:
        Tuple (R, G, B) in [0, 1].
    """
    y_n = y / 255.0
    u_n = (u - 128.0) / 255.0
    v_n = (v - 128.0) / 255.0

    r = y_n + 1.13983 * v_n
    g = y_n - 0.39465 * u_n - 0.58060 * v_n
    b = y_n + 2.03211 * u_n

    return (
        max(0.0, min(1.0, r)),
        max(0.0, min(1.0, g)),
        max(0.0, min(1.0, b)),
    )


def _rgb_to_lab(r: float, g: float, b: float) -> tuple[float, float, float]:
    """Convert linear sRGB (0-1) to CIELAB.

    Uses the D65 illuminant and the standard CIE conversion.

    Args:
        r: Red component 0-1.
        g: Green component 0-1.
        b: Blue component 0-1.

    Returns:
        Tuple (L*, a*, b*).
    """
    def linearise(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r_lin = linearise(r)
    g_lin = linearise(g)
    b_lin = linearise(b)

    # sRGB to XYZ (D65)
    X = r_lin * 0.4124564 + g_lin * 0.3575761 + b_lin * 0.1804375
    Y = r_lin * 0.2126729 + g_lin * 0.7151522 + b_lin * 0.0721750
    Z = r_lin * 0.0193339 + g_lin * 0.1191920 + b_lin * 0.9503041

    # Normalise by D65 white point
    X /= 0.95047
    Z /= 1.08883

    def f(t: float) -> float:
        return t ** (1.0 / 3.0) if t > 0.008856 else 7.787 * t + 16.0 / 116.0

    fx, fy, fz = f(X), f(Y), f(Z)

    L = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    b_lab = 200.0 * (fy - fz)

    return L, a, b_lab


# ---------------------------------------------------------------------------
# ffmpeg signalstats measurement
# ---------------------------------------------------------------------------


def _measure_source_lab(
    video_path: Path,
    n_frames: int = 30,
) -> tuple[ZoneLAB, ZoneLAB, ZoneLAB, float]:
    """Measure shadow/midtone/highlight LAB means for a video file.

    Samples ``n_frames`` evenly spaced frames using ffmpeg signalstats,
    reads the per-frame YAVG (luma average), then converts to LAB.

    Args:
        video_path: Path to the source video.
        n_frames: Number of frames to sample. More frames = more accurate
            but slower. 30 is a good balance for shot-level grading.

    Returns:
        Tuple of (shadows ZoneLAB, midtones ZoneLAB, highlights ZoneLAB,
        mean_saturation float).
    """
    if not video_path.exists():
        logger.warning("Source not found: %s", video_path)
        fallback = ZoneLAB(L=50.0, a=0.0, b=0.0)
        return fallback, fallback, fallback, 0.0

    # Get duration first.
    dur_result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    try:
        duration = float(dur_result.stdout.strip())
    except ValueError:
        duration = 60.0

    step = duration / max(n_frames, 1)

    shadow_L, shadow_a, shadow_b = [], [], []
    mid_L, mid_a, mid_b = [], [], []
    high_L, high_a, high_b = [], [], []
    sat_values: list[float] = []

    for i in range(n_frames):
        seek = min(i * step + step * 0.5, duration - 0.1)

        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostats",
                "-ss", f"{seek:.3f}",
                "-i", str(video_path),
                "-vf", "signalstats,scale=160:90",
                "-frames:v", "1",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=30,
        )
        combined = result.stderr

        # Parse YAVG, UAVG, VAVG from signalstats output.
        y_match = re.search(r"YAVG:(\d+(?:\.\d+)?)", combined)
        u_match = re.search(r"UAVG:(\d+(?:\.\d+)?)", combined)
        v_match = re.search(r"VAVG:(\d+(?:\.\d+)?)", combined)
        sat_match = re.search(r"SATAVG:(\d+(?:\.\d+)?)", combined)

        if not (y_match and u_match and v_match):
            continue

        y_val = float(y_match.group(1))
        u_val = float(u_match.group(1))
        v_val = float(v_match.group(1))

        if sat_match:
            sat_values.append(float(sat_match.group(1)))

        r, g, b = _yuv_to_rgb(y_val, u_val, v_val)
        L_val, a_val, b_val = _rgb_to_lab(r, g, b)

        # Zone assignment based on luma (Y).
        if y_val < 85:
            shadow_L.append(L_val)
            shadow_a.append(a_val)
            shadow_b.append(b_val)
        elif y_val <= 170:
            mid_L.append(L_val)
            mid_a.append(a_val)
            mid_b.append(b_val)
        else:
            high_L.append(L_val)
            high_a.append(a_val)
            high_b.append(b_val)

    def zone_mean(lst: list[float]) -> float:
        return sum(lst) / len(lst) if lst else 0.0

    def build_zone(L_list: list[float], a_list: list[float], b_list: list[float]) -> ZoneLAB:
        return ZoneLAB(
            L=round(zone_mean(L_list), 2),
            a=round(zone_mean(a_list), 2),
            b=round(zone_mean(b_list), 2),
            n_frames=len(L_list),
        )

    shadows = build_zone(shadow_L, shadow_a, shadow_b)
    midtones = build_zone(mid_L, mid_a, mid_b)
    highlights = build_zone(high_L, high_a, high_b)
    mean_sat = round(sum(sat_values) / len(sat_values), 2) if sat_values else 0.0

    return shadows, midtones, highlights, mean_sat


def _detect_era(source_name: str) -> str | None:
    """Detect a Leon era key from a source directory or file name.

    Args:
        source_name: Source identifier (directory stem or filename stem).

    Returns:
        Era key string (e.g. 'RE4R') or None if not matched.
    """
    name_lower = source_name.lower()
    for pattern, era_key in _LEON_ERA_PATTERNS:
        if re.search(pattern, name_lower):
            return era_key
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_sources(raw_dir: str | Path) -> dict[str, ColorStats]:
    """Measure LAB color statistics for all video sources in a directory.

    Treats each immediate subdirectory of raw_dir as a named source. If a
    subdirectory contains no video files, it is skipped. If raw_dir itself
    contains video files (no subdirectories), each video file is treated as
    its own source.

    Args:
        raw_dir: Directory containing source video folders or files.

    Returns:
        Dict mapping source_name -> ColorStats. Empty dict if no videos found.
    """
    raw_path = Path(raw_dir)
    if not raw_path.exists():
        logger.error("raw_dir does not exist: %s", raw_dir)
        return {}

    results: dict[str, ColorStats] = {}

    # Collect source groups: subdirectories first, then top-level video files.
    subdirs = [d for d in raw_path.iterdir() if d.is_dir()]

    if subdirs:
        for subdir in sorted(subdirs):
            video_files = [
                f for f in subdir.iterdir()
                if f.is_file() and f.suffix.lower() in _VIDEO_EXTS
            ]
            if not video_files:
                continue

            source_name = subdir.name
            # Use the first (or longest) video file as the representative sample.
            rep = max(video_files, key=lambda f: f.stat().st_size)

            logger.info("Analysing source '%s' via %s", source_name, rep.name)
            shadows, midtones, highlights, sat = _measure_source_lab(rep)

            era = _detect_era(source_name)
            results[source_name] = ColorStats(
                source_name=source_name,
                shadows=shadows,
                midtones=midtones,
                highlights=highlights,
                mean_saturation=sat,
                sample_frame_count=shadows.n_frames + midtones.n_frames + highlights.n_frames,
                detected_era=era,
            )
    else:
        # Top-level video files.
        video_files = [
            f for f in raw_path.iterdir()
            if f.is_file() and f.suffix.lower() in _VIDEO_EXTS
        ]
        for vf in sorted(video_files):
            source_name = vf.stem
            logger.info("Analysing source '%s'", source_name)
            shadows, midtones, highlights, sat = _measure_source_lab(vf)
            era = _detect_era(source_name)
            results[source_name] = ColorStats(
                source_name=source_name,
                shadows=shadows,
                midtones=midtones,
                highlights=highlights,
                mean_saturation=sat,
                sample_frame_count=shadows.n_frames + midtones.n_frames + highlights.n_frames,
                detected_era=era,
            )

    logger.info("Analysed %d sources.", len(results))
    return results


def _lab_to_rgb_shift(
    L_delta: float, a_delta: float, b_delta: float
) -> tuple[float, float, float]:
    """Convert a small LAB delta to an approximate RGB shift for LUT generation.

    This is a linear approximation valid for small deltas (< 20 LAB units).
    Accuracy is sufficient for LUT generation where exact color science is
    not critical at this stage.

    Args:
        L_delta: Lightness shift.
        a_delta: Green-red shift.
        b_delta: Blue-yellow shift.

    Returns:
        Approximate (R_shift, G_shift, B_shift) in [0,1] scale.
    """
    # Approximate inverse of the sRGB-to-LAB pipeline.
    # L delta maps mostly to equal RGB change. a and b map to opponent channels.
    r_shift = (L_delta / 116.0) + (a_delta / 500.0)
    g_shift = (L_delta / 116.0) - (a_delta / 500.0) - (b_delta / 200.0)
    b_shift = (L_delta / 116.0) + (b_delta / 200.0)
    return r_shift * 0.6, g_shift * 0.6, b_shift * 0.6


def generate_matching_luts(
    source_stats: dict[str, ColorStats],
    target_palette: dict[str, tuple[float, float, float]] | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, SourceLUTEntry]:
    """Generate per-source .cube LUTs that shift each source toward a target.

    For Leon-era sources, the hardcoded artistic recipes from _LEON_ERA_RECIPES
    are used directly. For other sources, LAB deltas are computed from the
    measured ColorStats to the target_palette and converted to RGB shifts.

    Args:
        source_stats: Dict from analyze_sources().
        target_palette: Target LAB means per zone. Keys must be 'shadows',
            'midtones', 'highlights'. Each value is (L*, a*, b*). Defaults
            to _TARGET_TEAL_ORANGE if None.
        output_dir: Directory to write .cube files. If None, writes to a
            'color_luts' subdirectory alongside this module.

    Returns:
        Dict mapping source_name -> SourceLUTEntry.
    """
    from .lut import _generate_synthetic_cube_lut

    if target_palette is None:
        target_palette = _TARGET_TEAL_ORANGE

    if output_dir is None:
        output_dir = Path(__file__).parent.parent / "assets" / "color_luts"
    lut_dir = Path(output_dir)
    lut_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, SourceLUTEntry] = {}

    for source_name, stats in source_stats.items():
        era = stats.detected_era
        lut_path = lut_dir / f"{source_name}.cube"

        # Leon era: use hardcoded recipe.
        if era is not None and era in _LEON_ERA_RECIPES:
            recipe = _LEON_ERA_RECIPES[era]
            intensity = float(recipe["intensity"])
            note = recipe["note"]

            _generate_synthetic_cube_lut(
                lut_path,
                name=f"{source_name}-{era}",
                shadow_shift=tuple(recipe["shadow_shift"]),  # type: ignore[arg-type]
                mid_shift=tuple(recipe["mid_shift"]),
                highlight_shift=tuple(recipe["highlight_shift"]),
                saturation=float(recipe["saturation"]),
                contrast=float(recipe["contrast"]),
                size=17,
            )
            logger.info(
                "Generated Leon era LUT for '%s' (%s) at %s",
                source_name, era, lut_path,
            )

        else:
            # Generic source: compute LAB deltas vs target.
            sh_target = target_palette.get("shadows", (28.0, -6.0, -8.0))
            mid_target = target_palette.get("midtones", (52.0, 1.0, 4.0))
            hi_target = target_palette.get("highlights", (78.0, 4.0, 14.0))

            sh_dL = sh_target[0] - stats.shadows.L
            sh_da = sh_target[1] - stats.shadows.a
            sh_db = sh_target[2] - stats.shadows.b

            mid_dL = mid_target[0] - stats.midtones.L
            mid_da = mid_target[1] - stats.midtones.a
            mid_db = mid_target[2] - stats.midtones.b

            hi_dL = hi_target[0] - stats.highlights.L
            hi_da = hi_target[1] - stats.highlights.a
            hi_db = hi_target[2] - stats.highlights.b

            shadow_rgb = _lab_to_rgb_shift(sh_dL, sh_da, sh_db)
            mid_rgb = _lab_to_rgb_shift(mid_dL, mid_da, mid_db)
            hi_rgb = _lab_to_rgb_shift(hi_dL, hi_da, hi_db)

            # Saturation: if source is oversaturated, pull back slightly.
            sat_factor = 1.0
            if stats.mean_saturation > 140:
                sat_factor = 0.88
            elif stats.mean_saturation < 60:
                sat_factor = 1.08

            intensity = 0.50
            note = f"Computed LAB-offset grade toward teal-orange for {source_name}."

            _generate_synthetic_cube_lut(
                lut_path,
                name=source_name,
                shadow_shift=shadow_rgb,
                mid_shift=mid_rgb,
                highlight_shift=hi_rgb,
                saturation=sat_factor,
                contrast=1.05,
                size=17,
            )
            logger.info("Generated computed LUT for '%s' at %s", source_name, lut_path)

        results[source_name] = SourceLUTEntry(
            source_name=source_name,
            lut_path=str(lut_path.resolve()),
            intensity=intensity,
            era=era,
            note=note,
        )

    return results


def build_color_plan(
    edit_plan: Any,
    lut_map: dict[str, SourceLUTEntry],
    style_profile: dict[str, Any] | None = None,
) -> ColorPlan:
    """Assign LUT entries to every shot in an EditPlan.

    Reads each ShotRecord.source and looks up the matching SourceLUTEntry.
    If a shot's source has no LUT entry (e.g., source added after LUT
    generation), it is assigned no LUT (empty lut_path).

    Per-shot intensity can be adjusted via the style_profile key
    'color_lut_intensity' (float 0-1, default 0.50). This is a global
    override; era-specific intensities from _LEON_ERA_RECIPES always take
    precedence if the SourceLUTEntry was generated from a recipe.

    Args:
        edit_plan: EditPlan instance from shot_optimizer.
        lut_map: Dict from generate_matching_luts().
        style_profile: Optional style profile dict. Reads 'color_lut_intensity'.

    Returns:
        ColorPlan with per-shot grading instructions.
    """
    if style_profile is None:
        style_profile = {}

    global_intensity = float(style_profile.get("color_lut_intensity", 0.50))
    shot_notes: list[ShotColorNote] = []

    for shot in edit_plan.shots:
        source = shot.source
        entry = lut_map.get(source)

        if entry is not None:
            # Era-specific intensity takes precedence.
            intensity = entry.intensity
            lut_path = entry.lut_path
            era = entry.era
        else:
            intensity = global_intensity
            lut_path = ""
            era = None

        shot_notes.append(ShotColorNote(
            cut_index=shot.cut_index,
            source=source,
            lut_path=lut_path,
            intensity=intensity,
            era=era,
        ))

    lut_entries = list(lut_map.values())
    return ColorPlan(
        lut_entries=lut_entries,
        shot_notes=shot_notes,
        target_palette="teal-orange",
    )


# ---------------------------------------------------------------------------
# Human-readable summary
# ---------------------------------------------------------------------------


def print_color_plan(plan: ColorPlan) -> None:
    """Print a readable color plan summary to stdout.

    Args:
        plan: ColorPlan from build_color_plan().
    """
    bar = "=" * 72
    thin = "-" * 72

    print(bar)
    print(f"  COLOR PLAN  (target palette: {plan.target_palette})")
    print(bar)
    print(f"  {'SOURCE':<30} {'ERA':<10} {'INTENSITY':>9}  LUT")
    print(thin)
    for entry in sorted(plan.lut_entries, key=lambda e: e.source_name):
        era_str = entry.era or "-"
        lut_name = Path(entry.lut_path).name if entry.lut_path else "(none)"
        print(
            f"  {entry.source_name:<30} {era_str:<10} "
            f"{entry.intensity:>8.0%}  {lut_name}"
        )
        if entry.note:
            print(f"     {entry.note}")
    print()
    print(f"  Shot notes: {len(plan.shot_notes)} shots covered.")
    print(bar)
