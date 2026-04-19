"""Motion-cut matching detector.

For every shot boundary, samples two frames from each side of the cut, runs
optical flow to derive a dominant motion direction per side, and classifies
the cut as match_cut / impact_cut / neutral / disconnected.

Uses OpenCV's Farneback dense optical flow on a heavily downscaled pair
(96px wide). Accuracy is rough but good enough for the question we're
asking: "does the outgoing motion continue into the incoming shot, or does
it cut against it?"

Fandom-edit craft shows up in this signal: deliberate match cuts (runner
exits frame left → next shot enters from right) are a hallmark of well-edited
action sequences. Disconnected cuts feel lazy.
"""

from __future__ import annotations

import logging
import math
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


MOTION_KINDS = ("match_cut", "impact_cut", "neutral", "disconnected")


def _sample_frame_pair(
    video: Path, t1: float, t2: float, tmp_dir: Path, width: int = 96,
) -> tuple[Path, Path] | None:
    """Grab two frames at t1 and t2. Used to compute optical flow on
    pre-cut motion (last two frames of outgoing shot) and post-cut motion
    (first two frames of incoming shot)."""
    if shutil.which("ffmpeg") is None:
        return None
    a = tmp_dir / f"a_{t1:.3f}.png"
    b = tmp_dir / f"b_{t2:.3f}.png"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-ss", f"{t1:.3f}",
             "-i", str(video),
             "-frames:v", "1",
             "-vf", f"scale={width}:-1",
             str(a)],
            check=True, capture_output=True, timeout=10,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-ss", f"{t2:.3f}",
             "-i", str(video),
             "-frames:v", "1",
             "-vf", f"scale={width}:-1",
             str(b)],
            check=True, capture_output=True, timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    if not (a.exists() and b.exists()):
        return None
    return a, b


def _dominant_motion(frame_a: Path, frame_b: Path) -> tuple[float, float] | None:
    """Return (angle_deg, magnitude) of the dominant motion between two
    consecutive frames, or None if it can't be computed.

    angle is in [0, 360) with 0 = rightward, 90 = downward (image coords).
    """
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return None

    try:
        a = cv2.imread(str(frame_a), cv2.IMREAD_GRAYSCALE)
        b = cv2.imread(str(frame_b), cv2.IMREAD_GRAYSCALE)
    except Exception:  # noqa: BLE001
        return None
    if a is None or b is None:
        return None
    if a.shape != b.shape:
        # Rescale b to a's shape
        b = cv2.resize(b, (a.shape[1], a.shape[0]))

    try:
        flow = cv2.calcOpticalFlowFarneback(
            a, b, None,
            pyr_scale=0.5, levels=3, winsize=15,
            iterations=3, poly_n=5, poly_sigma=1.2, flags=0,
        )
    except Exception:  # noqa: BLE001
        return None

    fx = flow[..., 0]
    fy = flow[..., 1]
    # Weight the mean by magnitude so dominant regions drive the result
    mag = np.sqrt(fx * fx + fy * fy)
    if mag.sum() < 1e-3:
        return 0.0, 0.0  # effectively still frame
    wx = (fx * mag).sum() / mag.sum()
    wy = (fy * mag).sum() / mag.sum()
    angle = math.degrees(math.atan2(wy, wx)) % 360
    magnitude = math.sqrt(wx * wx + wy * wy)
    return float(angle), float(magnitude)


def _classify_cut(
    out_motion: tuple[float, float],
    in_motion: tuple[float, float],
    still_threshold: float = 0.4,
    match_tol_deg: float = 35.0,
) -> str:
    """Classify a single cut given outgoing + incoming motion vectors."""
    out_angle, out_mag = out_motion
    in_angle, in_mag = in_motion

    if out_mag < still_threshold and in_mag < still_threshold:
        return "neutral"
    # When one side is still and the other isn't, treat as neutral — no
    # continuity signal either way.
    if out_mag < still_threshold or in_mag < still_threshold:
        return "neutral"

    diff = abs((out_angle - in_angle + 180) % 360 - 180)  # 0..180

    if diff <= match_tol_deg:
        return "match_cut"
    if diff >= 180 - match_tol_deg:
        return "impact_cut"
    return "disconnected"


def classify_motion_cuts(
    video: Path,
    boundaries: list[tuple[float, float]],
    *,
    max_samples: int = 40,
) -> dict[str, Any]:
    """Classify motion continuity at every shot boundary (sampled)."""
    if len(boundaries) < 2 or shutil.which("ffmpeg") is None:
        return {"sample_count": 0}

    cut_indices = list(range(1, len(boundaries)))
    if len(cut_indices) > max_samples:
        step = len(cut_indices) / max_samples
        cut_indices = [cut_indices[int(i * step)] for i in range(max_samples)]

    counts: Counter[str] = Counter()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        for i in cut_indices:
            # Outgoing motion: two frames near end of outgoing shot (0.10s apart)
            out_t1 = max(0.0, boundaries[i - 1][1] - 0.10)
            out_t2 = max(0.0, boundaries[i - 1][1] - 0.02)
            # Incoming motion: two frames just after the cut
            in_t1 = boundaries[i][0] + 0.02
            in_t2 = boundaries[i][0] + 0.10

            out_frames = _sample_frame_pair(video, out_t1, out_t2, tmp_p)
            in_frames = _sample_frame_pair(video, in_t1, in_t2, tmp_p)
            if not out_frames or not in_frames:
                continue
            out_motion = _dominant_motion(*out_frames)
            in_motion = _dominant_motion(*in_frames)
            if out_motion is None or in_motion is None:
                continue
            counts[_classify_cut(out_motion, in_motion)] += 1

    total = sum(counts.values())
    if total == 0:
        return {"sample_count": 0}

    dist = {k: counts.get(k, 0) for k in MOTION_KINDS}
    dist_pct = {k: round(dist[k] / total * 100.0, 2) for k in MOTION_KINDS}

    # Continuity score: match_cut is the best, impact_cut is still deliberate,
    # neutral is fine for dialogue edits, disconnected is the bad one.
    score = (dist["match_cut"] + 0.75 * dist["impact_cut"] + 0.35 * dist["neutral"]) / total * 100.0

    return {
        "sample_count": total,
        "distribution": dist,
        "distribution_pct": dist_pct,
        "continuity_score": round(score, 2),
    }


__all__ = ["MOTION_KINDS", "classify_motion_cuts"]
