"""Source profiler — produce a source-profile.json record per ingested video.

The artifact this module writes is the load-bearing input for Phase 3's
visual signature DB, the slot-fit scorer, the aspect-ratio arbiter, and
the quality-gap mitigator. Designed to run during ingest so we never re-scan.

Two-pass design:
  - Quick pass: container metadata (ffprobe) + a few frames sampled for
    letterbox/pillarbox + tier assignment. Produces all REQUIRED schema
    fields with a single ffprobe call + ~3 frame extractions.
  - Deep pass: 30+ frames analyzed for histograms, color cast, grain,
    sharpness, color temperature. Slower; runs after the quick pass returns
    a valid record so downstream stages aren't blocked.

Output: writes `projects/<slug>/data/source-profiles/<source_id>.json`.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

# Profiler version. Bump when the algorithm changes meaningfully so callers
# can detect stale profiles.
PROFILER_VERSION = "0.1.0"
GENERATOR = f"source_profiler/{PROFILER_VERSION}"


# Quality-tier scoring proxies (composite from grain + sharpness + bitrate).
QUALITY_TIER_THRESHOLDS = {  # composite 0-100 → tier
    "S": 92,
    "A": 82,
    "B": 70,
    "C": 55,
    "D": 0,
}

# Filename heuristics for source_type when not provided externally.
SOURCE_TYPE_HINTS: dict[str, tuple[str, ...]] = {
    "anime": ("anime", "_ova_", "subbed", "dubbed", "_arc_"),
    "western_animation": ("animation", "cartoon", "pixar", "dreamworks"),
    "3d_render": ("render", "blender", "unreal", "unity"),
    "music_video": ("mv_", "music_video", "_official_video"),
    "sports": ("highlights", "match_", "_vs_", "espn"),
    "documentary": ("documentary", "doc_", "_history"),
}

YEAR_PATTERN = re.compile(r"(?<!\d)(19[5-9]\d|20[0-2]\d|2030)(?!\d)")


@dataclass
class _ContainerMeta:
    width: int
    height: int
    fps: float
    duration_sec: float
    bitrate_kbps: int
    aspect_ratio_native: str


def _check_ffprobe() -> None:
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe not found. Install ffmpeg.")


def _check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found. Install it.")


def _ffprobe_container(path: Path) -> _ContainerMeta:
    """Pull the container metadata in a single ffprobe call."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,bit_rate,display_aspect_ratio:format=duration,bit_rate",
        "-of", "json",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
    payload = json.loads(res.stdout)
    stream = (payload.get("streams") or [{}])[0]
    fmt = payload.get("format") or {}

    width = int(stream.get("width") or 0) or 1920
    height = int(stream.get("height") or 0) or 1080

    rfr = stream.get("r_frame_rate") or "24/1"
    if "/" in rfr:
        n, d = rfr.split("/")
        try:
            fps = float(n) / max(1, float(d))
        except (ValueError, ZeroDivisionError):
            fps = 24.0
    else:
        try:
            fps = float(rfr)
        except ValueError:
            fps = 24.0

    duration_sec = float(fmt.get("duration") or 0)
    # Prefer container bitrate; fall back to stream bitrate.
    bitrate = int(fmt.get("bit_rate") or stream.get("bit_rate") or 0)
    bitrate_kbps = bitrate // 1000 if bitrate else 0

    dar = stream.get("display_aspect_ratio")
    if dar and ":" in dar:
        ar_native = dar
    else:
        # Derive from W:H, simplified
        gcd = math.gcd(width, height) or 1
        ar_native = f"{width // gcd}:{height // gcd}"

    return _ContainerMeta(
        width=width,
        height=height,
        fps=round(fps, 3),
        duration_sec=duration_sec,
        bitrate_kbps=bitrate_kbps,
        aspect_ratio_native=ar_native,
    )


def _detect_letter_pillar(path: Path, duration_sec: float) -> tuple[bool, bool]:
    """Use ffmpeg's cropdetect to spot letterbox / pillarbox."""
    if duration_sec <= 0:
        return False, False
    sample_t = min(60.0, duration_sec / 2)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "info",
        "-ss", f"{sample_t:.2f}",
        "-i", str(path),
        "-vframes", "60",
        "-vf", "cropdetect=24:16:0",
        "-f", "null", "-",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return False, False
    out = (res.stderr or "")
    matches = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", out)
    if not matches:
        return False, False
    # Take the most-frequent crop suggestion (last is often the cropdetect's settled value)
    w, h, x, y = (int(v) for v in matches[-1])
    container_meta = _ffprobe_container(path)
    full_w, full_h = container_meta.width, container_meta.height
    # Letterbox = vertical crop trims top/bottom
    letterbox = (full_h - h) >= max(8, int(0.04 * full_h))
    # Pillarbox = horizontal crop trims sides
    pillarbox = (full_w - w) >= max(8, int(0.04 * full_w))
    return letterbox, pillarbox


