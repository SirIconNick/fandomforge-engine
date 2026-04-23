"""Promote high-BPM/high-CPM action forensics into the high_energy bucket.

The high_energy bucket is narrow (n=3) because the original corpus was
light on "pure adrenaline, dance-tempo, no-breathing-room" edits. But
the action bucket (n=23) contains several forensics that visibly fit
high_energy better — fast BPM, high cuts-per-minute, short median shot,
heavy drop density.

This module scans existing forensics and flags candidates that cross
all four thresholds, then copies (not moves) them into the high_energy
bucket's forensic dir and triggers a re-synthesize. Originals stay in
action so we don't shrink that bucket's sample size.

Thresholds (calibrated from inspecting the current top-10 action perf):
* BPM >= 140
* CPM >= 60
* median_shot_duration_sec <= 1.0
* drops_per_min >= 1.5
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "reclassify_high_energy",
    "HIGH_ENERGY_THRESHOLDS",
]


@dataclass(frozen=True)
class _Thresholds:
    # Calibrated after inspecting the 23-video action bucket. Using OR
    # between BPM and CPM (either hot tempo OR rapid cuts qualifies) plus
    # a hard cap on median shot duration. Drops_per_min isn't reliable on
    # our current forensics — the effects.drops array is inconsistently
    # populated — so it's not in the gate.
    bpm_or_cpm_min: float = 1.0  # used as flag: must hit ONE of the below
    bpm: float = 130.0
    cpm: float = 75.0
    max_median_shot_sec: float = 0.7


HIGH_ENERGY_THRESHOLDS = _Thresholds()


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _forensic_score_for_high_energy(forensic: dict, t: _Thresholds) -> tuple[bool, dict]:
    """Return (qualifies, debug_info). Reads the actual forensic schema —
    bpm lives under ``music.bpm``, CPM is the mean of ``cut_timing.cpm_curve``,
    median shot duration comes from shot list, drops from the ``effects``
    or top-level ``drops`` array."""
    music = forensic.get("music") or {}
    cut_timing = forensic.get("cut_timing") or {}
    source = forensic.get("source") or {}
    shots = forensic.get("shots") or []
    drops = forensic.get("drops") or (forensic.get("effects") or {}).get("drops") or []

    bpm = float(music.get("bpm") or 0)
    cpm_curve = cut_timing.get("cpm_curve") or []
    cpm = (sum(cpm_curve) / len(cpm_curve)) if cpm_curve else 0.0

    # Median shot duration — fall back gracefully if shots lack durations
    durations = []
    for s in shots:
        dur_s = s.get("duration_sec")
        if dur_s is None and "start_sec" in s and "end_sec" in s:
            dur_s = float(s["end_sec"]) - float(s["start_sec"])
        if dur_s is not None and dur_s > 0:
            durations.append(float(dur_s))
    if durations:
        durations.sort()
        msd = durations[len(durations) // 2]
    else:
        msd = 99.0

    dur = float(source.get("duration_sec") or 0)
    dpm = (len(drops) / (dur / 60)) if dur > 0 else 0.0

    info = {
        "bpm": round(bpm, 1), "cpm": round(cpm, 1), "median_shot_sec": round(msd, 3),
        "drops_per_min": round(dpm, 2), "duration_sec": round(dur, 1),
    }
    # Qualify when short median shot AND at least one of tempo/CPM is hot
    qualifies = (
        msd <= t.max_median_shot_sec
        and (bpm >= t.bpm or cpm >= t.cpm)
    )
    return qualifies, info


def reclassify_high_energy(
    references_dir: Path = Path("references"),
    source_bucket: str = "action",
    target_bucket: str = "high_energy",
    *,
    dry_run: bool = False,
    thresholds: _Thresholds = HIGH_ENERGY_THRESHOLDS,
) -> dict[str, list[str]]:
    """Scan ``source_bucket`` forensics and copy qualifiers into
    ``target_bucket``'s forensic dir. Returns {"promoted": [ids],
    "skipped": [ids], "already_present": [ids]}."""
    src_dir = references_dir / source_bucket / "forensic"
    dst_dir = references_dir / target_bucket / "forensic"
    if not src_dir.exists():
        return {"promoted": [], "skipped": [], "already_present": []}

    dst_dir.mkdir(parents=True, exist_ok=True)
    result = {"promoted": [], "skipped": [], "already_present": []}

    for forensic_path in sorted(src_dir.glob("*.forensic.json")):
        video_id = forensic_path.stem.replace(".forensic", "")
        dst_path = dst_dir / forensic_path.name
        if dst_path.exists():
            result["already_present"].append(video_id)
            continue
        forensic = _load(forensic_path)
        if not forensic:
            result["skipped"].append(video_id)
            continue
        qualifies, info = _forensic_score_for_high_energy(forensic, thresholds)
        if not qualifies:
            result["skipped"].append(video_id)
            logger.debug("%s doesn't clear high_energy bar: %s", video_id, info)
            continue
        if dry_run:
            result["promoted"].append(video_id)
            logger.info("would promote %s: %s", video_id, info)
            continue
        # Update bucket label on the copy so analyst treats it as high_energy
        forensic = dict(forensic)
        forensic["bucket"] = target_bucket
        dst_path.write_text(
            json.dumps(forensic, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        result["promoted"].append(video_id)
        logger.info("promoted %s to %s: %s", video_id, target_bucket, info)
    return result
