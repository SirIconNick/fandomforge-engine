"""Visual signature database (Phase 3.4).

A catalog layer over per-source profiles. Stores `source-profile.json`
records under `~/.fandomforge/signatures/` indexed by source_id, era,
quality_tier, and source_type for fast lookup. Supports predictive risk
flagging: before assembly, compares each selected shot's source profile
to the project's median to flag clips with deviation > 2σ.

Per amendment A8 + Phase 3.4: bootstraps from per-source profiles, NOT
from the action-corpus (which would bias signatures toward action-only).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_SIGNATURE_DIR = Path.home() / ".fandomforge" / "signatures"


def _signatures_dir() -> Path:
    """Resolve the signatures dir, allowing FF_SIGNATURES_DIR override for tests."""
    import os
    env = os.environ.get("FF_SIGNATURES_DIR")
    if env:
        return Path(env)
    return DEFAULT_SIGNATURE_DIR


@dataclass
class DeviationFlag:
    source_id: str
    metric: str
    project_value: float
    source_value: float
    deviation_sigma: float
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "metric": self.metric,
            "project_value": round(self.project_value, 3),
            "source_value": round(self.source_value, 3),
            "deviation_sigma": round(self.deviation_sigma, 2),
            "note": self.note,
        }


def add_profile(profile: dict[str, Any]) -> Path:
    """Persist a source-profile dict into the signatures DB."""
    sd = _signatures_dir()
    sd.mkdir(parents=True, exist_ok=True)
    sid = profile.get("source_id", "")
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in sid) or "unknown"
    out = sd / f"{safe}.json"
    out.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    return out


def list_signatures(
    *,
    source_type: str | None = None,
    era_bucket: str | None = None,
    quality_tier: str | None = None,
) -> list[dict[str, Any]]:
    """Return all stored signatures, optionally filtered."""
    sd = _signatures_dir()
    if not sd.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sd.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if source_type and data.get("source_type") != source_type:
            continue
        if era_bucket and data.get("era_bucket") != era_bucket:
            continue
        if quality_tier and data.get("quality_tier") != quality_tier:
            continue
        out.append(data)
    return out


def get_signature(source_id: str) -> dict[str, Any] | None:
    sd = _signatures_dir()
    if not sd.exists():
        return None
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in source_id)
    p = sd / f"{safe}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def project_signature_summary(
    profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute project-wide median + stddev for the per-source visual stats
    we care about."""
    if not profiles:
        return {}
    fields = ("saturation_avg", "grain_noise_floor", "sharpness_score")
    summary: dict[str, dict[str, float]] = {}
    for f in fields:
        values = [float(p.get(f, 0)) for p in profiles if f in p and p[f] is not None]
        if not values:
            continue
        median = sorted(values)[len(values) // 2]
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        stddev = math.sqrt(variance)
        summary[f] = {"median": median, "mean": mean, "stddev": stddev,
                      "n": len(values), "min": min(values), "max": max(values)}
    return summary


def flag_deviations(
    project_profiles: list[dict[str, Any]],
    *,
    sigma_threshold: float = 2.0,
) -> list[DeviationFlag]:
    """Compare each profile to the project's per-metric stddev. Return
    flags for any source whose value deviates by >sigma_threshold.

    Note: per amendment A1+A8, we compare the source against the PROJECT's
    distribution, not against the global signature DB. Cross-project
    comparison happens via list_signatures() filtered by edit_type buckets.
    """
    summary = project_signature_summary(project_profiles)
    flags: list[DeviationFlag] = []
    for profile in project_profiles:
        sid = str(profile.get("source_id", ""))
        for metric, stats in summary.items():
            stddev = stats["stddev"]
            if stddev <= 1e-6:
                continue
            value = float(profile.get(metric, 0))
            sigma = abs(value - stats["mean"]) / stddev
            if sigma > sigma_threshold:
                flags.append(DeviationFlag(
                    source_id=sid,
                    metric=metric,
                    project_value=stats["mean"],
                    source_value=value,
                    deviation_sigma=sigma,
                    note=f"{metric}={value:.2f} vs project mean {stats['mean']:.2f} "
                         f"(stddev={stddev:.2f}, {sigma:.1f}σ)",
                ))
    return flags


def bootstrap_from_project(project_dir: Path) -> int:
    """Push every source-profile from a project into the signature DB.
    Returns the count added/refreshed."""
    src_dir = project_dir / "data" / "source-profiles"
    if not src_dir.exists():
        return 0
    n = 0
    for p in src_dir.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            add_profile(data)
            n += 1
        except (json.JSONDecodeError, OSError):
            continue
    return n


__all__ = [
    "DEFAULT_SIGNATURE_DIR",
    "DeviationFlag",
    "add_profile",
    "bootstrap_from_project",
    "flag_deviations",
    "get_signature",
    "list_signatures",
    "project_signature_summary",
]
