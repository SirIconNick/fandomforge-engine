"""Transition matcher — pick a transition for every cut based on motion flow.

For each consecutive shot pair in a shot-list, sample frames at the tail of
shot A and the head of shot B, compute optical flow direction on both, and
choose the transition from `docs/knowledge/transition-types.md`:

- Similar motion direction and mid-high magnitude -> whip_pan (matched dir)
- Similar color but very different motion -> flash_cut
- Same motion along an edge (e.g. 'cut on action') -> match_cut
- One shot has a drop boundary in audio -> flash_stack or speed_ramp
- None of the above -> hard_cut
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fandomforge import __version__
from fandomforge.validation import validate, validate_and_write

logger = logging.getLogger(__name__)


__all__ = [
    "TransitionConfig",
    "match_transitions",
    "match_transitions_from_files",
]


@dataclass
class TransitionConfig:
    sample_frames: int = 3  # per side (tail of A, head of B)
    frame_stride_sec: float = 0.08
    whip_magnitude_threshold: float = 8.0  # pixels per sample
    whip_angle_tolerance_deg: float = 35.0
    match_cut_dot_min: float = 0.85  # cosine between motion vectors
    speed_ramp_on_drops: bool = True
    flash_cut_duration_frames: int = 3


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tc_to_sec(tc: str) -> float:
    h, m, s = tc.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _sample_frames_to_arrays(
    video: Path,
    center_sec: float,
    count: int,
    stride_sec: float,
) -> list[Any] | None:
    """Sample `count` frames centered on `center_sec`, return grayscale arrays.

    Returns None if ffmpeg isn't available or extraction fails. Requires
    numpy + Pillow — both are required deps.
    """
    if shutil.which("ffmpeg") is None:
        return None
    try:
        import numpy as np  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError:
        return None

    offsets = [(i - (count - 1) / 2.0) * stride_sec for i in range(count)]
    arrays: list[Any] = []
    tmp = Path(tempfile.mkdtemp(prefix="ff_flow_"))
    try:
        for i, off in enumerate(offsets):
            t = max(0.0, center_sec + off)
            out = tmp / f"s_{i:03d}.jpg"
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error", "-nostats",
                "-ss", f"{t:.3f}",
                "-i", str(video),
                "-frames:v", "1",
                "-vf", "scale=160:-2",
                "-q:v", "4",
                str(out),
            ]
            try:
                subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    check=True,
                    timeout=10,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                continue
            if not out.exists():
                continue
            img = Image.open(out).convert("L")
            arrays.append(np.asarray(img, dtype=np.float32))
        return arrays if len(arrays) >= 2 else None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _estimate_motion(frames: list[Any]) -> tuple[float, float]:
    """Crude motion estimate: phase correlation between consecutive frames.

    Returns (angle_degrees, magnitude_pixels). Degrees convention: 0 = right,
    90 = down.
    """
    import numpy as np  # type: ignore

    if len(frames) < 2:
        return (0.0, 0.0)

    dxs: list[float] = []
    dys: list[float] = []

    for a, b in zip(frames[:-1], frames[1:]):
        if a.shape != b.shape:
            h = min(a.shape[0], b.shape[0])
            w = min(a.shape[1], b.shape[1])
            a = a[:h, :w]
            b = b[:h, :w]
        # Phase correlation via FFT.
        A = np.fft.fft2(a)
        B = np.fft.fft2(b)
        R = A * np.conj(B)
        denom = np.abs(R)
        denom[denom == 0] = 1e-9
        R /= denom
        corr = np.fft.ifft2(R).real
        peak = np.unravel_index(int(np.argmax(corr)), corr.shape)
        dy, dx = peak
        if dy > corr.shape[0] / 2:
            dy -= corr.shape[0]
        if dx > corr.shape[1] / 2:
            dx -= corr.shape[1]
        dxs.append(float(dx))
        dys.append(float(dy))

    if not dxs:
        return (0.0, 0.0)

    mean_dx = float(np.mean(dxs))
    mean_dy = float(np.mean(dys))
    mag = math.hypot(mean_dx, mean_dy)
    angle = math.degrees(math.atan2(mean_dy, mean_dx)) % 360
    return angle, mag


def _angle_diff(a: float, b: float) -> float:
    """Smallest absolute angle difference in degrees (0..180)."""
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _dot(v1: tuple[float, float], v2: tuple[float, float]) -> float:
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 == 0 or n2 == 0:
        return 0.0
    return (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)


def _near_drop(time_sec: float, drops: list[dict[str, Any]], window_sec: float = 0.5) -> bool:
    return any(abs(d.get("time", 0.0) - time_sec) <= window_sec for d in drops)


def _shot_video_path(
    shot: dict[str, Any],
    sources_by_id: dict[str, dict[str, Any]],
) -> Path | None:
    s = sources_by_id.get(shot["source_id"])
    if not s:
        return None
    return Path(s["path"])


def match_transitions(
    *,
    shot_list: dict[str, Any],
    source_catalog: dict[str, Any],
    beat_map: dict[str, Any] | None = None,
    config: TransitionConfig | None = None,
) -> dict[str, Any]:
    """Produce a schema-valid transition-plan.json dict."""
    cfg = config or TransitionConfig()
    validate(shot_list, "shot-list")
    validate(source_catalog, "source-catalog")
    if beat_map is not None:
        validate(beat_map, "beat-map")

    fps = int(shot_list["fps"])
    sources_by_id = {s["id"]: s for s in source_catalog["sources"]}
    drops = (beat_map or {}).get("drops", [])

    shots = shot_list["shots"]
    transitions: list[dict[str, Any]] = []

    for i in range(len(shots) - 1):
        a = shots[i]
        b = shots[i + 1]
        at_frame = int(a["start_frame"]) + int(a["duration_frames"])

        a_video = _shot_video_path(a, sources_by_id)
        b_video = _shot_video_path(b, sources_by_id)

        # Default to hard_cut; upgrade based on flow evidence.
        transition_type = "hard_cut"
        duration_frames = 0
        motion_dir: float | None = None
        requires_blur = False

        if a_video and b_video and a_video.exists() and b_video.exists():
            a_center = _tc_to_sec(a["source_timecode"]) + a["duration_frames"] / fps - cfg.frame_stride_sec
            b_center = _tc_to_sec(b["source_timecode"]) + cfg.frame_stride_sec
            a_frames = _sample_frames_to_arrays(a_video, a_center, cfg.sample_frames, cfg.frame_stride_sec)
            b_frames = _sample_frames_to_arrays(b_video, b_center, cfg.sample_frames, cfg.frame_stride_sec)
            if a_frames and b_frames:
                a_angle, a_mag = _estimate_motion(a_frames)
                b_angle, b_mag = _estimate_motion(b_frames)

                v1 = (math.cos(math.radians(a_angle)) * a_mag, math.sin(math.radians(a_angle)) * a_mag)
                v2 = (math.cos(math.radians(b_angle)) * b_mag, math.sin(math.radians(b_angle)) * b_mag)
                dot = _dot(v1, v2)

                # Match cut: motion direction aligns tightly (even if magnitudes differ).
                if dot >= cfg.match_cut_dot_min and a_mag > 1.0 and b_mag > 1.0:
                    transition_type = "match_cut"
                    motion_dir = (a_angle + b_angle) / 2.0
                # Whip pan: both sides have high motion in similar direction.
                elif (
                    a_mag >= cfg.whip_magnitude_threshold
                    and b_mag >= cfg.whip_magnitude_threshold
                    and _angle_diff(a_angle, b_angle) <= cfg.whip_angle_tolerance_deg
                ):
                    transition_type = "whip_pan"
                    duration_frames = 8
                    motion_dir = (a_angle + b_angle) / 2.0
                    requires_blur = True
                # Very high motion with opposing direction -> flash cut.
                elif a_mag + b_mag >= 2 * cfg.whip_magnitude_threshold and _angle_diff(a_angle, b_angle) > 90.0:
                    transition_type = "flash_cut"
                    duration_frames = cfg.flash_cut_duration_frames

        # Speed-ramp near audio drops (override cuts).
        if cfg.speed_ramp_on_drops and _near_drop(at_frame / fps, drops):
            transition_type = "speed_ramp"
            duration_frames = max(duration_frames, 12)

        entry: dict[str, Any] = {
            "from_shot_id": a["id"],
            "to_shot_id": b["id"],
            "type": transition_type,
            "at_frame": at_frame,
            "duration_frames": int(duration_frames),
            "requires_motion_blur": requires_blur,
        }
        if motion_dir is not None:
            entry["motion_direction"] = round(motion_dir, 2)
        if transition_type == "speed_ramp":
            entry["speed_ramp"] = {"from_rate": 1.0, "to_rate": 0.5, "ease": "ease_in_out"}
        elif transition_type == "flash_cut":
            entry["flash_color"] = "#FFFFFF"
        transitions.append(entry)

    out: dict[str, Any] = {
        "schema_version": 1,
        "project_slug": shot_list["project_slug"],
        "fps": fps,
        "transitions": transitions,
        "generated_at": _now_iso(),
        "generator": f"ff match transitions ({__version__})",
    }
    validate(out, "transition-plan")
    return out


def match_transitions_from_files(
    *,
    shot_list_path: Path,
    source_catalog_path: Path,
    beat_map_path: Path | None,
    output_path: Path,
    config: TransitionConfig | None = None,
) -> dict[str, Any]:
    shots = json.loads(shot_list_path.read_text(encoding="utf-8"))
    catalog = json.loads(source_catalog_path.read_text(encoding="utf-8"))
    beat = json.loads(beat_map_path.read_text(encoding="utf-8")) if beat_map_path else None
    plan = match_transitions(shot_list=shots, source_catalog=catalog, beat_map=beat, config=config)
    validate_and_write(plan, "transition-plan", output_path)
    return plan