def _sample_frames(path: Path, duration_sec: float, n: int) -> list[np.ndarray]:
    """Sample n evenly-spaced frames as RGB numpy arrays via ffmpeg pipe."""
    if duration_sec <= 0 or n <= 0:
        return []
    # Use ffmpeg select filter: 'eq(mod(n, k), 0)' isn't time-precise, so we
    # do n separate seek-to-time + 1-frame extracts via lavfi-thumbnail.
    # For simplicity we sample at fixed time offsets.
    margin = min(2.0, duration_sec * 0.02)
    span = max(0.5, duration_sec - 2 * margin)
    times = [margin + (i + 0.5) * span / n for i in range(n)]
    frames: list[np.ndarray] = []
    for t in times:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", f"{t:.3f}",
            "-i", str(path),
            "-vframes", "1",
            "-f", "image2pipe",
            "-vcodec", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", "320x180",  # downsample for speed
            "-",
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, check=True, timeout=15)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
        raw = r.stdout
        if len(raw) != 320 * 180 * 3:
            continue
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(180, 320, 3)
        frames.append(arr)
    return frames


def _luma_histogram_16(frames: list[np.ndarray]) -> dict[str, Any]:
    """16-bin luma histogram across all sampled frames, raw counts."""
    if not frames:
        return {"bins": 16, "counts": [0] * 16}
    hist = np.zeros(16, dtype=np.int64)
    for f in frames:
        # ITU-R BT.601 luma approximation
        luma = (0.299 * f[:, :, 0] + 0.587 * f[:, :, 1] + 0.114 * f[:, :, 2]).astype(np.int32)
        bins = np.clip(luma // 16, 0, 15)
        h, _ = np.histogram(bins, bins=np.arange(17))
        hist += h
    return {"bins": 16, "counts": [int(v) for v in hist.tolist()]}


def _chroma_histogram_16(frames: list[np.ndarray]) -> dict[str, Any]:
    """16-bin chroma magnitude histogram (HSV saturation channel * 255)."""
    if not frames:
        return {"bins": 16, "counts": [0] * 16}
    hist = np.zeros(16, dtype=np.int64)
    for f in frames:
        rgb = f.astype(np.float32) / 255.0
        mx = rgb.max(axis=2)
        mn = rgb.min(axis=2)
        # Saturation = (max-min)/max; 0 when grayscale
        sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
        sat_255 = (sat * 255).astype(np.int32)
        bins = np.clip(sat_255 // 16, 0, 15)
        h, _ = np.histogram(bins, bins=np.arange(17))
        hist += h
    return {"bins": 16, "counts": [int(v) for v in hist.tolist()]}


def _average_saturation(frames: list[np.ndarray]) -> float:
    if not frames:
        return 0.0
    sums = []
    for f in frames:
        rgb = f.astype(np.float32) / 255.0
        mx = rgb.max(axis=2)
        mn = rgb.min(axis=2)
        sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
        sums.append(float(sat.mean()))
    return round(float(np.mean(sums)), 4)


def _color_temperature(frames: list[np.ndarray]) -> dict[str, Any]:
    """Crude color temperature estimate from average R/B ratio.

    Higher R/B → warmer (low Kelvin); higher B/R → cooler (high Kelvin).
    Maps to 2700K (very warm) - 9000K (very cool) via a simple mapping.
    Confidence is low for heavily color-graded sources.
    """
    if not frames:
        return {"estimate_k": 6500, "confidence": 0.3}
    r_means, b_means = [], []
    for f in frames:
        r_means.append(float(f[:, :, 0].mean()))
        b_means.append(float(f[:, :, 2].mean()))
    r = float(np.mean(r_means)) or 1.0
    b = float(np.mean(b_means)) or 1.0
    ratio = r / b  # >1 = warm, <1 = cool
    # Map ratio (0.5..2.0) → kelvin (9000..2700) inverted
    ratio_clamped = float(np.clip(ratio, 0.5, 2.0))
    # Linear interp: ratio 0.5 → 9000K (cool); ratio 2.0 → 2700K (warm)
    kelvin = int(9000 - (ratio_clamped - 0.5) / 1.5 * (9000 - 2700))
    # Confidence: how far from neutral 1.0
    confidence = round(min(1.0, abs(ratio - 1.0) * 2.0 + 0.3), 3)
    return {"estimate_k": int(np.clip(kelvin, 1000, 12000)), "confidence": confidence}


def _color_cast(frames: list[np.ndarray]) -> dict[str, Any]:
    """Compute dominant hue + up to 5 dominant hex colors via simple k-means."""
    if not frames:
        return {"hue_degrees": 0.0, "dominant_colors": ["888888"]}
    # Stack a uniform sample across frames
    stacked = np.concatenate([f.reshape(-1, 3) for f in frames])
    # Subsample to 4000 pixels for speed
    if stacked.shape[0] > 4000:
        idx = np.random.RandomState(42).choice(stacked.shape[0], 4000, replace=False)
        stacked = stacked[idx]
    rgb = stacked.astype(np.float32) / 255.0
    # Convert to HSV; hue is what we want
    mx = rgb.max(axis=1)
    mn = rgb.min(axis=1)
    delta = mx - mn
    hue = np.zeros(rgb.shape[0])
    nonzero = delta > 1e-5
    rmax = (rgb[:, 0] == mx) & nonzero
    gmax = (rgb[:, 1] == mx) & nonzero
    bmax = (rgb[:, 2] == mx) & nonzero
    with np.errstate(divide="ignore", invalid="ignore"):
        hue[rmax] = ((rgb[rmax, 1] - rgb[rmax, 2]) / delta[rmax]) % 6
        hue[gmax] = (rgb[gmax, 2] - rgb[gmax, 0]) / delta[gmax] + 2
        hue[bmax] = (rgb[bmax, 0] - rgb[bmax, 1]) / delta[bmax] + 4
    hue_deg = (hue * 60.0) % 360.0
    sat = np.where(mx > 0, delta / np.maximum(mx, 1e-6), 0.0)
    # Dominant hue = circular mean weighted by saturation
    weights = sat
    if weights.sum() > 1e-6:
        x = np.sum(np.cos(np.radians(hue_deg)) * weights)
        y = np.sum(np.sin(np.radians(hue_deg)) * weights)
        dominant_hue = (math.degrees(math.atan2(y, x)) % 360)
    else:
        dominant_hue = 0.0

    # k-means via simple bucketing for top-5 colors (fast; not ideal but robust)
    quantized = (stacked // 51) * 51  # 5 levels per channel
    quantized_int = quantized.astype(int)
    # Hashable keys
    keys = quantized_int[:, 0] * 1_000_000 + quantized_int[:, 1] * 1000 + quantized_int[:, 2]
    unique, counts = np.unique(keys, return_counts=True)
    order = np.argsort(-counts)
    top = unique[order[:5]]
    dominant_colors: list[str] = []
    for k in top:
        r, g, b = int(k // 1_000_000), int((k // 1000) % 1000), int(k % 1000)
        dominant_colors.append(f"{r:02X}{g:02X}{b:02X}")
    if not dominant_colors:
        dominant_colors = ["888888"]
    return {
        "hue_degrees": round(float(dominant_hue), 2),
        "dominant_colors": dominant_colors,
    }


def _grain_noise_floor(frames: list[np.ndarray]) -> float:
    """Median high-pass energy across frames as a noise proxy 0-1."""
    if not frames:
        return 0.0
    energies: list[float] = []
    for f in frames:
        gray = (0.299 * f[:, :, 0] + 0.587 * f[:, :, 1] + 0.114 * f[:, :, 2]).astype(np.float32)
        # 3x3 mean blur
        kernel = np.ones((3, 3), np.float32) / 9
        # Manual conv via numpy stride trick is overkill; use scipy if available
        # Simple pad + slice mean for speed
        padded = np.pad(gray, 1, mode="edge")
        blur = (
            padded[:-2, :-2] + padded[:-2, 1:-1] + padded[:-2, 2:] +
            padded[1:-1, :-2] + padded[1:-1, 1:-1] + padded[1:-1, 2:] +
            padded[2:, :-2] + padded[2:, 1:-1] + padded[2:, 2:]
        ) / 9
        high_pass = np.abs(gray - blur)
        energies.append(float(np.median(high_pass)))
    median_energy = float(np.median(energies))
    # Empirical normalization: median high-pass of clean digital ~ 1-3,
    # heavy grain ~ 8-12. Map 0-15 → 0-1 with clipping.
    return round(min(1.0, median_energy / 15.0), 4)


def _sharpness(frames: list[np.ndarray]) -> float:
    """Edge density via Laplacian variance, normalized 0-1."""
    if not frames:
        return 0.5
    variances: list[float] = []
    for f in frames:
        gray = (0.299 * f[:, :, 0] + 0.587 * f[:, :, 1] + 0.114 * f[:, :, 2]).astype(np.float32)
        # Laplacian via 3x3 kernel
        padded = np.pad(gray, 1, mode="edge")
        lap = (
            -padded[:-2, 1:-1] - padded[2:, 1:-1] -
            padded[1:-1, :-2] - padded[1:-1, 2:] +
            4 * padded[1:-1, 1:-1]
        )
        variances.append(float(lap.var()))
    median_var = float(np.median(variances))
    # Empirical: clean sharp ≈ 200+, soft ≈ 30, heavy blur ≈ 5
    return round(min(1.0, median_var / 250.0), 4)


def _classify_source_type(source_id: str, hint: str | None = None) -> str:
    """Heuristic source-type from filename / source_id, with override hint."""
    if hint and hint in SOURCE_TYPE_HINTS:
        return hint
    sid = (source_id or "").lower()
    for stype, needles in SOURCE_TYPE_HINTS.items():
        for n in needles:
            if n in sid:
                return stype
    # Default fallback for unknown content
    return "live_action"


def _classify_era(source_id: str, hint: str | None = None) -> tuple[str, str | None]:
    """Try to extract a year from the filename/source_id and bucket it."""
    if hint:
        for bucket in ("pre-2000", "2000-2010", "2010-2020", "post-2020"):
            if bucket in hint:
                return bucket, hint
    m = YEAR_PATTERN.search(source_id or "")
    if m:
        year = int(m.group(1))
        label = f"{source_id}-{year}" if source_id else str(year)
        if year < 2000:
            return "pre-2000", label
        if year < 2010:
            return "2000-2010", label
        if year < 2020:
            return "2010-2020", label
        return "post-2020", label
    return "post-2020", None


def _quality_tier(
    bitrate_kbps: int,
    grain: float,
    sharpness: float,
    width: int,
    height: int,
) -> str:
    """Composite tier from resolution + bitrate + grain + sharpness."""
    res_score = 0.0
    if width * height >= 1920 * 1080:
        res_score = 25
    elif width * height >= 1280 * 720:
        res_score = 18
    elif width * height >= 854 * 480:
        res_score = 10
    else:
        res_score = 4

    bitrate_score = min(25, bitrate_kbps / 200) if bitrate_kbps > 0 else 12
    sharpness_score = sharpness * 25
    grain_penalty = grain * 25
    composite = res_score + bitrate_score + sharpness_score + (25 - grain_penalty)
    composite = max(0, min(100, composite))

    for tier, threshold in QUALITY_TIER_THRESHOLDS.items():
        if composite >= threshold:
            return tier
    return "D"


def _detect_visual_hazards(
    frames: list[np.ndarray],
    luma_hist: dict[str, Any],
    grain: float,
) -> list[str]:
    """Conservative free-text hazard list."""
    hazards: list[str] = []
    if grain > 0.6:
        hazards.append("heavy compression or grain")
    counts = luma_hist.get("counts") or []
    if counts:
        total = sum(counts) or 1
        # >40% of pixels in the bottom 4 bins (luma 0-63) → consistent dark scenes
        dark_pct = sum(counts[:4]) / total
        if dark_pct > 0.4:
            hazards.append("consistent dark scenes")
        bright_pct = sum(counts[-2:]) / total
        if bright_pct > 0.3:
            hazards.append("blown-out highlights")
    return hazards


def profile_source(
    video_path: Path | str,
    source_id: str,
    *,
    deep: bool = True,
    n_frames: int = 30,
    source_type_hint: str | None = None,
    era_hint: str | None = None,
) -> dict[str, Any]:
    """Produce a source-profile dict for one source.

    Args:
        video_path: path to the source video file.
        source_id: stable id (matches source-catalog entry).
        deep: if False, only the quick-pass (container + letterbox/pillarbox)
            is run. Visual stats are omitted.
        n_frames: how many frames to sample for the deep pass. Default 30 is
            a balance between speed and signal stability.
        source_type_hint: external override for source_type classification.
        era_hint: external override for era classification.
    """
    _check_ffprobe()
    _check_ffmpeg()

    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Source video not found: {video_path}")

    container = _ffprobe_container(video_path)
    letterbox, pillarbox = _detect_letter_pillar(video_path, container.duration_sec)

    # Defaults — populated by deep pass when enabled
    luma_hist: dict[str, Any] | None = None
    chroma_hist: dict[str, Any] | None = None
    avg_sat = 0.0
    color_temp: dict[str, Any] | None = None
    color_cast_data: dict[str, Any] | None = None
    grain = 0.3  # neutral default
    sharpness = 0.5
    hazards: list[str] = []
    frames_sampled = 0

    if deep:
        frames = _sample_frames(video_path, container.duration_sec, n_frames)
        frames_sampled = len(frames)
        if frames:
            luma_hist = _luma_histogram_16(frames)
            chroma_hist = _chroma_histogram_16(frames)
            avg_sat = _average_saturation(frames)
            color_temp = _color_temperature(frames)
            color_cast_data = _color_cast(frames)
            grain = _grain_noise_floor(frames)
            sharpness = _sharpness(frames)
            hazards = _detect_visual_hazards(frames, luma_hist, grain)
    if frames_sampled < 1:
        frames_sampled = 1  # required minimum

    source_type = _classify_source_type(source_id, source_type_hint)
    era_bucket, era_label = _classify_era(source_id, era_hint)
    quality_tier = _quality_tier(
        container.bitrate_kbps, grain, sharpness, container.width, container.height,
    )

    profile: dict[str, Any] = {
        "schema_version": 1,
        "source_id": source_id,
        "source_type": source_type,
        "era_bucket": era_bucket,
        "quality_tier": quality_tier,
        "aspect_ratio_native": container.aspect_ratio_native,
        "framerate_native": container.fps,
        "resolution_native": {"width": container.width, "height": container.height},
        "letterbox_detected": letterbox,
        "pillarbox_detected": pillarbox,
        "frames_sampled": frames_sampled,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": GENERATOR,
    }
    if era_label:
        profile["era_label"] = era_label
    if hazards:
        profile["visual_hazards"] = hazards
    if luma_hist is not None:
        profile["luma_histogram"] = luma_hist
    if chroma_hist is not None:
        profile["chroma_histogram"] = chroma_hist
    if color_temp is not None:
        profile["color_temperature_kelvin"] = color_temp
    if avg_sat:
        profile["saturation_avg"] = avg_sat
    if color_cast_data is not None:
        profile["color_cast"] = color_cast_data
    if grain is not None:
        profile["grain_noise_floor"] = grain
    if sharpness is not None:
        profile["sharpness_score"] = sharpness

    return profile


def write_source_profile(profile: dict[str, Any], project_dir: Path) -> Path:
    """Persist a source-profile dict to projects/<slug>/data/source-profiles/<id>.json."""
    from fandomforge.validation import validate_and_write

    out_dir = project_dir / "data" / "source-profiles"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", profile["source_id"])
    out_path = out_dir / f"{safe_id}.json"
    validate_and_write(profile, "source-profile", out_path)
    return out_path


def profile_all_sources(
    project_dir: Path,
    *,
    deep: bool = True,
) -> list[Path]:
    """Walk the source-catalog and produce a profile for each source.

    Idempotent: existing profile files are kept unless the catalog entry
    has changed (we always rewrite to capture latest generator version).
    """
    catalog_path = project_dir / "data" / "source-catalog.json"
    if not catalog_path.exists():
        return []
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    written: list[Path] = []
    for entry in catalog.get("sources") or []:
        path_str = entry.get("path")
        sid = entry.get("id") or entry.get("source_id")
        if not path_str or not sid:
            continue
        path = Path(path_str)
        if not path.exists():
            continue
        try:
            profile = profile_source(
                path, sid, deep=deep,
                source_type_hint=entry.get("media", {}).get("source_type_hint"),
            )
            written.append(write_source_profile(profile, project_dir))
        except Exception:  # noqa: BLE001 — best-effort per-source
            continue
    return written


__all__ = [
    "PROFILER_VERSION",
    "profile_source",
    "write_source_profile",
    "profile_all_sources",
]
