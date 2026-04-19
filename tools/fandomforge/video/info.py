"""Video metadata extraction via ffprobe."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class VideoInfo:
    """Metadata for a single video file."""

    path: str
    duration_sec: float
    width: int
    height: int
    fps: float
    codec: str
    bitrate: int
    has_audio: bool
    container: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_fps(fps_str: str) -> float:
    """Parse "num/den" string from ffprobe into a float."""
    if not fps_str or fps_str == "0/0":
        return 0.0
    if "/" in fps_str:
        num, den = fps_str.split("/")
        try:
            return float(num) / float(den) if float(den) != 0 else 0.0
        except ValueError:
            return 0.0
    try:
        return float(fps_str)
    except ValueError:
        return 0.0


def get_video_info(video_path: str | Path) -> VideoInfo:
    """Run ffprobe and return structured metadata.

    Args:
        video_path: path to video file

    Returns:
        VideoInfo dataclass with standardized fields.

    Raises:
        FileNotFoundError: if file doesn't exist
        RuntimeError: if ffprobe fails or returns unexpected format
    """
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"Video file not found: {path}")

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "ffprobe not found. Install ffmpeg (e.g. `brew install ffmpeg`)."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffprobe failed: {exc.stderr}") from exc

    data = json.loads(result.stdout)
    fmt = data.get("format", {})
    streams = data.get("streams", [])

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video_stream is None:
        raise RuntimeError(f"No video stream found in {path}")

    duration = float(fmt.get("duration", 0.0))
    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))
    fps = _parse_fps(video_stream.get("r_frame_rate", "0/0"))
    codec = str(video_stream.get("codec_name", "unknown"))
    bitrate = int(fmt.get("bit_rate", 0))
    container = str(fmt.get("format_name", "unknown"))

    return VideoInfo(
        path=str(path.resolve()),
        duration_sec=round(duration, 3),
        width=width,
        height=height,
        fps=round(fps, 3),
        codec=codec,
        bitrate=bitrate,
        has_audio=audio_stream is not None,
        container=container,
    )
