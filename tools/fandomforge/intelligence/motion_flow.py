"""Optical flow analysis for every shot in the scene library.

Uses OpenCV Farneback dense optical flow (or DIS flow when available) to
determine motion direction, magnitude, and whether it is the subject or the
camera that is moving. Results are persisted in the shot_library SQLite
database.

Algorithm per shot:
1. Sample three frames spread across the shot duration.
2. Compute Farneback dense flow between frame pairs (f0->f1, f1->f2).
3. Average the dominant flow vectors across both pairs.
4. Classify direction from the mean angle.
5. Separate subject-vs-camera motion by comparing centre-crop flow to
   full-frame flow: large homogeneous full-frame flow = camera pan/tilt;
   localised central flow against a near-zero background = subject motion.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

MotionDirection = Literal["left", "right", "up", "down", "static", "toward", "away", "mixed"]
MotionKind = Literal["camera", "subject", "mixed", "none"]

# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------


@dataclass
class MotionProfile:
    """Per-shot motion characterisation from optical flow analysis.

    Attributes:
        direction: Dominant motion direction across the shot.
        magnitude: Normalised magnitude in [0, 1]. 0 = fully static.
        kind: Whether the motion is a camera move, subject move, or mixed.
        confidence: Confidence in [0, 1]. Low when flow is incoherent.
    """

    direction: MotionDirection
    magnitude: float
    kind: MotionKind
    confidence: float


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_VECTOR_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi"}


def _extract_frame(video_path: Path, time_sec: float, out_path: Path) -> bool:
    """Extract a single frame from a video at the given time offset.

    Args:
        video_path: Source video file.
        time_sec: Seek position in seconds.
        out_path: Destination JPEG path.

    Returns:
        True if the frame was successfully extracted.
    """
    import shutil

    if shutil.which("ffmpeg") is None:
        logger.error("ffmpeg not found in PATH")
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostats",
        "-ss", f"{time_sec:.4f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-vf", "scale=320:-2",
        "-q:v", "4",
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
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.debug("Frame extraction failed at %.2fs: %s", time_sec, exc)
        return False


def _flow_between(frame_a_path: Path, frame_b_path: Path) -> tuple[float, float, float, float] | None:
    """Compute Farneback dense optical flow between two grayscale frames.

    Returns (mean_vx, mean_vy, homogeneity, magnitude_norm) or None on failure.

    homogeneity: cosine alignment of individual vectors with mean vector (0-1).
    magnitude_norm: mean flow pixel displacement scaled to [0, 1] using
                    a soft cap of 15 pixels as "maximum expected".
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        logger.error("OpenCV or NumPy not available")
        return None

    img_a = cv2.imread(str(frame_a_path), cv2.IMREAD_GRAYSCALE)
    img_b = cv2.imread(str(frame_b_path), cv2.IMREAD_GRAYSCALE)

    if img_a is None or img_b is None:
        return None

    # Resize to consistent analysis size
    target_w = 320
    target_h = 180
    img_a = cv2.resize(img_a, (target_w, target_h))
    img_b = cv2.resize(img_b, (target_w, target_h))

    flow = cv2.calcOpticalFlowFarneback(
        img_a, img_b,
        None,
        pyr_scale=0.5,
        levels=3,
        winsize=13,
        iterations=3,
        poly_n=5,
        poly_sigma=1.2,
        flags=0,
    )

    # flow shape: (H, W, 2) where [..., 0] = dx, [..., 1] = dy
    vx = flow[..., 0]
    vy = flow[..., 1]

    mean_vx = float(np.mean(vx))
    mean_vy = float(np.mean(vy))

    magnitudes = np.sqrt(vx ** 2 + vy ** 2)
    mean_mag = float(np.mean(magnitudes))

    # Homogeneity: fraction of vectors within 45 degrees of the mean direction
    mean_angle = math.atan2(mean_vy, mean_vx)
    angles = np.arctan2(vy, vx)
    angle_diff = np.abs(angles - mean_angle)
    # Wrap to [-pi, pi]
    angle_diff = np.where(angle_diff > math.pi, 2 * math.pi - angle_diff, angle_diff)
    homogeneity = float(np.mean(angle_diff <= (math.pi / 4)))

    # Normalise magnitude (soft cap at 15px displacement)
    magnitude_norm = min(mean_mag / 15.0, 1.0)

    return mean_vx, mean_vy, homogeneity, magnitude_norm


def _classify_direction(mean_vx: float, mean_vy: float, magnitude_norm: float) -> MotionDirection:
    """Map mean flow vector to a named direction.

    Args:
        mean_vx: Mean horizontal flow (positive = rightward).
        mean_vy: Mean vertical flow (positive = downward in image coords).
        magnitude_norm: Normalised magnitude in [0, 1].

    Returns:
        Named MotionDirection string.
    """
    static_threshold = 0.04
    if magnitude_norm < static_threshold:
        return "static"

    angle_deg = math.degrees(math.atan2(mean_vy, mean_vx))

    # Toward/away detection: very small uniform magnitude all directions
    # is treated as a zoom. We use angle_deg proximity to 4 cardinal dirs.
    abs_vx = abs(mean_vx)
    abs_vy = abs(mean_vy)

    # If neither axis dominates strongly, treat as mixed
    if max(abs_vx, abs_vy) < 0.5 and magnitude_norm < 0.15:
        return "mixed"

    if -45 <= angle_deg <= 45:
        return "right"
    elif angle_deg > 135 or angle_deg < -135:
        return "left"
    elif 45 < angle_deg <= 135:
        return "down"
    else:
        return "up"


