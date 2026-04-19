"""Face and gaze detection per shot.

For every shot with a visible character, detects faces and estimates gaze
direction from head pose (face angle + landmark-based yaw/pitch estimation).
Results are persisted in the shot_library SQLite database.

Detection pipeline:
1. Sample up to 5 frames from the shot.
2. Run face detection on each frame (dlib HOG detector as primary, OpenCV
   Haar cascade as fallback).
3. For detected faces, estimate head pose yaw/pitch from facial landmarks
   (dlib 68-point predictor) using the relationship between eye positions,
   nose tip, and chin.
4. Aggregate gaze direction across frames and return the dominant direction.

Gaze direction mapping:
  - Large rightward yaw = looking right (character's right in screen space)
  - Large leftward yaw = looking left
  - Upward pitch = looking up
  - Downward pitch = looking down
  - Near-zero yaw+pitch = looking at camera (center)
  - Face detected but head rotated beyond threshold = off-screen
  - No face detected = none
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

GazeDirection = Literal["left", "right", "up", "down", "center", "off_screen", "none"]

# ---------------------------------------------------------------------------
# Thresholds for head pose classification
# ---------------------------------------------------------------------------

_YAW_THRESHOLD_DEG = 18.0   # beyond this = looking left/right
_PITCH_THRESHOLD_DEG = 14.0  # beyond this = looking up/down
_OFF_SCREEN_THRESHOLD_DEG = 35.0  # beyond this = off-screen/away

# Landmark indices for simplified pose estimation (dlib 68-point model)
_LEFT_EYE_LEFT = 36
_LEFT_EYE_RIGHT = 39
_RIGHT_EYE_LEFT = 42
_RIGHT_EYE_RIGHT = 45
_NOSE_TIP = 30
_CHIN = 8
_LEFT_MOUTH = 48
_RIGHT_MOUTH = 54

# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------


@dataclass
class GazeProfile:
    """Per-shot gaze characterisation from face detection analysis.

    Attributes:
        gaze_dir: Dominant gaze direction tag across all detected faces and frames.
        face_count: Maximum number of faces detected in any single frame.
        confidence: Confidence in [0, 1]. Based on detection consistency.
    """

    gaze_dir: GazeDirection
    face_count: int
    confidence: float


# ---------------------------------------------------------------------------
# Lazy-load dlib resources
# ---------------------------------------------------------------------------

_dlib_detector = None
_dlib_predictor = None
_predictor_path: Path | None = None
_haar_cascade = None
_dlib_available = False
_haar_available = False


def _load_dlib() -> bool:
    """Attempt to load dlib detector and 68-point landmark predictor.

    Returns:
        True if both components loaded successfully.
    """
    global _dlib_detector, _dlib_predictor, _predictor_path, _dlib_available

    if _dlib_available:
        return True

    try:
        import dlib  # type: ignore

        _dlib_detector = dlib.get_frontal_face_detector()

        # Locate the predictor model file
        candidate_paths = [
            Path("/Users/damato/Video Project/.venv/lib/python3.14/site-packages/face_recognition_models/models/shape_predictor_68_face_landmarks.dat"),
            Path.home() / ".cache" / "dlib" / "shape_predictor_68_face_landmarks.dat",
        ]
        # Also search in common pip install paths
        import sys
        for sp in sys.path:
            sp_path = Path(sp)
            for sub in ["face_recognition_models/models", "dlib_models"]:
                cand = sp_path / sub / "shape_predictor_68_face_landmarks.dat"
                candidate_paths.append(cand)

        for p in candidate_paths:
            if p.exists():
                _predictor_path = p
                _dlib_predictor = dlib.shape_predictor(str(p))
                _dlib_available = True
                logger.debug("dlib predictor loaded from %s", p)
                return True

        # Predictor not found, but we can still detect faces without landmarks
        logger.warning("dlib 68-pt predictor .dat file not found; gaze estimation will be approximate")
        _dlib_available = True  # detector only
        return True

    except ImportError:
        logger.info("dlib not available; falling back to OpenCV Haar cascade")
        return False


def _load_haar() -> bool:
    """Load OpenCV Haar cascade face detector as fallback.

    Returns:
        True if loaded successfully.
    """
    global _haar_cascade, _haar_available

    if _haar_available:
        return True

    try:
        import cv2
        data_dir = Path(cv2.__file__).parent / "data"
        cascade_path = data_dir / "haarcascade_frontalface_default.xml"
        if not cascade_path.exists():
            # Try via cv2.data.haarcascades (newer OpenCV)
            cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"

        if cascade_path.exists():
            _haar_cascade = cv2.CascadeClassifier(str(cascade_path))
            _haar_available = True
            logger.debug("OpenCV Haar cascade loaded from %s", cascade_path)
            return True

        logger.warning("Haar cascade XML not found")
        return False

    except Exception as exc:
        logger.warning("Could not load Haar cascade: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------


def _extract_frame_for_gaze(video_path: Path, time_sec: float, out_path: Path) -> bool:
    """Extract a frame scaled to 640px width for face detection.

    Args:
        video_path: Source video.
        time_sec: Seek offset in seconds.
        out_path: Destination JPEG.

    Returns:
        True on success.
    """
    import shutil

    if shutil.which("ffmpeg") is None:
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostats",
        "-ss", f"{time_sec:.4f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-vf", "scale=640:-2",
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


# ---------------------------------------------------------------------------
# Face detection and pose estimation
# ---------------------------------------------------------------------------


def _detect_faces_dlib(img_bgr: "cv2.Mat") -> list[tuple[int, int, int, int]]:
    """Detect faces using dlib HOG detector.

    Returns a list of (x, y, w, h) bounding boxes.
    """
    import cv2

    if _dlib_detector is None:
        return []

    import dlib  # type: ignore

    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    dets = _dlib_detector(rgb, 1)
    boxes = []
    for d in dets:
        x = max(0, d.left())
        y = max(0, d.top())
        w = d.right() - d.left()
        h = d.bottom() - d.top()
        if w > 20 and h > 20:
            boxes.append((x, y, w, h))
    return boxes


def _detect_faces_haar(img_bgr: "cv2.Mat") -> list[tuple[int, int, int, int]]:
    """Detect faces using OpenCV Haar cascade.

    Returns a list of (x, y, w, h) bounding boxes.
    """
    import cv2

    if _haar_cascade is None:
        return []

    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    dets = _haar_cascade.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(40, 40),
    )
    if dets is None or len(dets) == 0:
        return []
    return [(int(x), int(y), int(w), int(h)) for x, y, w, h in dets]


def _estimate_gaze_from_landmarks(
    img_bgr: "cv2.Mat",
    face_box: tuple[int, int, int, int],
) -> GazeDirection | None:
    """Use dlib 68-pt landmarks to estimate yaw/pitch and map to a gaze direction.

    This is a simplified head-pose estimator. It computes:
    - Yaw from the horizontal asymmetry of the eye midpoints vs nose tip.
    - Pitch from the vertical ratio of forehead area to lower-face area.

    Args:
        img_bgr: Full frame in BGR format.
        face_box: (x, y, w, h) bounding box of the detected face.

    Returns:
        GazeDirection or None if landmarks unavailable.
    """
    if _dlib_predictor is None:
        return None

    try:
        import cv2
        import dlib  # type: ignore

        x, y, w, h = face_box
        rect = dlib.rectangle(x, y, x + w, y + h)
        rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        shape = _dlib_predictor(rgb, rect)

        pts = [(shape.part(i).x, shape.part(i).y) for i in range(68)]

        # Eye midpoints
        left_eye_cx = (pts[_LEFT_EYE_LEFT][0] + pts[_LEFT_EYE_RIGHT][0]) / 2
        right_eye_cx = (pts[_RIGHT_EYE_LEFT][0] + pts[_RIGHT_EYE_RIGHT][0]) / 2
        left_eye_cy = (pts[_LEFT_EYE_LEFT][1] + pts[_LEFT_EYE_RIGHT][1]) / 2
        right_eye_cy = (pts[_RIGHT_EYE_LEFT][1] + pts[_RIGHT_EYE_RIGHT][1]) / 2

        eye_center_x = (left_eye_cx + right_eye_cx) / 2
        eye_center_y = (left_eye_cy + right_eye_cy) / 2

        nose_x = pts[_NOSE_TIP][0]
        nose_y = pts[_NOSE_TIP][1]
        chin_y = pts[_CHIN][1]

        face_width = float(w) if w > 0 else 1.0
        face_height = float(h) if h > 0 else 1.0

        # Yaw: offset of nose from eye-centre, normalised by face width
        # Positive = nose is to the right of eye centre = head turned right
        yaw_norm = (nose_x - eye_center_x) / face_width
        # Scale to approximate degrees (empirical: 0.3 norm ~ 30 deg)
        yaw_deg = yaw_norm * 90.0

        # Pitch: normalised position of nose tip between eye centre and chin
        # 0.5 is neutral, <0.5 tilted up, >0.5 tilted down
        if chin_y > eye_center_y:
            pitch_norm = (nose_y - eye_center_y) / (chin_y - eye_center_y + 1e-6)
            pitch_deg = (pitch_norm - 0.45) * 60.0  # offset so centre is neutral
        else:
            pitch_deg = 0.0

        # Off-screen check
        if abs(yaw_deg) > _OFF_SCREEN_THRESHOLD_DEG or abs(pitch_deg) > _OFF_SCREEN_THRESHOLD_DEG:
            return "off_screen"

        # Classify
        if abs(yaw_deg) < _YAW_THRESHOLD_DEG and abs(pitch_deg) < _PITCH_THRESHOLD_DEG:
            return "center"
        if abs(yaw_deg) >= abs(pitch_deg):
            return "right" if yaw_deg > 0 else "left"
        else:
            return "down" if pitch_deg > 0 else "up"

    except Exception as exc:
        logger.debug("Landmark estimation failed: %s", exc)
        return None


def _estimate_gaze_no_landmarks(
    img_bgr: "cv2.Mat",
    face_box: tuple[int, int, int, int],
    frame_w: int,
) -> GazeDirection:
    """Approximate gaze from face box position alone (no landmarks).

    When the face centre is to the right of frame centre, the character is
    likely looking somewhat right (common framing). This is a rough fallback.

    Args:
        img_bgr: Full frame (unused here, included for API consistency).
        face_box: (x, y, w, h) bounding box.
        frame_w: Full frame width in pixels.

    Returns:
        Approximate GazeDirection.
    """
    x, y, w, h = face_box
    face_cx = x + w / 2
    frame_cx = frame_w / 2

    offset_ratio = (face_cx - frame_cx) / (frame_w / 2 + 1e-6)
    # Face on left half: character probably looking right (rule of thirds framing)
    # Face on right half: probably looking left
    if offset_ratio < -0.25:
        return "right"
    elif offset_ratio > 0.25:
        return "left"
    return "center"


def _analyse_frame_for_gaze(frame_path: Path) -> tuple[int, GazeDirection | None, float]:
    """Detect faces and estimate gaze in a single frame.

    Args:
        frame_path: Path to a JPEG frame.

    Returns:
        (face_count, gaze_direction or None, confidence)
    """
    try:
        import cv2
        import numpy as np

        img = cv2.imread(str(frame_path))
        if img is None:
            return 0, None, 0.0

        frame_h, frame_w = img.shape[:2]

        # Try dlib first, fall back to Haar
        faces: list[tuple[int, int, int, int]] = []
        if _dlib_available and _dlib_detector is not None:
            faces = _detect_faces_dlib(img)
        if not faces and _haar_available and _haar_cascade is not None:
            faces = _detect_faces_haar(img)

        if not faces:
            return 0, "none", 0.3

        # Use the largest face (primary subject)
        largest = max(faces, key=lambda b: b[2] * b[3])

        # Try landmark-based estimation first
        gaze = _estimate_gaze_from_landmarks(img, largest)
        if gaze is None:
            gaze = _estimate_gaze_no_landmarks(img, largest, frame_w)

        confidence = 0.75 if _dlib_predictor is not None else 0.50
        return len(faces), gaze, confidence

    except Exception as exc:
        logger.debug("Frame gaze analysis failed: %s", exc)
        return 0, None, 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_gaze(
    video_path: str | Path,
    start_sec: float,
    duration_sec: float,
) -> GazeProfile:
    """Detect faces and estimate gaze direction for a single shot.

    Samples up to 5 frames across the shot and aggregates results.

    Args:
        video_path: Path to source video file.
        start_sec: Shot start time in seconds.
        duration_sec: Shot duration in seconds.

    Returns:
        GazeProfile with gaze_dir, face_count, and confidence.
    """
    video_path = Path(video_path)

    _load_dlib()
    _load_haar()

    if not video_path.exists():
        logger.warning("Video not found: %s", video_path)
        return GazeProfile(gaze_dir="none", face_count=0, confidence=0.0)

    if duration_sec <= 0.05:
        return GazeProfile(gaze_dir="none", face_count=0, confidence=0.0)

    # Sample 5 positions distributed across the shot
    n_samples = min(5, max(1, int(duration_sec / 0.3)))
    sample_times = [
        start_sec + duration_sec * (i + 1) / (n_samples + 1)
        for i in range(n_samples)
    ]

    tmp = Path(tempfile.mkdtemp(prefix="ff_gaze_"))

    try:
        per_frame_results: list[tuple[int, GazeDirection | None, float]] = []

        for idx, t in enumerate(sample_times):
            fp = tmp / f"frame_{idx:02d}.jpg"
            if _extract_frame_for_gaze(video_path, t, fp):
                result = _analyse_frame_for_gaze(fp)
                per_frame_results.append(result)

        if not per_frame_results:
            return GazeProfile(gaze_dir="none", face_count=0, confidence=0.0)

        max_faces = max(r[0] for r in per_frame_results)
        valid = [r for r in per_frame_results if r[1] is not None and r[1] != "none"]

        if not valid:
            return GazeProfile(gaze_dir="none", face_count=max_faces, confidence=0.25)

        # Vote on dominant gaze direction
        from collections import Counter
        direction_votes = Counter(r[1] for r in valid)
        dominant_gaze: GazeDirection = direction_votes.most_common(1)[0][0]  # type: ignore[assignment]
        vote_fraction = direction_votes[dominant_gaze] / len(valid)

        avg_confidence = sum(r[2] for r in valid) / len(valid)
        confidence = round(avg_confidence * vote_fraction, 4)

        return GazeProfile(
            gaze_dir=dominant_gaze,
            face_count=max_faces,
            confidence=confidence,
        )

    finally:
        import shutil as _shutil
        _shutil.rmtree(tmp, ignore_errors=True)


def analyze_library(raw_dir: str | Path, db_path: str | Path) -> None:
    """Analyse every shot in the library for gaze and write results to the DB.

    Adds columns gaze_dir, face_count, gaze_conf if they do not exist.
    Skips shots that already have gaze_dir populated.

    Args:
        raw_dir: Directory containing the raw source video files.
        db_path: Path to the shot_library SQLite database.
    """
    raw_dir = Path(raw_dir)
    db_path = Path(db_path)

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    _load_dlib()
    _load_haar()

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(shots)")}
    for col, typedef in [
        ("gaze_dir", "TEXT"),
        ("face_count", "INTEGER"),
        ("gaze_conf", "REAL"),
    ]:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE shots ADD COLUMN {col} {typedef}")
    conn.commit()

    rows = conn.execute(
        "SELECT id, source, start_sec, end_sec, duration_sec "
        "FROM shots WHERE gaze_dir IS NULL ORDER BY id ASC"
    ).fetchall()

    if not rows:
        logger.info("All shots already have gaze analysis. Nothing to do.")
        conn.close()
        return

    logger.info("Analysing gaze for %d shots in %s", len(rows), raw_dir)

    updated = 0
    skipped = 0

    for shot_id, source, start_sec, end_sec, duration_sec in rows:
        video_file = _find_video(raw_dir, source)
        if video_file is None:
            logger.debug("No video for source '%s', skipping shot %d", source, shot_id)
            skipped += 1
            continue

        profile = detect_gaze(video_file, start_sec, duration_sec)

        conn.execute(
            "UPDATE shots SET gaze_dir=?, face_count=?, gaze_conf=? WHERE id=?",
            (profile.gaze_dir, profile.face_count, profile.confidence, shot_id),
        )
        conn.commit()
        updated += 1

        if updated % 25 == 0:
            logger.info("  Gaze analysis: %d / %d shots processed", updated, len(rows))

    conn.close()
    logger.info(
        "Gaze analysis complete. Updated: %d  Skipped (no video): %d",
        updated, skipped,
    )


def _find_video(raw_dir: Path, source: str) -> Path | None:
    """Locate a video file in raw_dir matching the given source stem.

    Args:
        raw_dir: Directory to search.
        source: Source identifier (may include or omit extension).

    Returns:
        First matching Path, or None.
    """
    _extensions = {".mp4", ".mkv", ".mov", ".webm", ".avi"}
    stem = Path(source).stem
    for ext in (".mp4", ".mkv", ".mov", ".webm", ".avi"):
        candidate = raw_dir / f"{stem}{ext}"
        if candidate.exists():
            return candidate
        candidate2 = raw_dir / f"{source}{ext}"
        if candidate2.exists():
            return candidate2
    matches = list(raw_dir.glob(f"{stem}.*"))
    video_matches = [m for m in matches if m.suffix.lower() in _extensions]
    return video_matches[0] if video_matches else None
