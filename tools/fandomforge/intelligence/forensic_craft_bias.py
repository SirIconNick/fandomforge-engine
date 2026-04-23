"""Craft-weight bias from the reference corpus.

The engine's `MFV_CRAFT_WEIGHTS` table is a hand-tuned starting point.
Once the forensic pipeline has analyzed a bucket's reference corpus,
we KNOW what craft techniques that bucket's expert editors actually
lean on — and that data should flow back into the engine's config so
future renders align with observed practice rather than intuition.

This module reads ``references/<bucket>/bucket-report.json`` (produced
by ``ff auto``) and returns a dict of suggested craft weights for that
bucket. The config layer blends this with the table and with the
training journal's render-outcome bias.

Blend priority, high to low:
  1. Training bias (real render outcomes from ``ff autopilot``)
  2. Forensic corpus bias (this module — observed in reference MFVs)
  3. Hand-tuned `MFV_CRAFT_WEIGHTS` table (baseline)

No single layer can override the others by itself — each biases the
weight toward its own target by a fixed fraction so the engine never
commits 100% to one signal.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "forensic_craft_suggestion",
    "forensic_blend_weight",
    "corrections_suggestion",
    "corrections_blend_weight",
    "clear_cache",
    "blend_weights",
    "apply_corrections",
    "effective_weights_breakdown",
]

# Forensic priors blend in at 20% vs table's 80%. Training bias still
# gets a 30% cut when present, so the stacked blend (table 56% / forensic
# 14% / training 30%) keeps no single layer dominant.
_FORENSIC_BLEND = 0.20

# Human corrections are the heaviest single-source signal — 40% blend over
# whatever the rest of the stack produced. Still < 50% so one bad correction
# can't flip a feature entirely; repeated corrections cumulatively override.
_CORRECTIONS_BLEND = 0.40


def forensic_blend_weight() -> float:
    """How much of the final weight the forensic signal gets (0..1)."""
    return _FORENSIC_BLEND


@lru_cache(maxsize=16)
def forensic_craft_suggestion(
    bucket: str,
    *,
    references_dir: str = "references",
) -> dict[str, float] | None:
    """Return ``{feature: weight}`` suggested by the bucket synthesis, or None.

    Reads ``<references_dir>/<bucket>/bucket-report.json`` if present and
    returns its ``consensus_craft_weights`` field. Cached per bucket so
    repeated lookups during a render are cheap; call ``clear_cache()``
    after re-running ``ff auto`` to pick up fresh data.
    """
    if not bucket:
        return None
    report_path = Path(references_dir) / bucket / "bucket-report.json"
    if not report_path.exists():
        return None
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("could not load %s: %s", report_path, exc)
        return None
    consensus = data.get("consensus_craft_weights") or {}
    if not isinstance(consensus, dict) or not consensus:
        return None
    return {str(k): float(v) for k, v in consensus.items()}


def corrections_blend_weight() -> float:
    """How much of the final weight the corrections signal gets (0..1)."""
    return _CORRECTIONS_BLEND


@lru_cache(maxsize=16)
def corrections_suggestion(bucket: str) -> dict[str, float] | None:
    """Aggregate all user corrections for a bucket into a mean craft profile.

    Reads the corrections journal, filters to entries whose ``corrected_bucket``
    matches, and returns the per-feature mean of ``corrected_craft_weights``.
    Newer entries outweigh older ones via a simple exponential decay so a
    recent correction moves the signal faster than an old one.
    """
    if not bucket:
        return None
    from fandomforge.intelligence.corrections_journal import iter_corrections

    relevant = [e for e in iter_corrections() if e.corrected_bucket == bucket]
    if not relevant:
        return None
    relevant.sort(key=lambda e: e.timestamp)
    weights: dict[str, float] = {}
    denom: dict[str, float] = {}
    for idx, entry in enumerate(relevant):
        age_weight = 0.6 + 0.4 * (idx + 1) / len(relevant)
        for feat, val in (entry.corrected_craft_weights or {}).items():
            try:
                v = float(val)
            except (TypeError, ValueError):
                continue
            weights[feat] = weights.get(feat, 0.0) + age_weight * v
            denom[feat] = denom.get(feat, 0.0) + age_weight
    if not weights:
        return None
    return {k: round(weights[k] / denom[k], 3) for k in weights}


def clear_cache() -> None:
    """Invalidate the LRU cache — call after re-running ``ff auto`` or
    after writing a new bucket-report.json / corrections journal entry."""
    forensic_craft_suggestion.cache_clear()
    corrections_suggestion.cache_clear()


def apply_corrections(
    current_row: dict[str, float],
    bucket: str,
    *,
    corrections_weight: float = _CORRECTIONS_BLEND,
) -> dict[str, float]:
    """Overlay human corrections on top of whatever the rest of the stack
    produced. Returns the blended dict. When no corrections exist for the
    bucket, returns ``current_row`` unchanged."""
    correction_row = corrections_suggestion(bucket)
    if not correction_row or corrections_weight <= 0.0:
        return {k: float(v) for k, v in current_row.items()}
    out: dict[str, float] = {}
    for feat, base in current_row.items():
        if feat in correction_row:
            out[feat] = round(
                (1 - corrections_weight) * float(base)
                + corrections_weight * float(correction_row[feat]),
                3,
            )
        else:
            out[feat] = float(base)
    return out


def blend_weights(
    table_row: dict[str, Any],
    forensic_row: dict[str, float] | None,
    *,
    forensic_weight: float = _FORENSIC_BLEND,
) -> dict[str, float]:
    """Blend table weights with forensic-observed weights.

    ``table_row``: the hand-tuned baseline (e.g. from ``MFV_CRAFT_WEIGHTS``).
    ``forensic_row``: ``{feature: weight}`` from the bucket synthesis, or
    None when no corpus data exists for that bucket.
    """
    if not forensic_row or forensic_weight <= 0.0:
        return {k: float(v) for k, v in table_row.items()}
    out: dict[str, float] = {}
    for feat, base in table_row.items():
        if feat in forensic_row:
            out[feat] = round(
                (1 - forensic_weight) * float(base)
                + forensic_weight * float(forensic_row[feat]),
                3,
            )
        else:
            out[feat] = float(base)
    return out


def effective_weights_breakdown(bucket: str) -> dict[str, Any]:
    """Return a per-feature breakdown showing each bias layer's contribution.

    Lets the UI explain *why* a bucket's effective weights are what they
    are: ``{feature: {table: 1.0, forensic: 0.0, training: None, correction:
    0.0, effective: 0.6}}``. ``training`` is None when no recommendation
    exists for that feature (mined_priors below threshold).

    Mirrors the exact blend logic in ``config.craft_weights_for`` so the
    UI display matches the live pipeline values byte-for-byte.
    """
    from fandomforge.config import MFV_CRAFT_WEIGHTS, MFV_CRAFT_FEATURES, _EDIT_TYPE_FALLBACKS

    key = (bucket or "").lower().strip()
    row = MFV_CRAFT_WEIGHTS.get(key)
    if row is None:
        fallback = _EDIT_TYPE_FALLBACKS.get(key)
        if fallback:
            row = MFV_CRAFT_WEIGHTS.get(fallback)
    row = row or {}
    table = {feat: float(row.get(feat, 0.0)) for feat in MFV_CRAFT_FEATURES}

    forensic = forensic_craft_suggestion(key) or {}
    corrections = corrections_suggestion(key) or {}

    training: dict[str, bool | None] = {}
    try:
        from fandomforge.intelligence.mined_priors import training_boolean_recommends
        for feat in MFV_CRAFT_FEATURES:
            training[feat] = training_boolean_recommends(key, f"craft.{feat}")
    except Exception:  # noqa: BLE001
        for feat in MFV_CRAFT_FEATURES:
            training[feat] = None

    out: dict[str, Any] = {}
    for feat in MFV_CRAFT_FEATURES:
        # Layer 1 — start at table
        current = table[feat]
        # Layer 1 blend — forensic corpus at 20%
        forensic_val = forensic.get(feat)
        if forensic_val is not None:
            current = round(0.8 * current + 0.2 * float(forensic_val), 3)
        # Layer 2 — training bias at 30% (pulls toward 1.0 or 0.0)
        training_val = training.get(feat)
        if training_val is not None:
            target = 1.0 if training_val else 0.0
            current = round(0.7 * current + 0.3 * target, 3)
        # Layer 3 — human corrections at 40%
        correction_val = corrections.get(feat)
        if correction_val is not None:
            current = round(0.6 * current + 0.4 * float(correction_val), 3)
        out[feat] = {
            "table": table[feat],
            "forensic": forensic_val,
            "training": training_val,
            "correction": correction_val,
            "effective": current,
            "active": current >= 0.5,
        }
    return out