def _classify_kind(
    full_homogeneity: float,
    centre_magnitude: float,
    full_magnitude: float,
) -> MotionKind:
    """Determine whether motion is camera-driven, subject-driven, or mixed.

    Heuristic:
    - High full-frame homogeneity + high full magnitude = camera pan/tilt.
    - Low full homogeneity but high centre magnitude = subject moves in static frame.
    - Otherwise mixed.

    Args:
        full_homogeneity: Fraction of vectors aligned with the mean (0-1).
        centre_magnitude: Mean magnitude in the centre 40% of the frame.
        full_magnitude: Mean magnitude across the whole frame.

    Returns:
        MotionKind string.
    """
    static_threshold = 0.04

    if full_magnitude < static_threshold:
        return "none"

    if full_homogeneity > 0.65 and full_magnitude > 0.1:
        # Most of the frame is moving in the same direction: camera motion
        return "camera"

    # Centre motion much stronger than periphery: subject motion
    if centre_magnitude > full_magnitude * 1.5 and centre_magnitude > 0.08:
        return "subject"

    if full_homogeneity < 0.35:
        return "mixed"

    return "camera"


def _flow_with_centre_split(
    frame_a_path: Path,
    frame_b_path: Path,
) -> tuple[float, float, float, float, float, float] | None:
    """Extended flow that also computes centre-crop magnitude separately.

    Returns:
        (mean_vx, mean_vy, full_homogeneity, full_mag_norm, centre_mag_norm, confidence)
        or None on failure.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    img_a = cv2.imread(str(frame_a_path), cv2.IMREAD_GRAYSCALE)
    img_b = cv2.imread(str(frame_b_path), cv2.IMREAD_GRAYSCALE)

    if img_a is None or img_b is None:
        return None

    target_w, target_h = 320, 180
    img_a = cv2.resize(img_a, (target_w, target_h))
    img_b = cv2.resize(img_b, (target_w, target_h))

    flow = cv2.calcOpticalFlowFarneback(
        img_a, img_b, None,
        pyr_scale=0.5, levels=3, winsize=13,
        iterations=3, poly_n=5, poly_sigma=1.2, flags=0,
    )

    vx = flow[..., 0]
    vy = flow[..., 1]

    mean_vx = float(np.mean(vx))
    mean_vy = float(np.mean(vy))
    magnitudes = np.sqrt(vx ** 2 + vy ** 2)
    full_mag = float(np.mean(magnitudes))

    # Homogeneity
    if full_mag > 1e-5:
        mean_angle = math.atan2(mean_vy, mean_vx)
        angles = np.arctan2(vy, vx)
        diff = np.abs(angles - mean_angle)
        diff = np.where(diff > math.pi, 2 * math.pi - diff, diff)
        full_homogeneity = float(np.mean(diff <= (math.pi / 4)))
    else:
        full_homogeneity = 1.0

    # Centre crop: middle 40% width x 60% height
    cx0 = int(target_w * 0.30)
    cx1 = int(target_w * 0.70)
    cy0 = int(target_h * 0.20)
    cy1 = int(target_h * 0.80)
    centre_mags = magnitudes[cy0:cy1, cx0:cx1]
    centre_mag = float(np.mean(centre_mags)) if centre_mags.size > 0 else 0.0

    full_mag_norm = min(full_mag / 15.0, 1.0)
    centre_mag_norm = min(centre_mag / 15.0, 1.0)

    # Confidence: how coherent is the flow field overall
    confidence = full_homogeneity * min(1.0, full_mag / 2.0 + 0.5)
    confidence = max(0.0, min(1.0, confidence))

    return mean_vx, mean_vy, full_homogeneity, full_mag_norm, centre_mag_norm, confidence


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_shot(
    video_path: str | Path,
    start_sec: float,
    duration_sec: float,
) -> MotionProfile:
    """Analyse a single shot for optical flow characteristics.

    Samples three frames evenly across the shot and averages flow across
    two adjacent frame pairs.

    Args:
        video_path: Path to the source video file.
        start_sec: Start time within the video in seconds.
        duration_sec: Duration of the shot in seconds.

    Returns:
        MotionProfile with direction, magnitude, kind, and confidence.
        On analysis failure, returns a static/none profile with confidence=0.
    """
    video_path = Path(video_path)

    if not video_path.exists():
        logger.warning("Video not found: %s", video_path)
        return MotionProfile(direction="static", magnitude=0.0, kind="none", confidence=0.0)

    if duration_sec <= 0.1:
        return MotionProfile(direction="static", magnitude=0.0, kind="none", confidence=0.0)

    # Sample three positions in the shot
    t0 = start_sec + duration_sec * 0.2
    t1 = start_sec + duration_sec * 0.5
    t2 = start_sec + duration_sec * 0.8

    tmp = Path(tempfile.mkdtemp(prefix="ff_flow_"))

    try:
        f0 = tmp / "f0.jpg"
        f1 = tmp / "f1.jpg"
        f2 = tmp / "f2.jpg"

        ok0 = _extract_frame(video_path, t0, f0)
        ok1 = _extract_frame(video_path, t1, f1)
        ok2 = _extract_frame(video_path, t2, f2)

        if not ok0 or not ok1:
            logger.debug("Could not extract frames from %s @ %.2fs", video_path.name, start_sec)
            return MotionProfile(direction="static", magnitude=0.0, kind="none", confidence=0.0)

        results = []
        if ok0 and ok1:
            r = _flow_with_centre_split(f0, f1)
            if r is not None:
                results.append(r)
        if ok1 and ok2:
            r = _flow_with_centre_split(f1, f2)
            if r is not None:
                results.append(r)

        if not results:
            return MotionProfile(direction="static", magnitude=0.0, kind="none", confidence=0.0)

        # Average across the available pairs
        mean_vx = sum(r[0] for r in results) / len(results)
        mean_vy = sum(r[1] for r in results) / len(results)
        full_homogeneity = sum(r[2] for r in results) / len(results)
        full_mag_norm = sum(r[3] for r in results) / len(results)
        centre_mag_norm = sum(r[4] for r in results) / len(results)
        confidence = sum(r[5] for r in results) / len(results)

        direction = _classify_direction(mean_vx, mean_vy, full_mag_norm)
        kind = _classify_kind(full_homogeneity, centre_mag_norm, full_mag_norm)

        return MotionProfile(
            direction=direction,
            magnitude=round(full_mag_norm, 4),
            kind=kind,
            confidence=round(confidence, 4),
        )

    finally:
        import shutil as _shutil
        _shutil.rmtree(tmp, ignore_errors=True)


def analyze_library(raw_dir: str | Path, db_path: str | Path) -> None:
    """Analyse every shot in the library and write motion columns to the DB.

    Adds columns motion_dir, motion_mag, motion_kind, motion_conf if they
    do not exist. Skips shots that already have motion_dir populated.

    Args:
        raw_dir: Directory containing the raw source video files.
        db_path: Path to the shot_library SQLite database.
    """
    raw_dir = Path(raw_dir)
    db_path = Path(db_path)

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    # Add columns if they don't already exist
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(shots)")}
    for col, typedef in [
        ("motion_dir", "TEXT"),
        ("motion_mag", "REAL"),
        ("motion_kind", "TEXT"),
        ("motion_conf", "REAL"),
    ]:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE shots ADD COLUMN {col} {typedef}")
    conn.commit()

    # Fetch shots that haven't been analysed yet
    rows = conn.execute(
        "SELECT id, source, start_sec, end_sec, duration_sec "
        "FROM shots WHERE motion_dir IS NULL ORDER BY id ASC"
    ).fetchall()

    if not rows:
        logger.info("All shots already have motion analysis. Nothing to do.")
        conn.close()
        return

    logger.info("Analysing motion flow for %d shots in %s", len(rows), raw_dir)

    updated = 0
    skipped = 0

    for shot_id, source, start_sec, end_sec, duration_sec in rows:
        # Find source video file
        video_file = _find_video(raw_dir, source)
        if video_file is None:
            logger.debug("No video found for source '%s', skipping shot %d", source, shot_id)
            skipped += 1
            continue

        profile = analyze_shot(video_file, start_sec, duration_sec)

        conn.execute(
            "UPDATE shots SET motion_dir=?, motion_mag=?, motion_kind=?, motion_conf=? WHERE id=?",
            (profile.direction, profile.magnitude, profile.kind, profile.confidence, shot_id),
        )
        conn.commit()
        updated += 1

        if updated % 25 == 0:
            logger.info("  Motion flow: %d / %d shots analysed", updated, len(rows))

    conn.close()
    logger.info(
        "Motion flow analysis complete. Updated: %d  Skipped (no video): %d",
        updated, skipped,
    )


def _find_video(raw_dir: Path, source: str) -> Path | None:
    """Locate a video file in raw_dir matching the given source stem.

    Args:
        raw_dir: Directory to search.
        source: Source identifier string (may have or lack extension).

    Returns:
        First matching Path, or None.
    """
    stem = Path(source).stem
    for ext in (".mp4", ".mkv", ".mov", ".webm", ".avi"):
        candidate = raw_dir / f"{stem}{ext}"
        if candidate.exists():
            return candidate
        # Try with original source as-is
        candidate2 = raw_dir / f"{source}{ext}"
        if candidate2.exists():
            return candidate2
    # Glob fallback
    matches = list(raw_dir.glob(f"{stem}.*"))
    video_matches = [m for m in matches if m.suffix.lower() in _VECTOR_EXTENSIONS]
    return video_matches[0] if video_matches else None
