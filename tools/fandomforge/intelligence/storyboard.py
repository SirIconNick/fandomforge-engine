"""Storyboard grid generator for FandomForge edit plans.

Produces a PNG contact sheet that the editor can review before rendering.
Each cell shows one representative frame per shot with metadata overlays:

  - Shot number and timeline position
  - Source clip name
  - Intended mood / slot
  - VO dialogue cue marker when present
  - Beat alignment indicator (DB / B)
  - Motion and gaze tags (when available from intelligence analysis)

Grid layout: 4 columns x N rows. Each cell is thumbnail + label strip.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

_COLS = 4
_THUMB_W = 360
_THUMB_H = 202  # 16:9
_LABEL_H = 72   # height of the metadata strip below each thumb
_GAP = 6
_HEADER_H = 56
_BG_COLOR = (14, 18, 28)
_CELL_BG = (22, 28, 44)
_LABEL_BG = (28, 34, 52)
_TEXT_MAIN = (240, 236, 220)
_TEXT_DIM = (140, 148, 172)
_TEXT_ACCENT = (255, 165, 60)       # orange highlight (beat / mood)
_TEXT_VO = (100, 220, 140)          # green for VO markers
_TEXT_WARN = (255, 90, 80)          # red for low-quality transitions
_TEXT_PEAK = (255, 210, 30)         # gold for peak hit marker

# Font candidates (macOS + Linux fallbacks)
_FONT_CANDIDATES = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

_MONO_FONT_CANDIDATES = [
    "/System/Library/Fonts/Menlo.ttc",
    "/Library/Fonts/Courier New.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]


# ---------------------------------------------------------------------------
# Internal frame extraction
# ---------------------------------------------------------------------------


def _extract_frame(video_path: Path, time_sec: float, out_path: Path, width: int = _THUMB_W) -> bool:
    """Extract one frame from a video at the given time offset.

    Args:
        video_path: Source video file.
        time_sec: Seek offset in seconds.
        out_path: Destination JPEG path.
        width: Output width in pixels (height auto-scaled to 16:9).

    Returns:
        True if the frame was successfully extracted.
    """
    if shutil.which("ffmpeg") is None:
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostats",
        "-ss", f"{time_sec:.4f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-vf", f"scale={width}:{int(width * 9 / 16)}",
        "-q:v", "3",
        str(out_path),
    ]
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            check=True,
            timeout=30,
        )
        return out_path.exists() and out_path.stat().st_size > 0
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _find_video(raw_dir: Path, source: str) -> Path | None:
    """Find a video file by source stem in raw_dir.

    Args:
        raw_dir: Directory containing raw video files.
        source: Source identifier string.

    Returns:
        First matching path, or None.
    """
    _video_exts = {".mp4", ".mkv", ".mov", ".webm", ".avi"}
    stem = Path(source).stem
    for ext in (".mp4", ".mkv", ".mov", ".webm", ".avi"):
        c1 = raw_dir / f"{stem}{ext}"
        if c1.exists():
            return c1
        c2 = raw_dir / f"{source}{ext}"
        if c2.exists():
            return c2
    matches = [m for m in raw_dir.glob(f"{stem}.*") if m.suffix.lower() in _video_exts]
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# Pillow helpers
# ---------------------------------------------------------------------------


def _load_fonts() -> tuple[Any, Any, Any]:
    """Load PIL fonts for different sizes.

    Returns:
        (label_font, small_font, title_font)
    """
    from PIL import ImageFont

    def _try_font(size: int, candidates: list[str]) -> Any:
        for path in candidates:
            if Path(path).exists():
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
        return ImageFont.load_default()

    label_font = _try_font(13, _FONT_CANDIDATES)
    small_font = _try_font(11, _FONT_CANDIDATES)
    title_font = _try_font(20, _FONT_CANDIDATES)
    return label_font, small_font, title_font


def _fmt_time(t: float) -> str:
    """Format a time in seconds as M:SS.ff.

    Args:
        t: Time in seconds.

    Returns:
        Formatted string.
    """
    m = int(t) // 60
    s = t - m * 60
    return f"{m}:{s:05.2f}"


def _truncate(text: str | None, max_len: int) -> str:
    """Truncate and sanitise a string for display.

    Args:
        text: Input string or None.
        max_len: Maximum character count.

    Returns:
        Truncated string.
    """
    if not text:
        return ""
    text = str(text)
    return text[:max_len] if len(text) <= max_len else text[: max_len - 1] + "…"


# ---------------------------------------------------------------------------
# Cell rendering
# ---------------------------------------------------------------------------


@dataclass
class _CellData:
    """All data needed to render one storyboard cell.

    Attributes:
        index: Shot number (zero-based cut_index).
        start_time: Timeline position in seconds.
        duration: On-screen duration in seconds.
        source: Source clip identifier.
        slot_name: Narrative slot name.
        mood_profile: Mood profile tag.
        emotion: Emotion attribute.
        action: Action attribute.
        beat_aligned: True if shot starts on a beat.
        is_downbeat: True if starts on a downbeat.
        has_vo: True if a VO dialogue cue is placed over this shot.
        vo_line: VO transcript snippet (may be empty).
        is_peak: True if this is the 80% peak hit shot.
        motion_dir: From motion_flow analysis (may be None).
        gaze_dir: From gaze_detector analysis (may be None).
        transition_quality: Score from transition_scorer (may be None).
        thumb_path: Path to the extracted thumbnail JPEG (may be None).
    """

    index: int
    start_time: float
    duration: float
    source: str
    slot_name: str
    mood_profile: str
    emotion: str | None
    action: str | None
    beat_aligned: bool
    is_downbeat: bool
    has_vo: bool
    vo_line: str
    is_peak: bool
    motion_dir: str | None
    gaze_dir: str | None
    transition_quality: float | None
    thumb_path: Path | None


def _render_cell(
    canvas: Any,
    draw: Any,
    cell: _CellData,
    col: int,
    row: int,
    fonts: tuple[Any, Any, Any],
) -> None:
    """Render one storyboard cell onto the canvas.

    Args:
        canvas: PIL Image canvas.
        draw: PIL ImageDraw instance.
        cell: Cell data.
        col: Column index (0 to _COLS-1).
        row: Row index.
        fonts: Tuple of (label_font, small_font, title_font).
    """
    from PIL import Image

    label_font, small_font, _ = fonts

    cell_w = _THUMB_W
    cell_h = _THUMB_H + _LABEL_H
    x0 = _GAP + col * (cell_w + _GAP)
    y0 = _HEADER_H + _GAP + row * (cell_h + _GAP)

    # Cell background
    draw.rectangle([x0, y0, x0 + cell_w, y0 + cell_h], fill=_CELL_BG)

    # Thumbnail
    if cell.thumb_path and cell.thumb_path.exists():
        try:
            thumb = Image.open(cell.thumb_path).convert("RGB")
            thumb = thumb.resize((_THUMB_W, _THUMB_H))
            canvas.paste(thumb, (x0, y0))
        except Exception:
            draw.rectangle([x0, y0, x0 + _THUMB_W, y0 + _THUMB_H], fill=(40, 40, 55))
    else:
        draw.rectangle([x0, y0, x0 + _THUMB_W, y0 + _THUMB_H], fill=(40, 40, 55))
        try:
            draw.text(
                (x0 + _THUMB_W // 2 - 20, y0 + _THUMB_H // 2 - 8),
                "no frame",
                fill=_TEXT_DIM,
                font=small_font,
            )
        except Exception:
            pass

    # Shot index badge (top-left corner overlay)
    badge_x, badge_y = x0 + 4, y0 + 4
    badge_text = f"#{cell.index:03d}"
    draw.rectangle([badge_x - 2, badge_y - 2, badge_x + 38, badge_y + 18], fill=(0, 0, 0, 180))
    try:
        draw.text((badge_x, badge_y), badge_text, fill=_TEXT_MAIN, font=label_font)
    except Exception:
        pass

    # Peak hit badge (top-right corner)
    if cell.is_peak:
        px = x0 + _THUMB_W - 52
        draw.rectangle([px - 2, badge_y - 2, px + 48, badge_y + 18], fill=(120, 80, 0, 200))
        try:
            draw.text((px, badge_y), "PEAK 80%", fill=_TEXT_PEAK, font=small_font)
        except Exception:
            pass

    # Beat alignment overlay (bottom-right of thumb)
    if cell.is_downbeat:
        beat_label = "DB"
        beat_color = _TEXT_ACCENT
    elif cell.beat_aligned:
        beat_label = "B"
        beat_color = (160, 200, 255)
    else:
        beat_label = ""
        beat_color = _TEXT_DIM

    if beat_label:
        bx = x0 + _THUMB_W - 26
        by = y0 + _THUMB_H - 20
        draw.rectangle([bx - 3, by - 2, bx + 22, by + 16], fill=(0, 0, 0, 160))
        try:
            draw.text((bx, by), beat_label, fill=beat_color, font=small_font)
        except Exception:
            pass

    # Transition quality warning (bottom-left of thumb)
    if cell.transition_quality is not None and cell.transition_quality < 0.45:
        qx = x0 + 4
        qy = y0 + _THUMB_H - 20
        draw.rectangle([qx - 2, qy - 2, qx + 36, qy + 16], fill=(80, 20, 20, 180))
        try:
            draw.text((qx, qy), f"T:{cell.transition_quality:.2f}", fill=_TEXT_WARN, font=small_font)
        except Exception:
            pass

    # ---- Label strip ----
    label_y0 = y0 + _THUMB_H
    draw.rectangle([x0, label_y0, x0 + cell_w, label_y0 + _LABEL_H], fill=_LABEL_BG)

    line_y = label_y0 + 5
    line_step = 15

    # Line 1: timecode + duration + slot
    time_str = f"{_fmt_time(cell.start_time)} dur={cell.duration:.2f}s"
    slot_str = _truncate(cell.slot_name, 16)
    try:
        draw.text((x0 + 5, line_y), time_str, fill=_TEXT_MAIN, font=label_font)
        draw.text((x0 + cell_w - 5 - len(slot_str) * 7, line_y), slot_str, fill=_TEXT_ACCENT, font=small_font)
    except Exception:
        pass
    line_y += line_step

    # Line 2: source
    src_str = _truncate(cell.source, 38)
    try:
        draw.text((x0 + 5, line_y), src_str, fill=_TEXT_DIM, font=small_font)
    except Exception:
        pass
    line_y += line_step

    # Line 3: mood + emotion + motion/gaze tags
    tag_parts: list[str] = []
    if cell.mood_profile:
        tag_parts.append(cell.mood_profile[:8])
    if cell.emotion:
        tag_parts.append(cell.emotion[:8])
    if cell.motion_dir and cell.motion_dir != "static":
        tag_parts.append(f"mov:{cell.motion_dir[:5]}")
    if cell.gaze_dir and cell.gaze_dir not in ("none",):
        tag_parts.append(f"gaze:{cell.gaze_dir[:6]}")
    tag_line = "  ".join(tag_parts)
    try:
        draw.text((x0 + 5, line_y), _truncate(tag_line, 44), fill=_TEXT_DIM, font=small_font)
    except Exception:
        pass
    line_y += line_step

    # Line 4: VO cue (if present)
    if cell.has_vo and cell.vo_line:
        vo_text = f'VO: "{_truncate(cell.vo_line, 40)}"'
        try:
            draw.text((x0 + 5, line_y), vo_text, fill=_TEXT_VO, font=small_font)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_storyboard(
    edit_plan: Any,
    raw_dir: str | Path,
    output_png: str | Path,
    *,
    transition_scores: dict[int, float] | None = None,
) -> None:
    """Build a storyboard PNG grid from a completed EditPlan.

    Extracts one representative frame per shot, assembles them in a 4-column
    grid, and writes the result to output_png.

    Args:
        edit_plan: EditPlan instance from shot_optimizer.plan_edit().
        raw_dir: Directory containing the raw source video files.
        output_png: Destination PNG path.
        transition_scores: Optional mapping from cut_index to transition quality
            score (0-1) from transition_scorer.score_sequence(). When provided,
            cells with quality below 0.45 will show a warning badge.

    Raises:
        ImportError: If Pillow is not installed.
        ValueError: If edit_plan has no shots.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise ImportError("Pillow is required. Run: pip install Pillow") from exc

    raw_dir = Path(raw_dir)
    output_png = Path(output_png)

    shots = edit_plan.shots
    if not shots:
        raise ValueError("EditPlan has no shots to render")

    # Build VO lookup by cut_index
    vo_by_cut: dict[int, str] = {}
    for vo in edit_plan.dialogue_placements:
        if vo.cut_index not in vo_by_cut:
            vo_by_cut[vo.cut_index] = vo.expected_line or ""

    # Canvas dimensions
    n_shots = len(shots)
    n_cols = _COLS
    n_rows = (n_shots + n_cols - 1) // n_cols
    cell_w = _THUMB_W
    cell_h = _THUMB_H + _LABEL_H
    canvas_w = n_cols * cell_w + (n_cols + 1) * _GAP
    canvas_h = _HEADER_H + n_rows * cell_h + (n_rows + 1) * _GAP + 10

    canvas = Image.new("RGB", (canvas_w, canvas_h), _BG_COLOR)
    draw = ImageDraw.Draw(canvas)

    fonts = _load_fonts()
    label_font, small_font, title_font = fonts

    # Header
    meta = edit_plan.metadata
    title = (
        f"STORYBOARD: {meta.template_name}  |  "
        f"{n_shots} shots  |  {_fmt_time(meta.total_duration_sec)}  |  "
        f"VO: {len(edit_plan.dialogue_placements)} cues"
    )
    try:
        draw.text((_GAP * 2, 14), title, fill=_TEXT_MAIN, font=title_font)
    except Exception:
        pass

    # Extract frames + render cells
    tmp = Path(tempfile.mkdtemp(prefix="ff_storyboard_"))

    try:
        for shot_idx, shot in enumerate(shots):
            col = shot_idx % n_cols
            row = shot_idx // n_cols

            # Find source video
            video_file = _find_video(raw_dir, shot.source)
            thumb_path: Path | None = None

            if video_file is not None:
                t_sample = shot.clip_start_sec + (shot.clip_end_sec - shot.clip_start_sec) * 0.35
                t_sample = max(shot.clip_start_sec, t_sample)
                jp = tmp / f"thumb_{shot_idx:04d}.jpg"
                if _extract_frame(video_file, t_sample, jp):
                    thumb_path = jp

            # Peak hit check
            is_peak = "[PEAK HIT @80%]" in (shot.intent or "")

            # Transition quality for this shot's incoming cut
            t_quality = None
            if transition_scores is not None and shot.cut_index > 0:
                t_quality = transition_scores.get(shot.cut_index - 1)

            cell = _CellData(
                index=shot.cut_index,
                start_time=shot.start_time,
                duration=shot.duration,
                source=shot.source,
                slot_name=shot.slot_name,
                mood_profile=shot.mood_profile,
                emotion=shot.emotion,
                action=shot.action,
                beat_aligned=shot.beat_aligned,
                is_downbeat=shot.is_downbeat,
                has_vo=shot.cut_index in vo_by_cut,
                vo_line=vo_by_cut.get(shot.cut_index, ""),
                is_peak=is_peak,
                motion_dir=getattr(shot, "motion_dir", None),
                gaze_dir=getattr(shot, "gaze_dir", None),
                transition_quality=t_quality,
                thumb_path=thumb_path,
            )

            _render_cell(canvas, draw, cell, col, row, fonts)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    output_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(output_png), "PNG", optimize=True)

    logger.info(
        "Storyboard saved: %s  (%d shots, %dx%d px)",
        output_png, n_shots, canvas_w, canvas_h,
    )


def build_storyboard_from_json(
    plan_json_path: str | Path,
    raw_dir: str | Path,
    output_png: str | Path,
) -> None:
    """Load an EditPlan from a saved JSON file and build its storyboard.

    Args:
        plan_json_path: Path to the JSON file produced by EditPlan.to_json().
        raw_dir: Directory containing raw video files.
        output_png: Destination PNG path.
    """
    from .shot_optimizer import EditPlan

    plan = EditPlan.from_json(plan_json_path)
    build_storyboard(plan, raw_dir, output_png)
