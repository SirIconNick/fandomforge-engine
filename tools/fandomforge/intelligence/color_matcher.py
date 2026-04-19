"""Color matcher — derive a per-source color plan from a hero reference frame.

Strategy (cheap and reproducible, no ML):

1. Pick a hero frame — highest theme-fit shot in Act 2 (or config-overridden).
2. For each source, extract a representative frame (shot-listed timecode).
3. Compute LAB-space mean + stddev for the hero and each source frame.
4. Emit a DaVinci-style node: exposure stops, temperature, tint, saturation,
   per-channel gain. Parameters bounded to musical ranges so any NLE can apply.

Also emits an optional `.cube` 17x17x17 LUT per source (identity LUT biased by
the target vs source RGB offsets) for editors who want a drop-in preset
without touching nodes.
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

__all__ = ["ColorMatchConfig", "match_color", "match_color_from_files"]


@dataclass
class ColorMatchConfig:
    """Tunables for the auto color plan."""

    target_color_space: str = "Rec.709"
    global_lut: str | None = None
    global_lut_intensity: float = 0.5
    lut_size: int = 17  # produce 17x17x17 .cube LUTs per source
    # Clamp the auto-exposure / temperature / tint to safe ranges so we don't
    # generate crazy values on outlier shots.
    max_exposure_stops: float = 1.2
    max_temp_shift_k: float = 1200.0
    max_tint_shift: float = 12.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tc_to_sec(tc: str) -> float:
    h, m, s = tc.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _grab_frame_rgb(video: Path, time_sec: float, width: int = 160) -> Any | None:
    """Grab a single frame as an (H, W, 3) uint8 RGB numpy array."""
    if shutil.which("ffmpeg") is None:
        return None
    try:
        import numpy as np  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError:
        return None

    tmp = Path(tempfile.mkdtemp(prefix="ff_color_"))
    try:
        out = tmp / "f.jpg"
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-nostats",
            "-ss", f"{time_sec:.3f}",
            "-i", str(video),
            "-frames:v", "1",
            "-vf", f"scale={width}:-2",
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
                timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
        if not out.exists():
            return None
        return np.asarray(Image.open(out).convert("RGB"), dtype=np.uint8)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _rgb_to_lab_stats(img: Any) -> dict[str, float] | None:
    """Return LAB mean + stddev + per-channel RGB mean."""
    try:
        import numpy as np  # type: ignore
    except ImportError:
        return None
    rgb = img.astype(np.float32) / 255.0
    # Convert sRGB -> linear.
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    # Linear RGB -> XYZ (D65).
    M = np.array([
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ], dtype=np.float32)
    xyz = linear.reshape(-1, 3) @ M.T
    # Normalize by D65 white.
    xn, yn, zn = 0.95047, 1.0, 1.08883
    xyz[:, 0] /= xn
    xyz[:, 1] /= yn
    xyz[:, 2] /= zn
    eps = 0.008856
    kappa = 903.3
    f_xyz = np.where(xyz > eps, np.cbrt(xyz), (kappa * xyz + 16.0) / 116.0)
    l = 116.0 * f_xyz[:, 1] - 16.0
    a = 500.0 * (f_xyz[:, 0] - f_xyz[:, 1])
    b = 200.0 * (f_xyz[:, 1] - f_xyz[:, 2])

    return {
        "L_mean": float(np.mean(l)),
        "L_std": float(np.std(l) + 1e-6),
        "a_mean": float(np.mean(a)),
        "a_std": float(np.std(a) + 1e-6),
        "b_mean": float(np.mean(b)),
        "b_std": float(np.std(b) + 1e-6),
        "R_mean": float(np.mean(linear[..., 0])),
        "G_mean": float(np.mean(linear[..., 1])),
        "B_mean": float(np.mean(linear[..., 2])),
    }


def _derive_node(
    hero_stats: dict[str, float],
    src_stats: dict[str, float],
    cfg: ColorMatchConfig,
) -> dict[str, Any]:
    """Produce a color-plan node that nudges a source toward the hero stats.

    Returns fields compatible with color-plan.schema.json `ColorNode`.
    """
    # Exposure: L delta mapped to stops.
    L_delta = (hero_stats["L_mean"] - src_stats["L_mean"]) / 50.0
    exposure = max(-cfg.max_exposure_stops, min(cfg.max_exposure_stops, L_delta))

    # Contrast: ratio of stddevs, clamped [0.7, 1.3].
    contrast = hero_stats["L_std"] / src_stats["L_std"]
    contrast = max(0.7, min(1.3, contrast))

    # Saturation: chroma magnitude ratio.
    hero_chroma = math.hypot(hero_stats["a_std"], hero_stats["b_std"])
    src_chroma = math.hypot(src_stats["a_std"], src_stats["b_std"]) + 1e-6
    saturation = max(0.4, min(1.6, hero_chroma / src_chroma))

    # Temperature: b_mean controls blue-yellow. Positive b = warmer (toward
    # yellow). We aim to match hero's b_mean, translated to a K offset.
    b_delta = hero_stats["b_mean"] - src_stats["b_mean"]
    temp_shift = max(-cfg.max_temp_shift_k, min(cfg.max_temp_shift_k, b_delta * 120.0))
    temperature_k = 6500.0 + temp_shift

    # Tint: a_mean is red-green axis.
    a_delta = hero_stats["a_mean"] - src_stats["a_mean"]
    tint = max(-cfg.max_tint_shift, min(cfg.max_tint_shift, a_delta * 0.4))

    # Gain (lift/gamma/gain all take y/r/g/b). We'll only emit gain.y from the
    # exposure stop and skip lift/gamma to keep the node minimal and safe.
    return {
        "label": "auto_match_to_hero",
        "exposure_stops": round(exposure, 3),
        "contrast": round(contrast, 3),
        "saturation": round(saturation, 3),
        "temperature_k": round(temperature_k, 1),
        "tint": round(tint, 2),
    }


def _write_cube_lut(
    hero_stats: dict[str, float],
    src_stats: dict[str, float],
    path: Path,
    size: int = 17,
) -> None:
    """Emit a simple .cube LUT that shifts source RGB means toward hero means.

    Not a full color grade. Use as a starter in NLEs that want a drop-in LUT
    preset; combine with the per-source node tree for finer control.
    """
    try:
        import numpy as np  # type: ignore
    except ImportError:
        return
    path.parent.mkdir(parents=True, exist_ok=True)

    hr, hg, hb = hero_stats["R_mean"], hero_stats["G_mean"], hero_stats["B_mean"]
    sr, sg, sb = src_stats["R_mean"], src_stats["G_mean"], src_stats["B_mean"]
    dr, dg, db = (hr - sr), (hg - sg), (hb - sb)
    # Clamp to sensible offsets.
    dr = max(-0.08, min(0.08, dr))
    dg = max(-0.08, min(0.08, dg))
    db = max(-0.08, min(0.08, db))

    lines: list[str] = []
    lines.append("# FandomForge auto color LUT")
    lines.append("TITLE \"FandomForge Auto Match\"")
    lines.append(f"LUT_3D_SIZE {size}")
    lines.append("DOMAIN_MIN 0.0 0.0 0.0")
    lines.append("DOMAIN_MAX 1.0 1.0 1.0")

    for b in range(size):
        for g in range(size):
            for r in range(size):
                fr = r / (size - 1)
                fg = g / (size - 1)
                fb = b / (size - 1)
                nr = max(0.0, min(1.0, fr + dr))
                ng = max(0.0, min(1.0, fg + dg))
                nb = max(0.0, min(1.0, fb + db))
                lines.append(f"{nr:.6f} {ng:.6f} {nb:.6f}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pick_hero_shot(shot_list: dict[str, Any]) -> dict[str, Any] | None:
    """Prefer the highest theme_fit shot in Act 2; fall back to the first shot."""
    shots = shot_list["shots"]
    if not shots:
        return None
    act2 = [s for s in shots if s.get("act") == 2]
    pool = act2 if act2 else shots
    pool_sorted = sorted(
        pool,
        key=lambda s: s.get("scores", {}).get("theme_fit", 0),
        reverse=True,
    )
    return pool_sorted[0]


def match_color(
    *,
    shot_list: dict[str, Any],
    source_catalog: dict[str, Any],
    output_dir: Path,
    config: ColorMatchConfig | None = None,
    hero_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive a color-plan.json dict from a shot-list + catalog. Writes any
    LUT .cube files to `output_dir/luts/`. The returned dict is schema-valid."""
    cfg = config or ColorMatchConfig()
    validate(shot_list, "shot-list")
    validate(source_catalog, "source-catalog")

    sources_by_id = {s["id"]: s for s in source_catalog["sources"]}

    hero_shot = hero_override or _pick_hero_shot(shot_list)
    if hero_shot is None:
        raise ValueError("shot-list has no shots; cannot pick a hero frame")
    hero_source = sources_by_id.get(hero_shot["source_id"])
    if hero_source is None:
        raise ValueError(f"hero shot references missing source {hero_shot['source_id']}")

    hero_sec = _tc_to_sec(hero_shot["source_timecode"])
    hero_img = _grab_frame_rgb(Path(hero_source["path"]), hero_sec)
    if hero_img is None:
        raise RuntimeError(
            "Could not extract hero reference frame. ffmpeg + numpy + Pillow required."
        )
    hero_stats = _rgb_to_lab_stats(hero_img)
    if hero_stats is None:
        raise RuntimeError("numpy required for color analysis")

    luts_dir = output_dir / "luts"
    hero_img_path = output_dir / "references" / f"hero_{hero_shot['id']}.jpg"
    hero_img_path.parent.mkdir(parents=True, exist_ok=True)
    # Save hero frame for reference / dashboard preview.
    try:
        from PIL import Image  # type: ignore
        Image.fromarray(hero_img).save(hero_img_path, quality=90)
    except Exception:
        hero_img_path = None  # type: ignore

    per_source: dict[str, dict[str, Any]] = {}
    # Use each source's first-used shot timecode for its representative frame.
    first_tc_per_source: dict[str, str] = {}
    for shot in shot_list["shots"]:
        sid = shot["source_id"]
        first_tc_per_source.setdefault(sid, shot["source_timecode"])

    for source_id, tc in first_tc_per_source.items():
        src = sources_by_id.get(source_id)
        if not src:
            continue
        src_img = _grab_frame_rgb(Path(src["path"]), _tc_to_sec(tc))
        if src_img is None:
            logger.warning("Could not extract reference for source %s", source_id)
            continue
        src_stats = _rgb_to_lab_stats(src_img)
        if src_stats is None:
            continue
        node = _derive_node(hero_stats, src_stats, cfg)
        lut_path = luts_dir / f"{source_id}.cube"
        _write_cube_lut(hero_stats, src_stats, lut_path, size=cfg.lut_size)

        per_source[source_id] = {
            "lut": str(lut_path),
            "lut_intensity": 0.6,
            "nodes": [node],
        }

    out: dict[str, Any] = {
        "schema_version": 1,
        "project_slug": shot_list["project_slug"],
        "target_color_space": cfg.target_color_space,
        "global_lut_intensity": cfg.global_lut_intensity,
        "hero_frame": {
            "source_id": hero_shot["source_id"],
            "timecode": hero_shot["source_timecode"],
        },
        "per_source": per_source,
        "generated_at": _now_iso(),
        "generator": f"ff match color ({__version__})",
    }
    if cfg.global_lut:
        out["global_lut"] = cfg.global_lut
    if hero_img_path is not None:
        out["hero_frame"]["image_path"] = str(hero_img_path)

    validate(out, "color-plan")
    return out


def match_color_from_files(
    *,
    shot_list_path: Path,
    source_catalog_path: Path,
    output_path: Path,
    output_dir: Path | None = None,
    config: ColorMatchConfig | None = None,
) -> dict[str, Any]:
    shots = json.loads(shot_list_path.read_text(encoding="utf-8"))
    catalog = json.loads(source_catalog_path.read_text(encoding="utf-8"))
    out_dir = output_dir or output_path.parent
    plan = match_color(
        shot_list=shots,
        source_catalog=catalog,
        output_dir=out_dir,
        config=config,
    )
    validate_and_write(plan, "color-plan", output_path)
    return plan
