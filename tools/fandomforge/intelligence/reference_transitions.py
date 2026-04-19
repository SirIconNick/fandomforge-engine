"""Transition classifier for reference videos.

At every adjacent shot boundary, samples the last frame of the outgoing shot
and the first frame of the incoming shot, then buckets the transition into
hard_cut / dissolve / flash_cut / whip_pan / speed_ramp based on frame
similarity, brightness deltas, and motion signature.

Pure heuristics, no ML. Falls back gracefully when ffmpeg or PIL/numpy are
unavailable — returns an empty distribution rather than crashing.

Why this matters: great fandom editors don't hard-cut everything. Variety in
transition choice is one of the strongest signals of editorial craft, and
Shannon entropy of the distribution gives a scalar to compare corpora.
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


# Canonical buckets — keep sorted so the schema enum stays deterministic.
TRANSITION_KINDS = (
    "hard_cut",
    "dissolve",
    "flash_cut",
    "whip_pan",
    "speed_ramp",
)


def _sample_frame(video: Path, time_sec: float, out_path: Path, width: int = 160) -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-ss", f"{time_sec:.3f}",
             "-i", str(video),
             "-frames:v", "1",
             "-vf", f"scale={width}:-1",
             str(out_path)],
            check=True, capture_output=True, timeout=15,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return out_path.exists() and out_path.stat().st_size > 0


def _hist_correlation(a: "Any", b: "Any") -> float:
    """Histogram correlation between two RGB arrays, 0..1."""
    import numpy as np  # type: ignore
    # 16 bins per channel
    bins = 16
    def _h(arr: Any) -> Any:
        r = np.histogram(arr[..., 0], bins=bins, range=(0, 1))[0]
        g = np.histogram(arr[..., 1], bins=bins, range=(0, 1))[0]
        b_ = np.histogram(arr[..., 2], bins=bins, range=(0, 1))[0]
        h = np.concatenate([r, g, b_]).astype("float32")
        total = h.sum()
        return h / total if total > 0 else h
    ha, hb = _h(a), _h(b)
    # Pearson correlation
    ha_m = ha - ha.mean()
    hb_m = hb - hb.mean()
    denom = (np.sqrt((ha_m ** 2).sum()) * np.sqrt((hb_m ** 2).sum()))
    if denom == 0:
        return 0.0
    return float(max(0.0, min(1.0, (ha_m * hb_m).sum() / denom)))


def _horizontal_blur_score(arr: "Any") -> float:
    """Proxy for horizontal motion blur — vertical variance / horizontal variance.

    High value → image is smeared horizontally (typical whip-pan signature).
    """
    import numpy as np  # type: ignore
    gray = arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114
    vx = gray.var(axis=1).mean()  # variance along rows → vertical detail
    hx = gray.var(axis=0).mean()  # variance along cols → horizontal detail
    if hx == 0:
        return 0.0
    return float(vx / hx)


def _classify_pair(
    out_frame: "Any",
    in_frame: "Any",
    out_luma: float,
    in_luma: float,
    gap_sec: float,
) -> str:
    """Classify a single cut pair given both frames + timing context.

    Ordering matters — check the distinctive signatures first (flash, whip,
    ramp), then fall back to the similarity-based dissolve vs hard_cut split.
    """
    # flash_cut: one side of the cut is a near-white or near-black frame
    # (typical of flash-bulb transitions)
    if out_luma > 0.85 or in_luma > 0.85:
        return "flash_cut"
    if out_luma < 0.05 and in_luma < 0.05:
        # Both sides dark — more likely a blackout dip than a flash
        return "dissolve"

    # whip_pan: incoming frame is horizontally smeared
    blur_in = _horizontal_blur_score(in_frame)
    blur_out = _horizontal_blur_score(out_frame)
    if blur_in > 3.0 or blur_out > 3.0:
        return "whip_pan"

    # dissolve: high histogram correlation across the boundary (content
    # bleeds through) and the cut boundary is >100ms of visible blend
    sim = _hist_correlation(out_frame, in_frame)
    if sim > 0.82:
        return "dissolve"

    # speed_ramp: outgoing motion is much larger than incoming — proxy for
    # time-stretch (slow-mo landing on real time). Use horizontal blur
    # differential as a coarse signal since we don't have true optical flow
    # here.
    if blur_out > 0 and blur_in > 0 and blur_out / max(blur_in, 1e-6) > 2.5:
        return "speed_ramp"

    return "hard_cut"


def classify_transitions(
    video: Path,
    boundaries: list[tuple[float, float]],
    *,
    max_samples: int = 60,
) -> dict[str, Any]:
    """Walk shot boundaries and classify each transition.

    Returns a dict with `sample_count`, `distribution` (counts per kind),
    `distribution_pct` (normalized), and `variety_entropy` (0-ln(5) range,
    higher = more varied).
    """
    try:
        import numpy as np  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError:
        return {"sample_count": 0}

    if shutil.which("ffmpeg") is None or len(boundaries) < 2:
        return {"sample_count": 0}

    # Sample up to max_samples cut points evenly spaced through the video.
    cut_indices = list(range(1, len(boundaries)))  # boundary N means cut from N-1 → N
    if len(cut_indices) > max_samples:
        step = len(cut_indices) / max_samples
        cut_indices = [cut_indices[int(i * step)] for i in range(max_samples)]

    counts: Counter[str] = Counter()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        for i in cut_indices:
            out_sec = max(0.0, boundaries[i - 1][1] - 0.08)  # just before cut
            in_sec = boundaries[i][0] + 0.02                  # just after cut

            out_path = tmp_p / f"out{i:04d}.jpg"
            in_path = tmp_p / f"in{i:04d}.jpg"
            if not _sample_frame(video, out_sec, out_path):
                continue
            if not _sample_frame(video, in_sec, in_path):
                continue
            try:
                out_arr = np.asarray(Image.open(out_path).convert("RGB")).astype("float32") / 255.0
                in_arr = np.asarray(Image.open(in_path).convert("RGB")).astype("float32") / 255.0
            except Exception:  # noqa: BLE001
                continue

            out_luma = float(
                (out_arr[..., 0] * 0.299 + out_arr[..., 1] * 0.587 + out_arr[..., 2] * 0.114).mean()
            )
            in_luma = float(
                (in_arr[..., 0] * 0.299 + in_arr[..., 1] * 0.587 + in_arr[..., 2] * 0.114).mean()
            )
            gap = boundaries[i][0] - boundaries[i - 1][1]

            kind = _classify_pair(out_arr, in_arr, out_luma, in_luma, gap)
            counts[kind] += 1

    total = sum(counts.values())
    if total == 0:
        return {"sample_count": 0}

    dist = {k: counts.get(k, 0) for k in TRANSITION_KINDS}
    dist_pct = {k: round(dist[k] / total * 100.0, 2) for k in TRANSITION_KINDS}

    # Shannon entropy, natural log. Max = ln(len(TRANSITION_KINDS)).
    entropy = 0.0
    for k in TRANSITION_KINDS:
        p = dist[k] / total
        if p > 0:
            entropy -= p * math.log(p)
    max_entropy = math.log(len(TRANSITION_KINDS))
    entropy_normalized = entropy / max_entropy if max_entropy > 0 else 0.0

    return {
        "sample_count": total,
        "distribution": dist,
        "distribution_pct": dist_pct,
        "variety_entropy": round(entropy, 4),
        "variety_entropy_normalized": round(entropy_normalized, 4),
        "dominant_kind": max(dist, key=lambda k: dist[k]),
    }


__all__ = ["TRANSITION_KINDS", "classify_transitions"]
