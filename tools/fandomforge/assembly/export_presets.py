"""Multi-platform export preset system for FandomForge.

Presets
-------
youtube          1920x1080  h264 crf 18  48kHz 192 kbps AAC  yuv420p  +faststart
youtube_shorts   1080x1920  vertical, center-crop from 1920x1080
tiktok           1080x1920  vertical, optimised for mobile
instagram_reels  1080x1920  vertical, under 90 s
twitter_x        1280x720   under 140 s
master           1920x1080  h264 crf 14  (archival quality)

Vertical re-framing
-------------------
When converting landscape source video to a vertical preset, a centre-crop
filter is applied first.  If face_recognition is available a face-aware crop
offset is computed so the character's face stays in frame rather than being
cut off by the crop.

Public API
----------
    from fandomforge.assembly.export_presets import export_preset, export_all_presets

    result = export_preset(
        input_video="exports/my_edit.mp4",
        preset_name="youtube",
        output_path="exports/my_edit_yt.mp4",
    )

    all_results = export_all_presets(
        input_video="exports/my_edit.mp4",
        output_dir="exports/platforms/",
    )
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Preset definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Preset:
    """Internal preset definition.

    Attributes:
        name: Machine-readable preset identifier.
        width: Output frame width in pixels.
        height: Output frame height in pixels.
        crf: H.264 constant rate factor (lower = higher quality / larger file).
        audio_bitrate: AAC bitrate string, e.g. "192k".
        audio_sample_rate: PCM sample rate in Hz.
        pix_fmt: Pixel format.  Always yuv420p for broad compatibility.
        faststart: Add +faststart movflag for web streaming.
        max_duration_sec: Clip to this length if > 0.  0 means no limit.
        vertical: True when the output is 9:16 (portrait).
        description: Human-readable preset label.
        extension: File extension without dot.
    """

    name: str
    width: int
    height: int
    crf: int
    audio_bitrate: str
    audio_sample_rate: int
    pix_fmt: str
    faststart: bool
    max_duration_sec: float
    vertical: bool
    description: str
    extension: str = "mp4"


_PRESETS: dict[str, _Preset] = {
    "youtube": _Preset(
        name="youtube",
        width=1920, height=1080,
        crf=18,
        audio_bitrate="192k",
        audio_sample_rate=48000,
        pix_fmt="yuv420p",
        faststart=True,
        max_duration_sec=0.0,
        vertical=False,
        description="YouTube 1080p (recommended upload quality)",
    ),
    "youtube_shorts": _Preset(
        name="youtube_shorts",
        width=1080, height=1920,
        crf=20,
        audio_bitrate="192k",
        audio_sample_rate=48000,
        pix_fmt="yuv420p",
        faststart=True,
        max_duration_sec=60.0,
        vertical=True,
        description="YouTube Shorts 9:16 vertical, max 60 s",
    ),
    "tiktok": _Preset(
        name="tiktok",
        width=1080, height=1920,
        crf=22,
        audio_bitrate="128k",
        audio_sample_rate=44100,
        pix_fmt="yuv420p",
        faststart=True,
        max_duration_sec=180.0,
        vertical=True,
        description="TikTok 9:16 vertical, mobile-optimised",
    ),
    "instagram_reels": _Preset(
        name="instagram_reels",
        width=1080, height=1920,
        crf=22,
        audio_bitrate="128k",
        audio_sample_rate=44100,
        pix_fmt="yuv420p",
        faststart=True,
        max_duration_sec=90.0,
        vertical=True,
        description="Instagram Reels 9:16 vertical, max 90 s",
    ),
    "twitter_x": _Preset(
        name="twitter_x",
        width=1280, height=720,
        crf=22,
        audio_bitrate="128k",
        audio_sample_rate=44100,
        pix_fmt="yuv420p",
        faststart=True,
        max_duration_sec=140.0,
        vertical=False,
        description="Twitter/X 720p, max 140 s",
    ),
    "master": _Preset(
        name="master",
        width=1920, height=1080,
        crf=14,
        audio_bitrate="320k",
        audio_sample_rate=48000,
        pix_fmt="yuv420p",
        faststart=False,
        max_duration_sec=0.0,
        vertical=False,
        description="Master archive 1080p high-quality (large file)",
    ),
}


def list_presets() -> list[str]:
    """Return the names of all available export presets."""
    return list(_PRESETS.keys())


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ExportResult:
    """Result from a single preset export run.

    Attributes:
        success: True when ffmpeg completed successfully.
        preset_name: Name of the preset used.
        output_path: Path of the exported file.
        file_size_bytes: Size of the output file.  0 on failure.
        duration_sec: Duration of the exported video.  0 when unknown.
        crop_offset_x: Horizontal crop offset applied (0 for centred crop).
        warnings: Non-fatal issues encountered during export.
        error: Error message when success is False.
    """

    success: bool
    preset_name: str
    output_path: Path | None
    file_size_bytes: int = 0
    duration_sec: float = 0.0
    crop_offset_x: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str = ""


# ---------------------------------------------------------------------------
# Video probing helpers
# ---------------------------------------------------------------------------


def _probe_video(video_path: Path) -> dict[str, Any]:
    """Return a dict with width, height, duration, and fps from ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries",
                "stream=width,height,r_frame_rate:format=duration",
                "-of", "json",
                str(video_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=True,
        )
        import json
        data = json.loads(result.stdout.decode())
        streams = data.get("streams", [{}])
        s = streams[0] if streams else {}
        fmt = data.get("format", {})

        fps_raw = s.get("r_frame_rate", "24/1")
        try:
            num, den = fps_raw.split("/")
            fps = float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            fps = 24.0

        return {
            "width": int(s.get("width", 1920)),
            "height": int(s.get("height", 1080)),
            "duration": float(fmt.get("duration", 0.0)),
            "fps": fps,
        }
    except Exception as exc:
        logger.debug("ffprobe failed on %s: %s", video_path, exc)
        return {"width": 1920, "height": 1080, "duration": 0.0, "fps": 24.0}


