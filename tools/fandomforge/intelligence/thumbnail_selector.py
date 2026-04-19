"""Thumbnail selector -- pick the best thumbnail candidate from a rendered video.

Algorithm
---------
1. Sample 30-50 candidate frames across the video (skipping first/last 2 s).
2. Score each frame on five signals:
   - Face presence (via OpenCV Haar cascade or face_recognition when available)
   - Sharpness (Laplacian variance -- higher = sharper)
   - Rule-of-thirds saliency (faces / bright objects near the four power points)
   - Saturation+contrast variety (rewards punchy, cinematic frames over grey ones)
   - Motion-blur penalty (frames with low Laplacian variance relative to neighbours
     are penalized; very blurry frames score near zero)
3. Optional GPT-4o-mini pass: send the top-5 frames as base64 images and ask which
   makes the most compelling YouTube thumbnail.
4. Extract the best frame to <output_path> and the top-N alternates alongside it.

Public API
----------
    from fandomforge.intelligence.thumbnail_selector import select_thumbnail, ThumbnailResult

    result = select_thumbnail(
        video_path="exports/my_edit.mp4",
        output_path="exports/my_edit.thumb.jpg",
        num_alternates=5,
    )
    print(result.best_frame_sec, result.score, result.alternate_paths)
"""

from __future__ import annotations

