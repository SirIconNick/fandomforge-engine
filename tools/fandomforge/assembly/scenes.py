"""Scene detection — wrap PySceneDetect to auto-split videos into scene clips."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class SceneEntry:
    start_sec: float
    end_sec: float
    duration_sec: float


def detect_scenes(
    video_path: Path | str,
    *,
    threshold: float = 27.0,
    min_scene_sec: float = 1.0,
) -> list[SceneEntry]:
    """Detect scene boundaries in a video using PySceneDetect's adaptive detector.

    Args:
        video_path: Path to video file
        threshold: Content detection threshold (lower = more scenes)
        min_scene_sec: Minimum scene duration to avoid micro-cuts

    Returns:
        List of SceneEntry objects with start/end/duration in seconds.
    """
    try:
        from scenedetect import detect, AdaptiveDetector
    except ImportError as exc:
        raise RuntimeError(
            "PySceneDetect not installed. Install with: "
            "pip install 'scenedetect[opencv]'"
        ) from exc

    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    scene_list = detect(
        str(video_path),
        AdaptiveDetector(adaptive_threshold=threshold, min_scene_len=int(min_scene_sec * 24)),
    )

    scenes: list[SceneEntry] = []
    for start, end in scene_list:
        start_sec = start.get_seconds()
        end_sec = end.get_seconds()
        duration = end_sec - start_sec
        if duration < min_scene_sec:
            continue
        scenes.append(
            SceneEntry(
                start_sec=round(start_sec, 3),
                end_sec=round(end_sec, 3),
                duration_sec=round(duration, 3),
            )
        )
    return scenes


def split_into_scenes(
    video_path: Path | str,
    output_dir: Path | str,
    *,
    threshold: float = 27.0,
    min_scene_sec: float = 1.0,
) -> list[Path]:
    """Split a video into separate files, one per detected scene."""
    try:
        from scenedetect import detect, AdaptiveDetector, split_video_ffmpeg
    except ImportError as exc:
        raise RuntimeError("PySceneDetect + ffmpeg required.") from exc

    video_path = Path(video_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    scene_list = detect(
        str(video_path),
        AdaptiveDetector(adaptive_threshold=threshold, min_scene_len=int(min_scene_sec * 24)),
    )

    split_video_ffmpeg(
        str(video_path),
        scene_list,
        output_dir=str(output_dir),
        show_progress=False,
    )

    return sorted(output_dir.glob("*.mp4"))
