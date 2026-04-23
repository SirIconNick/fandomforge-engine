"""Convert old per-playlist reference-priors.json into new bucket-report.json format.

The FandomForge engine originally mined reference priors per-playlist
(`references/tribute-pl1/reference-priors.json` etc). The current pipeline
aggregates per *bucket* and emits a richer `bucket-report.json` with
consensus craft weights. Running the full forensic pipeline on the 147
already-downloaded per-playlist mp4s to regenerate this data would take
hours per run.

This module walks the old per-playlist dirs, groups them into the new
buckets (tribute, sad, dance, hype_trailer, emotional), averages their
numeric priors, seeds consensus_craft_weights from the hand-tuned
MFV_CRAFT_WEIGHTS table, and writes a bucket-report.json that's good
enough for the web UI and the craft-bias stack.

The user can refine the craft weights via the /api/correct endpoint —
that's exactly what the human correction flow is for.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "BUCKET_MAPPING",
    "migrate_bucket",
    "migrate_all",
]


BUCKET_MAPPING: dict[str, list[str]] = {
    "tribute": [
        "tribute-pl1", "tribute-pl2", "tribute-pl3", "tribute-pl4",
        "tribute-pl5", "tribute-pl6", "tribute-pl7", "tribute-pl8",
        "tribute-pl9",
    ],
    "sad": ["sad-pl1", "sad-singles"],
    "hype_trailer": ["hype-pl1"],
    "dance": ["dance-singles"],
    # Emotional has no dedicated playlist — seed from sad + tribute which
    # share the emotion-edit grammar (slow cuts, lyric sync, restrained
    # diegetic). The user corrects via the UI to diverge from that seed.
    "emotional": [
        "sad-pl1", "sad-singles",
        "tribute-pl1", "tribute-pl2", "tribute-pl3",
    ],
}

_MINED_PRIOR_KEYS = (
    "cuts_on_beat_pct_mean",
    "tempo_bpm_median",
    "median_shot_duration_sec",
    "cuts_per_minute",
    "avg_luma_mean",
    "dark_shot_pct_mean",
    "bright_shot_pct_mean",
    "saturation_mean_mean",
)


def _load(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("unreadable priors %s: %s", path, exc)
        return None


def _merge_priors(priors_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Average numeric priors across the input list, carry non-numeric
    fields through when consistent. Normalizes percentage fields from the
    old 0-100 scale to the new 0-1 scale so both formats line up."""
    merged: dict[str, Any] = {}
    for key in _MINED_PRIOR_KEYS:
        values = [
            float(p.get(key)) for p in priors_list
            if isinstance(p.get(key), (int, float))
        ]
        if values:
            avg = sum(values) / len(values)
            # Old priors store percentages as 0-100; new format uses 0-1.
            if key.endswith("_pct_mean") and avg > 1.5:
                avg = avg / 100.0
            merged[key] = round(avg, 3)

    cpm_ranges = [p.get("shot_duration_range_sec") for p in priors_list
                  if isinstance(p.get("shot_duration_range_sec"), list)
                  and len(p.get("shot_duration_range_sec", [])) == 2]
    if cpm_ranges:
        lows = [float(r[0]) for r in cpm_ranges]
        highs = [float(r[1]) for r in cpm_ranges]
        merged["shot_duration_range_sec"] = [
            round(sum(lows) / len(lows), 3),
            round(sum(highs) / len(highs), 3),
        ]
    return merged


def _craft_seed_for(bucket: str) -> dict[str, float]:
    """Seed consensus_craft_weights from the hand-tuned table so the UI
    card shows realistic defaults. The user corrects from here."""
    from fandomforge.config import MFV_CRAFT_WEIGHTS
    return {k: float(v) for k, v in MFV_CRAFT_WEIGHTS.get(bucket, {}).items()}


def _cpm_range_from(priors_list: list[dict[str, Any]]) -> list[float] | None:
    """Estimate a target_cpm range from cuts_per_minute across the merged
    playlists. Uses the min and max values."""
    cpms = [
        float(p.get("cuts_per_minute"))
        for p in priors_list
        if isinstance(p.get("cuts_per_minute"), (int, float))
    ]
    if not cpms:
        return None
    return [round(min(cpms), 1), round(max(cpms), 1)]


def migrate_bucket(
    bucket: str,
    playlist_dirs: list[str],
    references_dir: Path = Path("references"),
    *,
    force: bool = False,
) -> Path | None:
    """Build a bucket-report.json for one bucket from its per-playlist priors.

    Returns the output path or None when the bucket has no input data.
    Skips when the output already exists unless ``force=True``.
    """
    out_dir = references_dir / bucket
    out_path = out_dir / "bucket-report.json"
    if out_path.exists() and not force:
        logger.info("skipping %s — bucket-report.json already exists", bucket)
        return out_path

    priors_list: list[dict[str, Any]] = []
    total_videos = 0
    source_playlists: list[str] = []
    video_ids: list[str] = []
    for sub in playlist_dirs:
        pdir = references_dir / sub
        prior_file = pdir / "reference-priors.json"
        if not prior_file.exists():
            continue
        data = _load(prior_file)
        if not data:
            continue
        priors = data.get("priors") or {}
        if priors:
            priors_list.append(priors)
        total_videos += int(data.get("video_count") or 0)
        for pl in (data.get("source_playlists") or []):
            if isinstance(pl, str) and pl.startswith("http"):
                source_playlists.append(pl)
        for v in (data.get("videos") or []):
            if isinstance(v, dict) and v.get("id"):
                video_ids.append(str(v["id"]))

    if not priors_list:
        logger.info("no old priors for bucket %s", bucket)
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    mined = _merge_priors(priors_list)
    cpm_range = _cpm_range_from(priors_list)
    bucket_report = {
        "bucket": bucket,
        "sample_count": total_videos,
        "sample_source": "legacy-migration",
        "top_performers": [],
        "defining_moves": [],
        "anti_patterns": [],
        "consensus_craft_weights": _craft_seed_for(bucket),
        "consensus_target_cpm_range": cpm_range,
        "consensus_edit_type": bucket,
        "mined_priors": mined,
        "per_video_grades": {},
        "video_ids": video_ids,
        "source_playlists": list(dict.fromkeys(source_playlists)),  # dedup preserve order
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "legacy_priors_migrator",
    }
    out_path.write_text(
        json.dumps(bucket_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(
        "migrated %s: %d playlists, %d videos → %s",
        bucket, len(priors_list), total_videos, out_path,
    )
    return out_path


def migrate_all(
    references_dir: Path = Path("references"),
    *,
    force: bool = False,
) -> dict[str, Path | None]:
    """Migrate every bucket in BUCKET_MAPPING. Returns {bucket: path_or_None}."""
    results: dict[str, Path | None] = {}
    for bucket, playlists in BUCKET_MAPPING.items():
        results[bucket] = migrate_bucket(
            bucket, playlists, references_dir=references_dir, force=force,
        )
    return results