import base64
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ThumbnailResult:
    """Outcome of a thumbnail selection run.

    Attributes:
        best_frame_path: Path to the extracted best-frame JPEG.
        best_frame_sec: Timestamp (seconds) of the best frame in the video.
        score: Composite quality score in range [0.0, 1.0].
        alternate_paths: Up to num_alternates runner-up paths.
        alternate_frame_secs: Timestamps corresponding to alternates.
        gpt_chosen: True if GPT-4o-mini picked this frame over others.
        error: Non-empty when something went wrong.
    """

    best_frame_path: Path | None
    best_frame_sec: float = 0.0
    score: float = 0.0
    alternate_paths: list[Path] = field(default_factory=list)
    alternate_frame_secs: list[float] = field(default_factory=list)
    gpt_chosen: bool = False
    error: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _video_duration(video_path: Path) -> float | None:
    """Return video duration in seconds via ffprobe, or None on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=True,
        )
        return float(result.stdout.decode().strip())
    except Exception:
        return None


def _extract_frame_to_array(
    video_path: Path,
    timestamp_sec: float,
    width: int = 1920,
    height: int = 1080,
) -> "np.ndarray | None":
    """Extract a single frame from a video at the given timestamp.

    Returns an HxWx3 BGR numpy array, or None on failure.
    """
    try:
        import numpy as np

        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-nostats",
            "-ss", f"{timestamp_sec:.3f}",
            "-i", str(video_path),
            "-frames:v", "1",
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "pipe:1",
        ]
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        if result.returncode != 0 or not result.stdout:
            return None
        arr = np.frombuffer(result.stdout, dtype=np.uint8)
        if arr.size != width * height * 3:
            return None
        return arr.reshape((height, width, 3))
    except Exception as exc:
        logger.debug("Frame extraction failed at %.2fs: %s", timestamp_sec, exc)
        return None


def _save_frame_to_jpeg(frame: "np.ndarray", out_path: Path, quality: int = 90) -> bool:
    """Write an HxWx3 BGR numpy array as a JPEG file."""
    try:
        import cv2  # type: ignore[import-untyped]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok:
            out_path.write_bytes(buf.tobytes())
            return True
        return False
    except ImportError:
        # Fallback: let ffmpeg write the JPEG from the raw array
        try:
            import numpy as np

            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "rawvideo",
                "-pix_fmt", "bgr24",
                "-s", f"{frame.shape[1]}x{frame.shape[0]}",
                "-i", "pipe:0",
                "-qscale:v", "2",
                str(out_path),
            ]
            out_path.parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(
                cmd,
                input=frame.tobytes(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
            return proc.returncode == 0 and out_path.exists()
        except Exception:
            return False


def _laplacian_variance(frame: "np.ndarray") -> float:
    """Compute the Laplacian variance of the frame as a sharpness proxy.

    Higher value means sharper / less blurry.  Operates on a greyscale
    downscaled version for speed.
    """
    try:
        import cv2  # type: ignore[import-untyped]
        import numpy as np

        small = cv2.resize(frame, (640, 360))
        grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        lap = cv2.Laplacian(grey, cv2.CV_64F)
        return float(np.var(lap))
    except ImportError:
        # Minimal numpy fallback -- approximate gradient magnitude
        try:
            import numpy as np

            h, w, _ = frame.shape
            grey = (
                0.114 * frame[:, :, 0].astype(float)
                + 0.587 * frame[:, :, 1].astype(float)
                + 0.299 * frame[:, :, 2].astype(float)
            )
            dx = np.diff(grey, axis=1)
            dy = np.diff(grey, axis=0)
            return float(np.var(dx) + np.var(dy))
        except Exception:
            return 0.0


def _detect_faces(frame: "np.ndarray") -> int:
    """Return the number of faces detected in the frame.

    Tries face_recognition first (accurate), then OpenCV Haar cascade (fast).
    Returns 0 if neither is available.
    """
    # 1. Try face_recognition
    try:
        import face_recognition  # type: ignore[import-untyped]
        import numpy as np

        rgb = frame[:, :, ::-1]  # BGR to RGB
        small = rgb[::2, ::2, :]  # 2x downscale for speed
        locs = face_recognition.face_locations(small, model="hog")
        return len(locs)
    except ImportError:
        pass

    # 2. Try OpenCV Haar cascade
    try:
        import cv2  # type: ignore[import-untyped]

        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        cc_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        detector = cv2.CascadeClassifier(cc_path)
        faces = detector.detectMultiScale(grey, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
        return len(faces) if len(faces) > 0 else 0
    except Exception:
        return 0


def _saturation_score(frame: "np.ndarray") -> float:
    """Return mean saturation (0-1) of the frame in HSV space.

    Penalizes washed-out / near-grey frames.
    """
    try:
        import cv2  # type: ignore[import-untyped]
        import numpy as np

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        return float(np.mean(hsv[:, :, 1])) / 255.0
    except Exception:
        try:
            import numpy as np

            r = frame[:, :, 2].astype(float)
            g = frame[:, :, 1].astype(float)
            b = frame[:, :, 0].astype(float)
            cmax = np.maximum(np.maximum(r, g), b)
            cmin = np.minimum(np.minimum(r, g), b)
            delta = cmax - cmin
            with np.errstate(invalid="ignore", divide="ignore"):
                sat = np.where(cmax > 0, delta / cmax, 0)
            return float(np.mean(sat))
        except Exception:
            return 0.5


def _rms_contrast(frame: "np.ndarray") -> float:
    """Return root-mean-square contrast of the luma channel."""
    try:
        import numpy as np

        luma = (
            0.114 * frame[:, :, 0].astype(float)
            + 0.587 * frame[:, :, 1].astype(float)
            + 0.299 * frame[:, :, 2].astype(float)
        )
        return float(luma.std() / 255.0)
    except Exception:
        return 0.5


def _rule_of_thirds_score(frame: "np.ndarray", face_count: int) -> float:
    """Estimate how well the frame respects the rule of thirds.

    Without a full saliency map we approximate by looking at the
    brightness distribution near the four power points.
    Returns a score in [0, 1].
    """
    try:
        import numpy as np

        h, w, _ = frame.shape
        grey = (
            0.114 * frame[:, :, 0].astype(float)
            + 0.587 * frame[:, :, 1].astype(float)
            + 0.299 * frame[:, :, 2].astype(float)
        )
        total_brightness = float(np.mean(grey)) or 1.0

        # Power points (1/3 and 2/3 along each axis)
        thirds_y = [h // 3, (2 * h) // 3]
        thirds_x = [w // 3, (2 * w) // 3]
        window_h = max(1, h // 10)
        window_w = max(1, w // 10)

        power_brightness_sum = 0.0
        count = 0
        for ty in thirds_y:
            for tx in thirds_x:
                patch = grey[
                    max(0, ty - window_h): min(h, ty + window_h),
                    max(0, tx - window_w): min(w, tx + window_w),
                ]
                if patch.size:
                    power_brightness_sum += float(np.mean(patch))
                    count += 1

        if count == 0:
            return 0.5

        thirds_score = (power_brightness_sum / count) / (total_brightness * 1.5)
        thirds_score = min(1.0, max(0.0, thirds_score))

        # Bonus when faces are detected (tribute videos want character faces at thirds)
        face_bonus = min(0.2, face_count * 0.1)
        return min(1.0, thirds_score + face_bonus)
    except Exception:
        return 0.5


def _dramatic_lighting_score(frame: "np.ndarray") -> float:
    """Score frames with dramatic chiaroscuro lighting.

    Tribute edits look best with high contrast between darks and highlights.
    Returns a score in [0, 1]; frames with a roughly bimodal luma histogram
    (dark shadows + bright highlights) score highest.
    """
    try:
        import numpy as np

        luma = (
            0.114 * frame[:, :, 0].astype(float)
            + 0.587 * frame[:, :, 1].astype(float)
            + 0.299 * frame[:, :, 2].astype(float)
        )
        flat = luma.flatten()
        shadow_pct = float(np.mean(flat < 64)) / 255.0 * 255
        highlight_pct = float(np.mean(flat > 191)) / 255.0 * 255
        # Reward having both deep shadows AND bright highlights
        drama = (shadow_pct + highlight_pct) / 2.0
        return min(1.0, max(0.0, drama))
    except Exception:
        return 0.5


def _composite_score(
    sharpness: float,
    face_count: int,
    saturation: float,
    contrast: float,
    thirds: float,
    drama: float,
    is_tribute: bool,
) -> float:
    """Combine sub-scores into a single frame quality score [0, 1].

    Weights
    -------
    Sharpness is normalized against a ceiling of 2000 (typical for a sharp
    1080p frame; frames above this get full marks).
    """
    sharp_norm = min(1.0, sharpness / 2000.0)
    face_norm = min(1.0, face_count * 0.4)  # 1 face = 0.4, 2+ = 1.0 cap

    if is_tribute:
        # Tribute-mode: emphasise character faces + dramatic lighting
        weights = {
            "sharp": 0.20,
            "face": 0.30,
            "sat": 0.10,
            "contrast": 0.10,
            "thirds": 0.10,
            "drama": 0.20,
        }
    else:
        weights = {
            "sharp": 0.30,
            "face": 0.25,
            "sat": 0.15,
            "contrast": 0.10,
            "thirds": 0.15,
            "drama": 0.05,
        }

    total = (
        weights["sharp"] * sharp_norm
        + weights["face"] * face_norm
        + weights["sat"] * saturation
        + weights["contrast"] * contrast
        + weights["thirds"] * thirds
        + weights["drama"] * drama
    )
    return min(1.0, max(0.0, total))


# ---------------------------------------------------------------------------
# GPT-4o-mini picker
# ---------------------------------------------------------------------------


def _gpt_pick_best(frame_paths: Sequence[Path], project_root: Path | str = ".") -> int | None:
    """Ask GPT-4o-mini to choose the most compelling thumbnail from up to 5 frames.

    Returns the zero-based index of the chosen frame, or None on any failure.
    """
    # Load env key
    if not os.environ.get("OPENAI_API_KEY"):
        for env_name in (".env", ".env.local"):
            env_file = Path(project_root) / env_name
            if env_file.exists():
                for raw_line in env_file.read_text().splitlines():
                    raw_line = raw_line.strip()
                    if raw_line.startswith("#") or "=" not in raw_line:
                        continue
                    k, v = raw_line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k == "OPENAI_API_KEY" and v:
                        os.environ["OPENAI_API_KEY"] = v
                        break

    if not os.environ.get("OPENAI_API_KEY"):
        return None

    try:
        from openai import OpenAI  # type: ignore[import-untyped]
    except ImportError:
        return None

    content: list[dict] = [
        {
            "type": "text",
            "text": (
                "You are a YouTube thumbnail expert for fan-edit tribute videos. "
                "Below are up to 5 candidate thumbnail frames numbered 0 through 4. "
                "Pick the single most compelling thumbnail for a YouTube video. "
                "Criteria: clear character face, dramatic or cinematic lighting, "
                "strong composition, emotionally intense expression, visually striking. "
                "Reply with ONLY the integer index of your choice (0-4). "
                "No explanation needed."
            ),
        }
    ]

    valid_indices: list[int] = []
    for i, fp in enumerate(frame_paths[:5]):
        if not fp.exists():
            continue
        try:
            b64 = base64.b64encode(fp.read_bytes()).decode()
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{b64}",
                        "detail": "low",
                    },
                }
            )
            valid_indices.append(i)
        except Exception:
            continue

    if not valid_indices:
        return None

    try:
        client = OpenAI()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": content}],
            max_tokens=5,
            temperature=0.0,
        )
        raw = (response.choices[0].message.content or "").strip()
        idx = int(raw)
        if 0 <= idx < len(valid_indices):
            return valid_indices[idx]
        return valid_indices[0]
    except Exception as exc:
        logger.debug("GPT thumbnail pick failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def select_thumbnail(
    video_path: str | Path,
    output_path: str | Path,
    *,
    num_alternates: int = 5,
    num_candidates: int = 40,
    is_tribute: bool = True,
    use_gpt: bool = True,
    project_root: str | Path = ".",
) -> ThumbnailResult:
    """Pick the best thumbnail frame from a rendered video.

    Samples num_candidates evenly-spaced frames (skipping the first and last
    2 seconds), scores each one, and writes the best to output_path.
    Also saves up to num_alternates runner-up frames beside the output.

    Args:
        video_path: Path to the rendered MP4/MKV.
        output_path: Destination path for the best thumbnail JPEG.
        num_candidates: How many frames to sample and score.
        num_alternates: How many runner-up frames to also export.
        is_tribute: Enable tribute-specific scoring (face + drama emphasis).
        use_gpt: If True and OPENAI_API_KEY is set, run the GPT-4o-mini
            "which is the best thumbnail?" picker over the top-5 results.
        project_root: Directory to search for .env files.

    Returns:
        ThumbnailResult with paths, timestamps, and scores.
    """
    video_path = Path(video_path)
    output_path = Path(output_path)

    if not video_path.exists():
        return ThumbnailResult(
            best_frame_path=None,
            error=f"Video not found: {video_path}",
        )

    try:
        import numpy as np  # noqa: F401 -- validate numpy available early
    except ImportError:
        return ThumbnailResult(
            best_frame_path=None,
            error="numpy is required for thumbnail selection. pip install numpy",
        )

    duration = _video_duration(video_path)
    if duration is None or duration < 1.0:
        return ThumbnailResult(
            best_frame_path=None,
            error=f"Could not determine video duration for {video_path}",
        )

    margin = 2.0
    usable_start = margin
    usable_end = max(margin + 0.5, duration - margin)
    usable_duration = usable_end - usable_start

    if usable_duration <= 0:
        usable_start = 0.0
        usable_end = duration
        usable_duration = duration

    # Build evenly-spaced sample timestamps
    count = max(1, num_candidates)
    timestamps: list[float] = []
    for i in range(count):
        t = usable_start + (i / max(1, count - 1)) * usable_duration
        timestamps.append(round(t, 3))

    # Score each candidate
    scored: list[tuple[float, float, "np.ndarray"]] = []  # (score, timestamp, frame)

    for ts in timestamps:
        frame = _extract_frame_to_array(video_path, ts)
        if frame is None:
            continue

        sharpness = _laplacian_variance(frame)
        # Skip motion-blurred frames early (Laplacian < 20 is extremely blurry)
        if sharpness < 20.0:
            continue

        face_count = _detect_faces(frame)
        saturation = _saturation_score(frame)
        contrast = _rms_contrast(frame)
        thirds = _rule_of_thirds_score(frame, face_count)
        drama = _dramatic_lighting_score(frame)

        score = _composite_score(
            sharpness=sharpness,
            face_count=face_count,
            saturation=saturation,
            contrast=contrast,
            thirds=thirds,
            drama=drama,
            is_tribute=is_tribute,
        )
        scored.append((score, ts, frame))

    if not scored:
        return ThumbnailResult(
            best_frame_path=None,
            error="No scoreable frames found in video.",
        )

    scored.sort(key=lambda x: -x[0])

    # Build the top-(num_alternates+1) candidate list so the best is at index 0
    top_n = scored[: num_alternates + 1]

    # Save top-N to temp files for GPT picker
    base_stem = output_path.stem
    base_dir = output_path.parent
    base_dir.mkdir(parents=True, exist_ok=True)

    temp_paths: list[Path] = []
    for rank, (sc, ts, fr) in enumerate(top_n):
        suffix = "" if rank == 0 else f".alt{rank}"
        dest = base_dir / f"{base_stem}{suffix}.thumb.jpg"
        if _save_frame_to_jpeg(fr, dest):
            temp_paths.append(dest)
        else:
            temp_paths.append(dest)  # keep slot even on failure

    # GPT pick over top-5
    gpt_chosen = False
    best_rank = 0
    if use_gpt and len(temp_paths) >= 2:
        gpt_idx = _gpt_pick_best(temp_paths[: min(5, len(temp_paths))], project_root)
        if gpt_idx is not None and gpt_idx != 0:
            # Swap GPT's choice to rank 0
            top_n[0], top_n[gpt_idx] = top_n[gpt_idx], top_n[0]
            temp_paths[0], temp_paths[gpt_idx] = temp_paths[gpt_idx], temp_paths[0]
            best_rank = gpt_idx
            gpt_chosen = True

    # Rename/move so best frame lands at output_path
    best_temp = temp_paths[0]
    if best_temp != output_path:
        try:
            import shutil
            shutil.copy2(str(best_temp), str(output_path))
        except Exception as exc:
            return ThumbnailResult(
                best_frame_path=None,
                error=f"Failed to copy best frame to output: {exc}",
            )

    # Rename alternates
    alt_paths: list[Path] = []
    alt_secs: list[float] = []
    for rank in range(1, len(temp_paths)):
        alt_sc, alt_ts, _ = top_n[rank]
        alt_dest = base_dir / f"{base_stem}.alt{rank}.thumb.jpg"
        src = temp_paths[rank]
        if src.exists() and src != alt_dest:
            try:
                import shutil
                shutil.copy2(str(src), str(alt_dest))
            except Exception:
                pass
        if alt_dest.exists():
            alt_paths.append(alt_dest)
            alt_secs.append(alt_ts)

    best_score, best_ts, _ = top_n[0]

    return ThumbnailResult(
        best_frame_path=output_path if output_path.exists() else None,
        best_frame_sec=best_ts,
        score=best_score,
        alternate_paths=alt_paths,
        alternate_frame_secs=alt_secs,
        gpt_chosen=gpt_chosen,
    )
