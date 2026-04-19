"""Extract specific time ranges from downloaded videos using ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ExtractResult:
    success: bool
    path: Path | None
    stderr: str = ""


def _check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found. Install with: brew install ffmpeg")


def _parse_timestamp(ts: str) -> float:
    """Parse HH:MM:SS or MM:SS or SS.ss into seconds (float)."""
    ts = ts.strip()
    if ":" not in ts:
        return float(ts)
    parts = ts.split(":")
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    raise ValueError(f"Unparseable timestamp: {ts}")


def extract_range(
    source_path: str | Path,
    output_path: str | Path,
    *,
    start: str | float,
    end: str | float | None = None,
    duration: float | None = None,
    reencode: bool = True,
    crf: int = 18,
    audio_only: bool = False,
) -> ExtractResult:
    """Extract a time range from a video file.

    Args:
        source_path: Full input video file
        output_path: Where the extracted clip should land
        start: Start timestamp (HH:MM:SS or seconds as float)
        end: End timestamp (optional if duration is given)
        duration: Explicit duration in seconds (used if end is not given)
        reencode: If True, re-encode for frame-accurate cuts. Slower but precise.
                  If False, stream-copy (fast, but cuts snap to keyframes).
        crf: Constant Rate Factor for H.264 re-encode (18 = visually lossless)
    """
    _check_ffmpeg()

    src = Path(source_path)
    if not src.exists():
        return ExtractResult(success=False, path=None, stderr=f"Source not found: {src}")

    start_sec = _parse_timestamp(start) if isinstance(start, str) else float(start)

    if end is not None:
        end_sec = _parse_timestamp(end) if isinstance(end, str) else float(end)
        dur = end_sec - start_sec
    elif duration is not None:
        dur = duration
    else:
        return ExtractResult(
            success=False,
            path=None,
            stderr="Must supply either `end` or `duration`.",
        )

    if dur <= 0:
        return ExtractResult(success=False, path=None, stderr="Duration must be > 0")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if audio_only:
        # Extract just audio. Force a .wav extension if output doesn't have an audio one.
        if out.suffix.lower() not in {".wav", ".mp3", ".m4a", ".aac", ".flac"}:
            out = out.with_suffix(".wav")
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(src),
            "-ss", f"{start_sec:.3f}",
            "-t", f"{dur:.3f}",
            "-vn",
            "-acodec", "pcm_s16le" if out.suffix.lower() == ".wav" else "aac",
            "-ar", "48000",
            "-ac", "2",
            str(out),
        ]
    elif reencode:
        # Frame-accurate: seek after -i, re-encode
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(src),
            "-ss", f"{start_sec:.3f}",
            "-t", f"{dur:.3f}",
            "-c:v", "libx264",
            "-crf", str(crf),
            "-preset", "medium",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(out),
        ]
    else:
        # Stream copy: fast but keyframe-aligned
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", f"{start_sec:.3f}",
            "-i", str(src),
            "-t", f"{dur:.3f}",
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            str(out),
        ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        return ExtractResult(success=False, path=None, stderr=exc.stderr or str(exc))

    return ExtractResult(success=True, path=out)