def _probe_face_crop_offset(
    video_path: Path,
    src_width: int,
    src_height: int,
    crop_width: int,
) -> int:
    """Sample a frame from the middle of the video and detect face positions.

    Returns the horizontal crop x-offset that best centres detected faces.
    Falls back to centred crop (offset = (src_width - crop_width) // 2) when
    face detection is unavailable or no faces are found.
    """
    default_offset = (src_width - crop_width) // 2

    # Extract a frame at the 30% mark for face detection
    sample_sec = max(1.0, _probe_video(video_path).get("duration", 10.0) * 0.30)

    try:
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{sample_sec:.2f}",
            "-i", str(video_path),
            "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "pipe:1",
        ]
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=False,
        )
        if not result.stdout or result.returncode != 0:
            return default_offset

        import numpy as np

        expected = src_width * src_height * 3
        if len(result.stdout) != expected:
            return default_offset
        frame = np.frombuffer(result.stdout, dtype=np.uint8).reshape((src_height, src_width, 3))
    except Exception:
        return default_offset

    # Try face_recognition first
    face_cx: list[int] = []
    try:
        import face_recognition  # type: ignore[import-untyped]

        rgb = frame[:, :, ::-1]
        small = rgb[::2, ::2, :]
        locs = face_recognition.face_locations(small, model="hog")
        for top, right, bottom, left in locs:
            cx = (left + right) // 2 * 2  # scale back up from 2x downsample
            face_cx.append(cx)
    except ImportError:
        pass

    # Fallback to OpenCV Haar cascade
    if not face_cx:
        try:
            import cv2  # type: ignore[import-untyped]

            grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            cc_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            detector = cv2.CascadeClassifier(cc_path)
            faces = detector.detectMultiScale(grey, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
            for (x, y, w, h) in faces:
                face_cx.append(x + w // 2)
        except Exception:
            pass

    if not face_cx:
        return default_offset

    # Target: centre of the leftmost + rightmost face
    mean_cx = sum(face_cx) // len(face_cx)
    offset = mean_cx - crop_width // 2
    # Clamp to valid range
    offset = max(0, min(src_width - crop_width, offset))
    return offset


# ---------------------------------------------------------------------------
# FFmpeg filter builders
# ---------------------------------------------------------------------------


def _build_video_filter(
    preset: _Preset,
    src_width: int,
    src_height: int,
    crop_x_offset: int = 0,
) -> str:
    """Build the ffmpeg -vf filter string for the given preset.

    For vertical presets: crop the source to the correct 9:16 ratio first,
    then scale to the target resolution.  For landscape presets: scale with
    padding to avoid distortion.
    """
    target_w = preset.width
    target_h = preset.height

    if preset.vertical:
        # Crop source to 9:16 before scaling
        crop_h = src_height
        crop_w = int(src_height * (9 / 16))
        if crop_w > src_width:
            crop_w = src_width
            crop_h = int(src_width * (16 / 9))
        crop_x = max(0, min(src_width - crop_w, crop_x_offset))
        crop_y = (src_height - crop_h) // 2
        crop_filter = f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}"
        scale_filter = f"scale={target_w}:{target_h}"
        return f"{crop_filter},{scale_filter}"
    else:
        # Scale with letterbox/pillarbox padding for landscape targets
        scale_filter = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
        return scale_filter


# ---------------------------------------------------------------------------
# Duration clamping helper
# ---------------------------------------------------------------------------


def _trim_flag(max_duration_sec: float, src_duration_sec: float) -> list[str]:
    """Return ffmpeg -t argument list if trimming is needed, else empty list."""
    if max_duration_sec > 0 and src_duration_sec > max_duration_sec:
        return ["-t", f"{max_duration_sec:.3f}"]
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def export_preset(
    input_video: str | Path,
    preset_name: str,
    output_path: str | Path,
    options: dict[str, Any] | None = None,
) -> ExportResult:
    """Export a video using the named preset.

    Args:
        input_video: Source video file (MP4, MKV, MOV, etc.).
        preset_name: One of the keys returned by list_presets().
        output_path: Destination file path.  Parent directory is created
            automatically.
        options: Optional overrides.  Supported keys:
            - face_aware_crop (bool, default True): detect face position for
              vertical crop offset (requires opencv or face_recognition).
            - crf (int): override the CRF for this export only.
            - ffmpeg_extra (list[str]): additional ffmpeg args appended before
              the output path.

    Returns:
        ExportResult with success status, path, and file size.
    """
    if options is None:
        options = {}

    input_video = Path(input_video)
    output_path = Path(output_path)

    if not input_video.exists():
        return ExportResult(
            success=False,
            preset_name=preset_name,
            output_path=None,
            error=f"Input video not found: {input_video}",
        )

    if preset_name not in _PRESETS:
        available = ", ".join(_PRESETS.keys())
        return ExportResult(
            success=False,
            preset_name=preset_name,
            output_path=None,
            error=f"Unknown preset '{preset_name}'. Available: {available}",
        )

    preset = _PRESETS[preset_name]

    try:
        import shutil
        if shutil.which("ffmpeg") is None:
            return ExportResult(
                success=False,
                preset_name=preset_name,
                output_path=None,
                error="ffmpeg not found. Install with: brew install ffmpeg",
            )
    except Exception:
        pass

    warnings: list[str] = []
    probe = _probe_video(input_video)
    src_w = probe["width"]
    src_h = probe["height"]
    src_dur = probe["duration"]

    # Warn if source is shorter than the maximum allowed duration (it's fine,
    # just informational)
    if preset.max_duration_sec > 0 and src_dur < preset.max_duration_sec:
        pass  # Source is within limits, no warning needed
    elif preset.max_duration_sec > 0 and src_dur > preset.max_duration_sec:
        warnings.append(
            f"Source is {src_dur:.1f} s; preset '{preset_name}' clips to "
            f"{preset.max_duration_sec:.0f} s. Consider trimming before exporting."
        )

    # Face-aware crop offset for vertical presets
    crop_x_offset = 0
    if preset.vertical and options.get("face_aware_crop", True):
        crop_h = src_h
        crop_w = int(src_h * (9 / 16))
        if crop_w > src_w:
            crop_w = src_w
        crop_x_offset = _probe_face_crop_offset(input_video, src_w, src_h, crop_w)

    vf = _build_video_filter(preset, src_w, src_h, crop_x_offset)

    crf = int(options.get("crf", preset.crf))
    trim_args = _trim_flag(preset.max_duration_sec, src_dur)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    movflags = "+faststart" if preset.faststart else "+write_colr"

    cmd: list[str] = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostats",
        "-i", str(input_video),
        *trim_args,
        "-vf", vf,
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", "slow",
        "-pix_fmt", preset.pix_fmt,
        "-c:a", "aac",
        "-b:a", preset.audio_bitrate,
        "-ar", str(preset.audio_sample_rate),
        "-movflags", movflags,
        *list(options.get("ffmpeg_extra", [])),
        str(output_path),
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=3600,  # 1 hour max for long master exports
        )
    except subprocess.TimeoutExpired:
        return ExportResult(
            success=False,
            preset_name=preset_name,
            output_path=None,
            warnings=warnings,
            error="Export timed out after 1 hour.",
        )
    except FileNotFoundError:
        return ExportResult(
            success=False,
            preset_name=preset_name,
            output_path=None,
            error="ffmpeg not found. Install with: brew install ffmpeg",
        )

    stderr_text = proc.stderr.decode(errors="replace").strip() if proc.stderr else ""

    if proc.returncode != 0 or not output_path.exists():
        return ExportResult(
            success=False,
            preset_name=preset_name,
            output_path=None,
            warnings=warnings,
            error=f"ffmpeg exited {proc.returncode}: {stderr_text[:500]}",
        )

    file_size = output_path.stat().st_size
    out_dur = _probe_video(output_path).get("duration", 0.0)

    return ExportResult(
        success=True,
        preset_name=preset_name,
        output_path=output_path,
        file_size_bytes=file_size,
        duration_sec=out_dur,
        crop_offset_x=crop_x_offset,
        warnings=warnings,
    )


