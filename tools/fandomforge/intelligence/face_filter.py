"""Face-recognition-based character filtering.

Goal: given a reference image of a character (e.g. Leon), scan source videos
for timestamps where that character is on-screen. Filter shot lists to only
include validated-character shots.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FaceMatch:
    source_id: str
    time_sec: float
    confidence: float  # 0-1, higher = better match
    face_count: int
    best_distance: float  # raw distance (lower = better)


def _have_face_recognition() -> bool:
    try:
        import face_recognition  # noqa: F401
        return True
    except ImportError:
        return False


def _sample_frames_with_ffmpeg(
    video: Path, sample_interval_sec: float, output_dir: Path, width: int = 480
) -> list[tuple[float, Path]]:
    """Sample one frame every N seconds and save as JPG. Returns [(time, path), ...]."""
    if shutil.which("ffmpeg") is None:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    frames: list[tuple[float, Path]] = []

    # Use ffmpeg to extract frames at the sample rate
    fps = 1.0 / sample_interval_sec
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostats",
        "-i", str(video),
        "-vf", f"fps={fps},scale={width}:-2",
        "-q:v", "4",
        str(output_dir / "frame_%06d.jpg"),
    ]
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            check=True,
            timeout=600,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []

    # Pair each frame with its timestamp
    jpgs = sorted(output_dir.glob("frame_*.jpg"))
    for i, jpg in enumerate(jpgs):
        time_sec = i * sample_interval_sec
        frames.append((time_sec, jpg))
    return frames


def scan_video_for_face(
    video_path: str | Path,
    reference_encoding: list[float],
    *,
    source_id: str = "",
    sample_interval_sec: float = 5.0,
    tolerance: float = 0.55,
    width: int = 480,
) -> list[FaceMatch]:
    """Scan a video at regular intervals, return timestamps where the reference face appears.

    tolerance: 0.6 is face_recognition default. Lower = stricter (fewer false positives).
    """
    if not _have_face_recognition():
        return []

    import face_recognition
    import numpy as np

    video = Path(video_path)
    if not video.exists():
        return []

    tmp = Path(tempfile.mkdtemp(prefix="ff_face_"))
    try:
        frames = _sample_frames_with_ffmpeg(video, sample_interval_sec, tmp, width=width)
        if not frames:
            return []

        ref_enc = np.array(reference_encoding, dtype=np.float64)
        matches: list[FaceMatch] = []

        for time_sec, jpg in frames:
            try:
                img = face_recognition.load_image_file(str(jpg))
            except Exception:
                continue
            face_locs = face_recognition.face_locations(img, model="hog")
            if not face_locs:
                continue
            encodings = face_recognition.face_encodings(img, face_locs)
            if not encodings:
                continue

            # Compute distances to reference
            distances = face_recognition.face_distance(encodings, ref_enc)
            best = float(min(distances))
            if best <= tolerance:
                matches.append(
                    FaceMatch(
                        source_id=source_id,
                        time_sec=time_sec,
                        confidence=max(0.0, 1.0 - (best / tolerance)),
                        face_count=len(face_locs),
                        best_distance=best,
                    )
                )

        return matches
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def encode_reference_face(
    image_path: str | Path,
) -> list[float] | None:
    """Load an image with a clear view of the target character and return face encoding."""
    if not _have_face_recognition():
        return None

    import face_recognition

    img_path = Path(image_path)
    if not img_path.exists():
        return None

    try:
        img = face_recognition.load_image_file(str(img_path))
        face_locs = face_recognition.face_locations(img, model="hog")
        if not face_locs:
            return None
        encodings = face_recognition.face_encodings(img, face_locs)
        if not encodings:
            return None
        # Use the largest face in the frame (best reference)
        sizes = [((bottom - top) * (right - left), i) for i, (top, right, bottom, left) in enumerate(face_locs)]
        _, best_idx = max(sizes)
        return encodings[best_idx].tolist()
    except Exception:
        return None


def capture_reference_from_video(
    video_path: str | Path,
    time_sec: float,
    output_jpg: str | Path,
) -> bool:
    """Save a reference frame from a video for later face encoding."""
    if shutil.which("ffmpeg") is None:
        return False

    out = Path(output_jpg)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostats",
        "-ss", f"{time_sec:.3f}",
        "-i", str(video_path),
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
        return out.exists()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def filter_shots_by_face(
    shots: list,
    raw_dir: Path | str,
    reference_encoding: list[float],
    *,
    tolerance: float = 0.55,
    window_sec: float = 3.0,
) -> tuple[list, list]:
    """Split a shot list into (keepers, rejects) based on whether the reference face is present.

    For each non-placeholder shot, samples a frame AT the shot's source timestamp.
    If the reference face appears, keep. Otherwise, reject.

    Returns (keepers, rejects).
    """
    if not _have_face_recognition():
        return (shots, [])

    import face_recognition
    import numpy as np

    raw_dir = Path(raw_dir)
    ref_enc = np.array(reference_encoding, dtype=np.float64)

    keepers: list = []
    rejects: list = []

    tmp = Path(tempfile.mkdtemp(prefix="ff_face_filter_"))
    try:
        for shot in shots:
            if shot.is_placeholder() or shot.source_id in {"", "—"}:
                keepers.append(shot)  # keep placeholders as-is
                continue

            source_files = list(raw_dir.glob(f"{shot.source_id}.*"))
            video_files = [p for p in source_files if p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}]
            if not video_files:
                keepers.append(shot)  # no source — can't check, keep
                continue

            ts = shot.source_timestamp_sec or 0.0
            # Sample 3 frames: start, middle, end of the shot
            sampled_times = [
                ts,
                ts + shot.duration_sec / 2,
                ts + shot.duration_sec - 0.1,
            ]
            found = False
            for st in sampled_times:
                frame = tmp / f"s{shot.number:04d}_t{int(st * 10)}.jpg"
                if not capture_reference_from_video(video_files[0], st, frame):
                    continue
                try:
                    img = face_recognition.load_image_file(str(frame))
                    face_locs = face_recognition.face_locations(img, model="hog")
                    if not face_locs:
                        continue
                    encodings = face_recognition.face_encodings(img, face_locs)
                    if not encodings:
                        continue
                    distances = face_recognition.face_distance(encodings, ref_enc)
                    if min(distances) <= tolerance:
                        found = True
                        break
                except Exception:
                    continue

            if found:
                keepers.append(shot)
            else:
                rejects.append(shot)

        return (keepers, rejects)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
