"""Animated title and text overlay system for FandomForge assembly pipeline.

Renders cinematic text overlays directly into the video via ffmpeg drawtext
and Pillow-generated PNG composites. Supports:

    - Character intro cards  (e.g. "LEON KENNEDY / DSO AGENT / est. 1998")
    - Chapter/act titles     (e.g. "ACT 1: RACCOON CITY")
    - Kinetic typography     (word-by-word animated dialogue synced to VO start)
    - End credits            (song credit + source disclosure + editor name)
    - YouTube chapter markers (written to a separate text file)

Animation styles:
    fade_in         -- opacity 0->1 over animation_sec
    slide_up        -- slides from bottom edge to final position while fading in
    type_on         -- characters appear left-to-right (typewriter effect)

Font strategy:
    Bebas Neue for display/headline text (cinematic, all-caps impact).
    Helvetica Neue / Arial for subtitle and caption text.
    Falls back through system fonts gracefully if Bebas is not installed.

All drawtext calls use ffmpeg's built-in text renderer. The Pillow path is
used only for overlays that require per-pixel compositing (light leaks, PNG
cards with semi-transparent backgrounds).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Animation style enum
# ---------------------------------------------------------------------------

class AnimationStyle(str, Enum):
    """Supported text animation styles for overlay entries."""

    FADE_IN = "fade_in"
    SLIDE_UP = "slide_up"
    TYPE_ON = "type_on"
    STATIC = "static"


# ---------------------------------------------------------------------------
# Core data structures
# ---------------------------------------------------------------------------

@dataclass
class TextOverlay:
    """A single animated text element to be composited onto the video.

    Attributes:
        text: The string to display. Use '\\n' for line breaks within
            a single overlay (rendered as stacked drawtext calls).
        start_sec: Timeline time (seconds) when this overlay becomes visible.
        end_sec: Timeline time (seconds) when this overlay disappears.
        position: Anchor position string. One of 'bottom_center',
            'top_center', 'center', 'bottom_left', 'top_left',
            'bottom_right', 'top_right', or an explicit 'x:y' coordinate
            string like '960:900'.
        font: Font family name. Passed directly to drawtext; must be a
            system font name. Defaults to 'Bebas Neue' for headlines.
        font_size: Font size in pixels.
        color: Font color in ffmpeg color notation (e.g. 'white', '#FFFFFF',
            'white@0.9' for slight transparency).
        animation: Animation style that controls how the text enters.
        animation_sec: Duration in seconds over which the entry animation plays.
            For slide_up and fade_in this is the ramp-in time. For type_on
            this is the total time to reveal all characters.
        fade_out_sec: Duration in seconds for the fade-out at end_sec.
        shadow: If True, add a semi-transparent drop shadow behind the text.
        uppercase: If True, force the text to uppercase before rendering.
        line_spacing_px: Extra vertical spacing between lines (multi-line only).
        overlay_kind: Human-readable label for logging ('intro_card',
            'chapter_title', 'kinetic_word', 'end_credit').
    """

    text: str
    start_sec: float
    end_sec: float
    position: str = "bottom_center"
    font: str = "Bebas Neue"
    font_size: int = 72
    color: str = "white"
    animation: AnimationStyle = AnimationStyle.FADE_IN
    animation_sec: float = 0.3
    fade_out_sec: float = 0.2
    shadow: bool = True
    uppercase: bool = True
    line_spacing_px: int = 8
    overlay_kind: str = "generic"


@dataclass
class OverlayPlan:
    """A complete set of text overlays for one edit.

    Attributes:
        overlays: All TextOverlay entries in chronological order.
        youtube_chapters: List of (time_sec, chapter_title) pairs for
            YouTube chapter markers in the video description.
        total_duration_sec: Expected total video duration. Used to
            position end-credit overlays.
    """

    overlays: list[TextOverlay] = field(default_factory=list)
    youtube_chapters: list[tuple[float, str]] = field(default_factory=list)
    total_duration_sec: float = 60.0


# ---------------------------------------------------------------------------
# Font resolution
# ---------------------------------------------------------------------------

# Ordered preference lists. First match wins.
_HEADLINE_FONT_CANDIDATES = [
    "Bebas Neue",
    "BebasNeue-Regular",
    "Impact",
    "Arial Black",
    "Arial Bold",
    "Helvetica",
]

_BODY_FONT_CANDIDATES = [
    "Helvetica Neue",
    "Helvetica",
    "Arial",
    "DejaVu Sans",
    "Liberation Sans",
]


def _resolve_font(preferred: str) -> str:
    """Return the best available font name for the preferred choice.

    Tries to locate the preferred font via fc-list (Linux/Mac) or falls
    back through the candidate lists. Returns the first candidate that
    fc-list confirms exists, or the preferred name as-is (ffmpeg will
    substitute its own default if it cannot find the font).

    Args:
        preferred: Preferred font name or family.

    Returns:
        Font name string suitable for passing to ffmpeg drawtext.
    """
    if shutil.which("fc-list") is None:
        return preferred

    headline_set = {f.lower() for f in _HEADLINE_FONT_CANDIDATES}
    body_set = {f.lower() for f in _BODY_FONT_CANDIDATES}

    try:
        result = subprocess.run(
            ["fc-list", "--format=%{family}\n"],
            capture_output=True, text=True, timeout=5,
        )
        available = {ln.strip().lower() for ln in result.stdout.splitlines() if ln.strip()}
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return preferred

    if preferred.lower() in available:
        return preferred

    candidates = (
        _HEADLINE_FONT_CANDIDATES
        if preferred.lower() in headline_set
        else _BODY_FONT_CANDIDATES
        if preferred.lower() in body_set
        else _HEADLINE_FONT_CANDIDATES
    )

    for candidate in candidates:
        if candidate.lower() in available:
            return candidate

    return preferred


# ---------------------------------------------------------------------------
# Position helpers
# ---------------------------------------------------------------------------

def _position_to_xy(position: str, width: int, height: int) -> tuple[str, str]:
    """Convert a position name to ffmpeg drawtext x/y expressions.

    Args:
        position: Position name or 'x:y' coordinate string.
        width: Video width in pixels.
        height: Video height in pixels.

    Returns:
        Tuple of (x_expr, y_expr) strings for ffmpeg drawtext.
    """
    margin = 40

    pos_map: dict[str, tuple[str, str]] = {
        "bottom_center": ("(w-text_w)/2", f"h-th-{margin}"),
        "top_center": ("(w-text_w)/2", str(margin)),
        "center": ("(w-text_w)/2", "(h-th)/2"),
        "bottom_left": (str(margin), f"h-th-{margin}"),
        "top_left": (str(margin), str(margin)),
        "bottom_right": (f"w-tw-{margin}", f"h-th-{margin}"),
        "top_right": (f"w-tw-{margin}", str(margin)),
    }

    if position in pos_map:
        return pos_map[position]

    # Try parsing as explicit 'x:y'
    if ":" in position:
        parts = position.split(":", 1)
        try:
            return (str(int(parts[0])), str(int(parts[1])))
        except ValueError:
            pass

    # Fallback to bottom center
    return ("(w-text_w)/2", f"h-th-{margin}")


# ---------------------------------------------------------------------------
# Drawtext filter builder
# ---------------------------------------------------------------------------

def _alpha_expr(
    start_sec: float,
    end_sec: float,
    animation: AnimationStyle,
    animation_sec: float,
    fade_out_sec: float,
) -> str:
    """Build an ffmpeg alpha expression for animated opacity.

    Returns a string suitable for the alpha= parameter of drawtext.
    Uses ffmpeg's 't' variable (current time in seconds).

    Args:
        start_sec: When the overlay starts appearing.
        end_sec: When the overlay disappears.
        animation: Entry animation style.
        animation_sec: Duration of the entry animation.
        fade_out_sec: Duration of the exit fade.

    Returns:
        ffmpeg expression string for alpha.
    """
    fade_in_end = start_sec + animation_sec
    fade_out_start = end_sec - fade_out_sec

    if animation in (AnimationStyle.FADE_IN, AnimationStyle.SLIDE_UP, AnimationStyle.TYPE_ON):
        # Fade in, hold, fade out
        fade_in = f"if(lt(t,{start_sec:.4f}),0,if(lt(t,{fade_in_end:.4f}),(t-{start_sec:.4f})/{animation_sec:.4f},1))"
        fade_out = (
            f"if(lt(t,{fade_out_start:.4f}),1,"
            f"if(lt(t,{end_sec:.4f}),({end_sec:.4f}-t)/{fade_out_sec:.4f},0))"
        )
        return f"min({fade_in},{fade_out})"

    # Static: just on/off
    return f"if(between(t,{start_sec:.4f},{end_sec:.4f}),1,0)"


def _y_expr_slide(
    position: str,
    width: int,
    height: int,
    start_sec: float,
    animation_sec: float,
) -> str:
    """Build a y expression for slide_up animation.

    The text starts 40px below its final position and slides up over
    animation_sec seconds using a linear ease.

    Args:
        position: Position name passed to _position_to_xy.
        width: Video width.
        height: Video height.
        start_sec: Timeline start of the overlay.
        animation_sec: Duration of the slide.

    Returns:
        ffmpeg expression string for y coordinate.
    """
    _, base_y = _position_to_xy(position, width, height)
    slide_offset = 40  # pixels
    # Clamp at start_sec; after animation_sec, y settles to base_y
    return (
        f"if(lt(t,{start_sec:.4f}),"
        f"{base_y}+{slide_offset},"
        f"if(lt(t,{start_sec + animation_sec:.4f}),"
        f"{base_y}+{slide_offset}*(1-(t-{start_sec:.4f})/{animation_sec:.4f}),"
        f"{base_y}))"
    )


def _build_drawtext_filter(
    overlay: TextOverlay,
    width: int,
    height: int,
    fps: int,
    font_override: str | None = None,
) -> list[str]:
    """Build one or more drawtext filter strings for an overlay.

    Multi-line text (lines separated by '\\n') generates one drawtext
    call per line, stacked vertically.

    Args:
        overlay: The TextOverlay to render.
        width: Video width in pixels.
        height: Video height in pixels.
        fps: Video frame rate.
        font_override: Resolved font name; if None, resolves from overlay.font.

    Returns:
        List of drawtext filter strings (one per line of text).
    """
    font = font_override or _resolve_font(overlay.font)
    text_raw = overlay.text.upper() if overlay.uppercase else overlay.text

    lines = text_raw.split("\\n") if "\\n" in text_raw else text_raw.split("\n")
    lines = [ln.strip() for ln in lines if ln.strip()]

    filters: list[str] = []
    x_expr, y_expr_base = _position_to_xy(overlay.position, width, height)
    alpha = _alpha_expr(
        overlay.start_sec, overlay.end_sec,
        overlay.animation, overlay.animation_sec, overlay.fade_out_sec,
    )

    for idx, line in enumerate(lines):
        # Escape special drawtext characters
        escaped = (
            line
            .replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace(":", "\\:")
        )

        line_height = overlay.font_size + overlay.line_spacing_px
        if len(lines) > 1:
            # Offset each line vertically from the anchor
            total_height = len(lines) * line_height
            if overlay.position in ("bottom_center", "bottom_left", "bottom_right"):
                y_expr = f"({y_expr_base})-{(len(lines)-1-idx) * line_height}"
            else:
                y_expr = f"({y_expr_base})+{idx * line_height}"
        else:
            y_expr = y_expr_base

        # Override y for slide_up
        if overlay.animation == AnimationStyle.SLIDE_UP and idx == 0:
            y_expr = _y_expr_slide(
                overlay.position, width, height,
                overlay.start_sec, overlay.animation_sec,
            )
        elif overlay.animation == AnimationStyle.SLIDE_UP and idx > 0:
            # Stack subsequent lines relative to the sliding anchor
            y_expr = (
                f"({_y_expr_slide(overlay.position, width, height, overlay.start_sec, overlay.animation_sec)})"
                f"+{idx * line_height}"
            )

        # For type_on, reveal characters progressively using ffmpeg's n (frame number)
        if overlay.animation == AnimationStyle.TYPE_ON:
            total_chars = max(len(line), 1)
            chars_per_sec = total_chars / max(overlay.animation_sec, 0.1)
            # Build a string using substr; ffmpeg drawtext doesn't support dynamic
            # text length, so we use text_x fade-in trick: render each character as
            # a separate drawtext with staggered start times.
            for char_idx, char in enumerate(line):
                if char == " ":
                    continue
                char_escaped = (
                    char.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
                )
                char_start = overlay.start_sec + char_idx / chars_per_sec
                char_alpha = _alpha_expr(
                    char_start, overlay.end_sec,
                    AnimationStyle.FADE_IN, 0.05, overlay.fade_out_sec,
                )
                # x position for individual character: approximate by font_size * char_idx
                # This is an approximation since exact advance widths are not available.
                char_x = f"({x_expr})+{char_idx * overlay.font_size * 0.55:.1f}"
                dt = (
                    f"drawtext=font='{font}'"
                    f":text='{char_escaped}'"
                    f":fontsize={overlay.font_size}"
                    f":fontcolor={overlay.color}"
                    f":x={char_x}"
                    f":y={y_expr}"
                    f":alpha='{char_alpha}'"
                )
                if overlay.shadow:
                    dt += ":shadowcolor=black@0.6:shadowx=2:shadowy=2"
                filters.append(dt)
            continue  # skip the normal single-line drawtext for this line

        dt = (
            f"drawtext=font='{font}'"
            f":text='{escaped}'"
            f":fontsize={overlay.font_size}"
            f":fontcolor={overlay.color}"
            f":x={x_expr}"
            f":y={y_expr}"
            f":alpha='{alpha}'"
        )
        if overlay.shadow:
            dt += ":shadowcolor=black@0.7:shadowx=3:shadowy=3"

        filters.append(dt)

    return filters


# ---------------------------------------------------------------------------
# High-level overlay factories
# ---------------------------------------------------------------------------

def make_character_intro_card(
    name: str,
    subtitle: str,
    start_sec: float,
    *,
    hold_sec: float = 1.5,
    fade_in_sec: float = 0.3,
    slide_sec: float = 0.3,
    fade_out_sec: float = 0.2,
    position: str = "bottom_center",
) -> list[TextOverlay]:
    """Build a two-element character intro card overlay sequence.

    Creates:
        1. A large Bebas Neue headline with the character name (slide_up).
        2. A smaller Helvetica subtitle line (fade_in, 0.1s delay after headline).

    The total visible duration is hold_sec. Animation starts at start_sec.

    Args:
        name: Character name to display (e.g. 'LEON KENNEDY').
        subtitle: Subtitle text (e.g. 'DSO AGENT / est. 1998').
        start_sec: Timeline position where the card first appears.
        hold_sec: How long the card remains fully visible.
        fade_in_sec: Duration of the slide-in animation.
        slide_sec: Duration of the slide-up motion.
        fade_out_sec: Duration of the fade-out.
        position: Anchor position for the card.

    Returns:
        List of two TextOverlay instances (headline + subtitle).
    """
    end_sec = start_sec + slide_sec + hold_sec + fade_out_sec

    headline = TextOverlay(
        text=name,
        start_sec=start_sec,
        end_sec=end_sec,
        position=position,
        font="Bebas Neue",
        font_size=82,
        color="white",
        animation=AnimationStyle.SLIDE_UP,
        animation_sec=slide_sec,
        fade_out_sec=fade_out_sec,
        shadow=True,
        uppercase=True,
        overlay_kind="intro_card_name",
    )

    subtitle_start = start_sec + 0.1
    subline = TextOverlay(
        text=subtitle,
        start_sec=subtitle_start,
        end_sec=end_sec,
        position=position,
        font="Helvetica Neue",
        font_size=32,
        color="white@0.85",
        animation=AnimationStyle.FADE_IN,
        animation_sec=fade_in_sec,
        fade_out_sec=fade_out_sec,
        shadow=True,
        uppercase=True,
        line_spacing_px=4,
        overlay_kind="intro_card_subtitle",
    )

    # Place subtitle below the headline
    # We adjust position slightly downward by modifying the y expression via
    # a special pseudo-position token. The drawtext builder handles stacking.
    # We use a line-break trick: pack both into one overlay with \\n separator
    # so the builder stacks them natively.
    combined = TextOverlay(
        text=f"{name}\\n{subtitle}",
        start_sec=start_sec,
        end_sec=end_sec,
        position=position,
        font="Bebas Neue",
        font_size=80,
        color="white",
        animation=AnimationStyle.SLIDE_UP,
        animation_sec=slide_sec,
        fade_out_sec=fade_out_sec,
        shadow=True,
        uppercase=True,
        line_spacing_px=6,
        overlay_kind="intro_card",
    )
    # Return the combined overlay as a single entry for simplicity
    del headline, subline
    return [combined]


def make_chapter_title(
    title: str,
    start_sec: float,
    *,
    hold_sec: float = 2.0,
    fade_in_sec: float = 0.4,
    fade_out_sec: float = 0.4,
    position: str = "center",
) -> TextOverlay:
    """Build a chapter or act title card overlay.

    Appears at the act boundary with a slow fade in/out. Cinematic style:
    large Bebas Neue text at screen center or top-center.

    Args:
        title: Chapter title (e.g. 'ACT 1: RACCOON CITY').
        start_sec: When the title appears on screen.
        hold_sec: How long the title holds before fading out.
        fade_in_sec: Duration of the fade-in.
        fade_out_sec: Duration of the fade-out.
        position: Anchor position (default 'center').

    Returns:
        A single TextOverlay instance.
    """
    end_sec = start_sec + fade_in_sec + hold_sec + fade_out_sec
    return TextOverlay(
        text=title,
        start_sec=start_sec,
        end_sec=end_sec,
        position=position,
        font="Bebas Neue",
        font_size=96,
        color="white",
        animation=AnimationStyle.FADE_IN,
        animation_sec=fade_in_sec,
        fade_out_sec=fade_out_sec,
        shadow=True,
        uppercase=True,
        overlay_kind="chapter_title",
    )


def make_kinetic_words(
    dialogue_line: str,
    vo_start_sec: float,
    *,
    hold_after_sec: float = 0.5,
    anim_per_word_sec: float = 0.15,
    fade_out_sec: float = 0.3,
    position: str = "bottom_center",
    font_size: int = 52,
) -> list[TextOverlay]:
    """Build word-by-word kinetic typography overlays synced to a VO line.

    Each word of the dialogue line gets its own TextOverlay with a staggered
    start time, creating a typewriter-style reveal that tracks the speech.

    Args:
        dialogue_line: The full dialogue text to animate.
        vo_start_sec: Timeline position where the VO audio starts.
        hold_after_sec: Seconds to hold the last word after the full line is shown.
        anim_per_word_sec: Time between each word appearing.
        fade_out_sec: Duration to fade out each word.
        position: Anchor position for the text block.
        font_size: Font size in pixels.

    Returns:
        List of TextOverlay instances, one per word.
    """
    words = dialogue_line.strip().split()
    if not words:
        return []

    total_duration = len(words) * anim_per_word_sec + hold_after_sec + fade_out_sec
    line_end_sec = vo_start_sec + total_duration
    overlays: list[TextOverlay] = []

    for idx, word in enumerate(words):
        word_start = vo_start_sec + idx * anim_per_word_sec
        overlays.append(
            TextOverlay(
                text=word,
                start_sec=word_start,
                end_sec=line_end_sec,
                position=position,
                font="Bebas Neue",
                font_size=font_size,
                color="white",
                animation=AnimationStyle.FADE_IN,
                animation_sec=0.08,
                fade_out_sec=fade_out_sec,
                shadow=True,
                uppercase=True,
                overlay_kind="kinetic_word",
            )
        )

    return overlays


def make_end_credits(
    song_title: str,
    song_artist: str,
    fandoms: list[str],
    editor_name: str,
    total_duration_sec: float,
    *,
    credits_start_offset_sec: float = 3.0,
    hold_sec: float = 5.0,
    fade_in_sec: float = 0.5,
    fade_out_sec: float = 1.0,
) -> list[TextOverlay]:
    """Build the end-credits overlay block.

    Creates a stacked credits card with:
        Line 1: "SONG: <title> BY <artist>"
        Line 2: "SOURCES: <fandoms joined>"
        Line 3: "EDITED BY: <editor_name>"

    Appears near the end of the video, fades out with the video.

    Args:
        song_title: Song name.
        song_artist: Artist name.
        fandoms: List of source fandom names (e.g. ['RE2R', 'RE4R', 'RE9']).
        editor_name: Creator/editor handle.
        total_duration_sec: Full video duration; credits start relative to this.
        credits_start_offset_sec: How many seconds from the end to start credits.
        hold_sec: How long the credits hold.
        fade_in_sec: Duration of the fade-in.
        fade_out_sec: Duration of the fade-out.

    Returns:
        A list containing a single multi-line TextOverlay.
    """
    start_sec = max(0.0, total_duration_sec - credits_start_offset_sec - hold_sec)
    end_sec = total_duration_sec

    sources_str = " / ".join(fandoms) if fandoms else "original footage"
    credit_text = (
        f"SONG: {song_title} BY {song_artist}\\n"
        f"SOURCES: {sources_str}\\n"
        f"EDITED BY: {editor_name}"
    )

    return [
        TextOverlay(
            text=credit_text,
            start_sec=start_sec,
            end_sec=end_sec,
            position="bottom_center",
            font="Helvetica Neue",
            font_size=28,
            color="white@0.8",
            animation=AnimationStyle.FADE_IN,
            animation_sec=fade_in_sec,
            fade_out_sec=fade_out_sec,
            shadow=True,
            uppercase=False,
            line_spacing_px=10,
            overlay_kind="end_credit",
        )
    ]


# ---------------------------------------------------------------------------
# YouTube chapters file
# ---------------------------------------------------------------------------

def write_youtube_chapters(
    chapters: list[tuple[float, str]],
    output_path: Path | str,
) -> Path:
    """Write a YouTube-compatible chapter list to a text file.

    Format expected by YouTube:
        00:00 Chapter Name
        00:45 Next Chapter

    The first chapter must start at 00:00 for YouTube to recognize the list.
    If no chapter is at 0.0, one is prepended automatically.

    Args:
        chapters: List of (time_sec, title) tuples. Need not be sorted.
        output_path: Path to write the chapter list text file.

    Returns:
        Path to the written file.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    sorted_chapters = sorted(chapters, key=lambda x: x[0])

    if not sorted_chapters or sorted_chapters[0][0] > 0.0:
        sorted_chapters.insert(0, (0.0, "Intro"))

    lines: list[str] = []
    for time_sec, title in sorted_chapters:
        minutes = int(time_sec // 60)
        seconds = int(time_sec % 60)
        lines.append(f"{minutes:02d}:{seconds:02d} {title}")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# Plan builder from edit plan dict
# ---------------------------------------------------------------------------

def build_overlay_plan_from_edit_plan(
    edit_plan: dict[str, Any],
    *,
    total_duration_sec: float,
    editor_name: str = "FandomForge",
) -> OverlayPlan:
    """Construct an OverlayPlan from the canonical edit plan dictionary.

    Reads the following keys from edit_plan (all optional):
        song_title: str
        song_artist: str
        fandoms: list[str]
        act_plan: list of act dicts with 'name', 'time_range', 'shot_numbers'
        character_intros: list of {'name', 'subtitle', 'song_time_sec'}
        dialogue_placements: list of {'text', 'start_sec', 'kinetic': bool}

    Args:
        edit_plan: The edit plan dictionary produced by director.propose_edit()
            or loaded from edit-plan.md.
        total_duration_sec: Full video duration in seconds.
        editor_name: Credit name for the end card.

    Returns:
        OverlayPlan ready to pass to build_overlay_layer.
    """
    plan = OverlayPlan(total_duration_sec=total_duration_sec)

    # Act titles from act_plan
    for act in edit_plan.get("act_plan", []):
        time_range = act.get("time_range", [0.0, 0.0])
        act_start = float(time_range[0]) if time_range else 0.0
        act_name = act.get("name", f"Act {act.get('act', '?')}")
        plan.overlays.append(make_chapter_title(act_name, act_start))
        plan.youtube_chapters.append((act_start, act_name))

    # Character intro cards
    for intro in edit_plan.get("character_intros", []):
        name = intro.get("name", "CHARACTER")
        subtitle = intro.get("subtitle", "")
        start = float(intro.get("song_time_sec", 0.0))
        plan.overlays.extend(
            make_character_intro_card(name, subtitle, start)
        )

    # Kinetic typography / dialogue placements
    for dialogue in edit_plan.get("dialogue_placements", []):
        text = dialogue.get("text", "")
        start = float(dialogue.get("start_sec", 0.0))
        kinetic = bool(dialogue.get("kinetic", False))
        if not text:
            continue
        if kinetic:
            plan.overlays.extend(make_kinetic_words(text, start))
        else:
            plan.overlays.append(
                TextOverlay(
                    text=text,
                    start_sec=start,
                    end_sec=start + 3.0,
                    position="bottom_center",
                    font="Helvetica Neue",
                    font_size=40,
                    color="white",
                    animation=AnimationStyle.FADE_IN,
                    animation_sec=0.15,
                    fade_out_sec=0.25,
                    shadow=True,
                    uppercase=False,
                    overlay_kind="dialogue_subtitle",
                )
            )

    # End credits
    song_title = edit_plan.get("song_title", "Unknown Song")
    song_artist = edit_plan.get("song_artist", "Unknown Artist")
    fandoms = edit_plan.get("fandoms", [])
    plan.overlays.extend(
        make_end_credits(
            song_title, song_artist, fandoms, editor_name, total_duration_sec
        )
    )

    # Sort by start time
    plan.overlays.sort(key=lambda o: o.start_sec)

    return plan


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------

def build_overlay_layer(
    input_video: Path | str,
    overlay_plan: OverlayPlan,
    output_path: Path | str,
    *,
    chapters_output: Path | str | None = None,
    width: int = 1920,
    height: int = 1080,
    fps: int = 24,
    work_dir: Path | str | None = None,
) -> Path:
    """Burn all text overlays from an OverlayPlan into a video via ffmpeg drawtext.

    Constructs a chained -vf filter string containing one drawtext call per
    overlay element. All drawtext entries are evaluated at every frame, but
    the alpha expression makes them transparent outside their time window.

    Args:
        input_video: Source video file to add overlays onto.
        overlay_plan: The OverlayPlan with all TextOverlay entries.
        output_path: Destination video path.
        chapters_output: Optional path to write YouTube chapter markers.
        width: Video width (used for position calculations).
        height: Video height.
        fps: Frame rate (used for type_on frame-count calculations).
        work_dir: Scratch directory.

    Returns:
        Path to the output video with overlays burned in.

    Raises:
        RuntimeError: If ffmpeg is not available or the render fails.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found. Install via: brew install ffmpeg")

    src = Path(input_video)
    if not src.exists():
        raise FileNotFoundError(f"Input video not found: {src}")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    work = Path(work_dir) if work_dir else out.parent / ".overlay-work"
    work.mkdir(parents=True, exist_ok=True)

    # Write YouTube chapters if requested
    if chapters_output and overlay_plan.youtube_chapters:
        write_youtube_chapters(overlay_plan.youtube_chapters, Path(chapters_output))

    if not overlay_plan.overlays:
        # Nothing to overlay; just copy the input to output
        shutil.copy2(src, out)
        return out

    # Build the drawtext filter chain
    all_filter_parts: list[str] = []

    for overlay in overlay_plan.overlays:
        font = _resolve_font(overlay.font)
        parts = _build_drawtext_filter(overlay, width, height, fps, font_override=font)
        all_filter_parts.extend(parts)

    if not all_filter_parts:
        shutil.copy2(src, out)
        return out

    # Chain all drawtext filters with comma separator
    vf_chain = ",".join(all_filter_parts)

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostats",
        "-i", str(src),
        "-vf", vf_chain,
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out),
    ]

    logger.debug("Running drawtext render: %d overlays", len(overlay_plan.overlays))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=900,
        )
        if result.returncode != 0:
            logger.error("drawtext render failed:\n%s", result.stderr[-2000:])
            raise RuntimeError(
                f"ffmpeg drawtext failed (rc={result.returncode}). "
                f"Stderr tail: {result.stderr[-500:]}"
            )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ffmpeg drawtext render timed out after 900s") from exc

    return out


# ---------------------------------------------------------------------------
# Overlay plan serialization helpers
# ---------------------------------------------------------------------------

def overlay_plan_to_json(plan: OverlayPlan, output_path: Path | str) -> Path:
    """Serialize an OverlayPlan to JSON for inspection and debugging.

    Args:
        plan: The OverlayPlan to serialize.
        output_path: Destination JSON file path.

    Returns:
        Path to the written JSON file.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {
        "total_duration_sec": plan.total_duration_sec,
        "overlay_count": len(plan.overlays),
        "youtube_chapters": [
            {"time_sec": t, "title": title}
            for t, title in plan.youtube_chapters
        ],
        "overlays": [
            {
                "kind": o.overlay_kind,
                "text": o.text,
                "start_sec": o.start_sec,
                "end_sec": o.end_sec,
                "position": o.position,
                "font": o.font,
                "font_size": o.font_size,
                "animation": o.animation.value,
                "animation_sec": o.animation_sec,
            }
            for o in plan.overlays
        ],
    }

    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return out
