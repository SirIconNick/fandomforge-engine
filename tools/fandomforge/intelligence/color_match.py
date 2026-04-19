"""Auto color-matching — match every shot's color to a reference shot.

Uses OpenCV histogram matching (LAB color space) for professional-grade
color unification across shots from different sources.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def extract_reference_frame(
    source_video: str | Path,
    time_sec: float,
    output_jpg: str | Path,
) -> bool:
    """Extract a single frame to use as the color reference."""
    if not _have("ffmpeg"):
        return False

    out = Path(output_jpg)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostats",
        "-ss", f"{time_sec:.3f}",
        "-i", str(source_video),
        "-frames:v", "1",
        "-q:v", "2",
        str(out),
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
        return out.exists() and out.stat().st_size > 0
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def histogram_match_video(
    input_video: str | Path,
    reference_jpg: str | Path,
    output_video: str | Path,
    *,
    intensity: float = 0.6,
) -> bool:
    """Apply histogram matching to the entire input video, targeting reference colors.

    Uses OpenCV for frame-by-frame LAB-space histogram matching, then re-encodes.
    Intensity blends with original (0-1).
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return False

    src = Path(input_video)
    ref = Path(reference_jpg)
    out = Path(output_video)
    if not src.exists() or not ref.exists():
        return False
    out.parent.mkdir(parents=True, exist_ok=True)

    # Load reference and compute its LAB histograms
    ref_img = cv2.imread(str(ref))
    if ref_img is None:
        return False
    ref_lab = cv2.cvtColor(ref_img, cv2.COLOR_BGR2LAB)

    # Precompute reference channel CDFs
    ref_cdfs = []
    for c in range(3):
        ch = ref_lab[..., c]
        hist = np.bincount(ch.flatten(), minlength=256).astype(np.float64)
        cdf = hist.cumsum()
        if cdf[-1] > 0:
            cdf /= cdf[-1]
        ref_cdfs.append(cdf)

    # Open input video
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return False

    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Pipe matched frames to ffmpeg for proper H.264 encoding
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostats",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{w}x{h}",
        "-r", str(fps),
        "-i", "-",
        "-i", str(src),  # keep original audio
        "-map", "0:v",
        "-map", "1:a?",
        "-c:v", "libx264",
        "-crf", "20",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out),
    ]

    proc = subprocess.Popen(
        ffmpeg_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert proc.stdin is not None

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
            matched = lab.copy()
            for c in range(3):
                ch = lab[..., c]
                hist = np.bincount(ch.flatten(), minlength=256).astype(np.float64)
                cdf = hist.cumsum()
                if cdf[-1] > 0:
                    cdf /= cdf[-1]
                # Lookup: for each input level, find reference level with nearest CDF
                lut = np.zeros(256, dtype=np.uint8)
                for i in range(256):
                    diff = np.abs(ref_cdfs[c] - cdf[i])
                    lut[i] = int(np.argmin(diff))
                matched[..., c] = lut[ch]

            matched_bgr = cv2.cvtColor(matched, cv2.COLOR_LAB2BGR)

            # Blend with original at intensity
            if intensity < 1.0:
                blended = cv2.addWeighted(frame, 1.0 - intensity, matched_bgr, intensity, 0)
            else:
                blended = matched_bgr

            proc.stdin.write(blended.tobytes())
    finally:
        cap.release()
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.wait(timeout=600)

    return out.exists() and out.stat().st_size > 0
