"""Color grading — apply base LUT or preset look to assembled video."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ColorPreset(Enum):
    """Built-in color presets that map to ffmpeg filter chains."""

    NONE = "none"
    TEAL_ORANGE = "teal_orange"  # hype / action
    DESATURATED_WARM = "desaturated_warm"  # emotional / grief
    CRUSHED_NOIR = "crushed_noir"  # thriller / Vendetta look
    COOL_CINEMATIC = "cool_cinematic"  # Villeneuve / Nolan
    NOSTALGIC = "nostalgic"  # memory / lifted blacks
    TACTICAL = "tactical"  # modern ops / John Wick
    FILM_BLEACH = "film_bleach"  # ethereal / afterlife


_PRESET_FILTERS: dict[ColorPreset, str] = {
    ColorPreset.NONE: "null",
    ColorPreset.TEAL_ORANGE: (
        # Lift shadows toward teal, push highlights warm, boost saturation
        "curves=blue='0/0.1 0.4/0.5 1/0.95',"
        "colorbalance=rs=0.1:gs=0:bs=-0.1:rh=0.15:gh=0:bh=-0.15,"
        "eq=saturation=1.15:contrast=1.1"
    ),
    ColorPreset.DESATURATED_WARM: (
        "eq=saturation=0.7:contrast=1.05:brightness=0.02,"
        "colorbalance=rh=0.08:bh=-0.05"
    ),
    ColorPreset.CRUSHED_NOIR: (
        # Crushed blacks, high contrast, slight teal shadows
        "curves=all='0/0 0.15/0.05 0.5/0.5 0.85/0.95 1/1',"
        "colorbalance=rs=-0.1:bs=0.12,"
        "eq=saturation=0.85:contrast=1.25"
    ),
    ColorPreset.COOL_CINEMATIC: (
        # Cool shadows, slightly warm midtones
        "colorbalance=rs=-0.05:bs=0.1:rm=0.05:bm=-0.03,"
        "eq=saturation=0.8:contrast=1.08"
    ),
    ColorPreset.NOSTALGIC: (
        # Lifted blacks, reduced saturation, warm cast
        "curves=all='0/0.1 0.5/0.55 1/0.95',"
        "colorbalance=rh=0.1:bh=-0.08,"
        "eq=saturation=0.75:contrast=0.95"
    ),
    ColorPreset.TACTICAL: (
        # Similar to teal-orange but more muted
        "colorbalance=rs=0.05:bs=-0.05:rh=0.1:bh=-0.1,"
        "eq=saturation=0.9:contrast=1.15:gamma=0.95"
    ),
    ColorPreset.FILM_BLEACH: (
        "eq=saturation=0.5:contrast=1.1:brightness=0.05,"
        "curves=all='0/0 0.5/0.6 1/1'"
    ),
}


@dataclass
class ColorResult:
    success: bool
    output_path: Path | None
    stderr: str = ""


def _check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found.")


def apply_base_grade(
    input_video: Path | str,
    output_video: Path | str,
    *,
    preset: ColorPreset = ColorPreset.TACTICAL,
    lut_path: Path | str | None = None,
    lut_intensity: float = 0.75,
) -> ColorResult:
    """Apply a color grade to a video.

    Args:
        input_video: source video
        output_video: output path
        preset: built-in color preset
        lut_path: optional .cube LUT file path; overrides preset if provided
        lut_intensity: 0-1 intensity of the LUT application (0.75 = 75% LUT + 25% original)
    """
    _check_ffmpeg()

    input_video = Path(input_video)
    output_video = Path(output_video)
    output_video.parent.mkdir(parents=True, exist_ok=True)

    if not input_video.exists():
        return ColorResult(success=False, output_path=None, stderr=f"Missing: {input_video}")

    if lut_path:
        lut_path = Path(lut_path)
        if not lut_path.exists():
            return ColorResult(success=False, output_path=None, stderr=f"LUT not found: {lut_path}")
        # Split, LUT the copy, blend back
        vf = (
            f"split=2[a][b];"
            f"[b]lut3d=file='{lut_path}'[b_lut];"
            f"[a][b_lut]blend=all_mode=normal:all_opacity={lut_intensity}"
        )
    else:
        vf = _PRESET_FILTERS[preset]

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_video),
        "-vf", vf,
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "medium",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_video),
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=1800)
    except subprocess.CalledProcessError as exc:
        return ColorResult(success=False, output_path=None, stderr=(exc.stderr or str(exc))[-1000:])
    except subprocess.TimeoutExpired:
        return ColorResult(success=False, output_path=None, stderr="Color grade timed out.")

    return ColorResult(success=True, output_path=output_video)