def export_all_presets(
    input_video: str | Path,
    output_dir: str | Path,
    *,
    preset_names: list[str] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, ExportResult]:
    """Export the input video using all (or a subset of) presets.

    Outputs are written to ``<output_dir>/<stem>_<preset_name>.mp4``.

    Args:
        input_video: Source video file.
        output_dir: Directory to write all exported files.
        preset_names: Subset of preset names to run.  Defaults to all presets.
        options: Forwarded to export_preset() for each run.

    Returns:
        Dict mapping preset name to ExportResult.
    """
    input_video = Path(input_video)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    names = preset_names if preset_names is not None else list(_PRESETS.keys())
    results: dict[str, ExportResult] = {}

    for preset_name in names:
        stem = input_video.stem
        out_path = output_dir / f"{stem}_{preset_name}.mp4"
        logger.info("Exporting preset '%s' -> %s", preset_name, out_path)
        results[preset_name] = export_preset(
            input_video, preset_name, out_path, options=options
        )
        if results[preset_name].success:
            size_mb = results[preset_name].file_size_bytes / (1024 * 1024)
            logger.info(
                "  OK  %.1f MB  %.1f s",
                size_mb,
                results[preset_name].duration_sec,
            )
        else:
            logger.warning("  FAIL  %s", results[preset_name].error)

    return results
