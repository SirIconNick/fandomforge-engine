"""Thumbnail preview grid — generate a contact sheet PNG of every shot.

Lets you QA a shot list at a glance before running the full pipeline.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PreviewResult:
    success: bool
    output_path: Path | None
    shot_count: int = 0
    stderr: str = ""


def _extract_frame_at(source: Path, time_sec: float, out_jpg: Path, width: int = 320) -> bool:
    """Extract a single frame at a given timestamp, scaled to width pixels."""
    if shutil.which("ffmpeg") is None:
        return False

    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostats",
        "-ss", f"{time_sec:.3f}",
        "-i", str(source),
        "-frames:v", "1",
        "-vf", f"scale={width}:-2",
        "-q:v", "3",
        str(out_jpg),
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
        return out_jpg.exists() and out_jpg.stat().st_size > 0
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def generate_contact_sheet(
    shots: list,  # list of ShotEntry
    raw_dir: Path | str,
    output_path: Path | str,
    *,
    thumb_width: int = 320,
    cols: int = 5,
    gap: int = 8,
    bg_color: tuple[int, int, int] = (15, 20, 32),
    label_color: tuple[int, int, int] = (255, 171, 92),
) -> PreviewResult:
    """Render a PNG contact sheet with one thumbnail per shot.

    Uses Pillow to compose the grid. Each thumb shows the shot number + source.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return PreviewResult(
            success=False,
            output_path=None,
            stderr="Pillow not installed. Run: pip install Pillow",
        )

    raw_dir = Path(raw_dir)
    out = Path(output_path)
    if not shots:
        return PreviewResult(success=False, output_path=None, stderr="No shots provided")

    tmp_dir = Path(tempfile.mkdtemp(prefix="ff_preview_"))
    thumbs: list[tuple[object, str]] = []  # (PIL Image or None, label)

    for shot in shots:
        label_num = str(shot.number)
        label_hero = shot.hero or ""
        label_src = shot.source_id or "—"
        label = f"#{label_num} · {label_hero or label_src[:12]}"

        if shot.is_placeholder() or shot.source_id in {"", "—"}:
            # Black thumbnail for placeholders
            black = Image.new("RGB", (thumb_width, int(thumb_width * 9 / 16)), (20, 20, 20))
            thumbs.append((black, label))
            continue

        # Find the source video
        source_files = list(raw_dir.glob(f"{shot.source_id}.*"))
        video_files = [p for p in source_files if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
        if not video_files:
            placeholder = Image.new("RGB", (thumb_width, int(thumb_width * 9 / 16)), (60, 30, 30))
            thumbs.append((placeholder, label + " ✗"))
            continue

        ts = shot.source_timestamp_sec if shot.source_timestamp_sec is not None else 0.0
        thumb_path = tmp_dir / f"shot_{shot.number:03d}.jpg"
        if _extract_frame_at(video_files[0], ts, thumb_path, width=thumb_width):
            try:
                img = Image.open(thumb_path).convert("RGB")
                thumbs.append((img, label))
            except Exception:
                placeholder = Image.new("RGB", (thumb_width, int(thumb_width * 9 / 16)), (80, 40, 40))
                thumbs.append((placeholder, label + " ✗"))
        else:
            placeholder = Image.new("RGB", (thumb_width, int(thumb_width * 9 / 16)), (80, 40, 40))
            thumbs.append((placeholder, label + " ✗"))

    if not thumbs:
        return PreviewResult(success=False, output_path=None, stderr="No thumbs extracted")

    # Compose grid
    first = next((t[0] for t in thumbs if t[0] is not None), None)
    if first is None:
        return PreviewResult(success=False, output_path=None, stderr="All thumb generation failed")

    t_w, t_h = first.size
    label_h = 28
    cell_h = t_h + label_h
    rows = (len(thumbs) + cols - 1) // cols
    canvas_w = cols * t_w + (cols + 1) * gap
    canvas_h = rows * cell_h + (rows + 1) * gap + 60  # +60 for header

    canvas = Image.new("RGB", (canvas_w, canvas_h), bg_color)
    draw = ImageDraw.Draw(canvas)

    # Try loading a reasonable font
    font_path = None
    for candidate in [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial.ttf",
    ]:
        if Path(candidate).exists():
            font_path = candidate
            break

    try:
        label_font = ImageFont.truetype(font_path, 13) if font_path else ImageFont.load_default()
        title_font = ImageFont.truetype(font_path, 22) if font_path else ImageFont.load_default()
    except Exception:
        label_font = ImageFont.load_default()
        title_font = ImageFont.load_default()

    # Title
    draw.text((gap * 2, 14), f"{len(shots)} shots · contact sheet", fill=label_color, font=title_font)

    # Grid
    for i, (img, label) in enumerate(thumbs):
        row = i // cols
        col = i % cols
        x = gap + col * (t_w + gap)
        y = 60 + gap + row * (cell_h + gap)

        if img is not None:
            if img.size != (t_w, t_h):
                img = img.resize((t_w, t_h))
            canvas.paste(img, (x, y))

        # Label bar below thumb
        label_y = y + t_h
        draw.rectangle(
            [x, label_y, x + t_w, label_y + label_h],
            fill=(30, 36, 48),
        )
        try:
            draw.text(
                (x + 6, label_y + 6),
                label[:40],
                fill=(246, 245, 241),
                font=label_font,
            )
        except Exception:
            pass

    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, "PNG", optimize=True)

    # Cleanup
    shutil.rmtree(tmp_dir, ignore_errors=True)

    return PreviewResult(success=True, output_path=out, shot_count=len(shots))
