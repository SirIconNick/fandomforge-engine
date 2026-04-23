"""Outcome aggregator — turns the render journal into learned priors.

Walks every RenderJournalEntry, correlates configuration fields with
review dimension scores, and produces a ``mined_training_priors.json``
the engine's planners can blend into their defaults.

Two kinds of correlations get surfaced:

1. **Boolean / categorical features** (e.g. ``mfv_craft_enabled``,
   ``color_preset``, each ``craft_weight`` as on/off):
   compare average overall_score in renders where the feature was
   TRUE vs FALSE. Delta above threshold = "this choice helped."

2. **Numeric features** (e.g. ``target_cpm``, ``avg_shot_duration_sec``,
   ``source_diversity_entropy``): compute Pearson correlation against
   each dimension score. Strong positive correlation = "higher value
   tends to mean higher score for that dimension."

With enough samples (>= MIN_SAMPLES_FOR_CLAIM) each correlation
becomes an actionable recommendation the engine can respect when
planning a new render.

The output is deterministic from the same journal — it's just
statistics, not an ML model. The engine's planners still own the
decision; this module just tells them what the journal says worked.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "MinedTrainingPriors",
    "BooleanImpact",
    "NumericCorrelation",
    "aggregate",
    "format_recommendations",
]


MIN_SAMPLES_FOR_CLAIM = 3
SIGNIFICANT_SCORE_DELTA = 1.5  # points — anything smaller is noise
SIGNIFICANT_CORRELATION = 0.35  # Pearson r threshold
EXPERIMENT_WEIGHT = 2.0  # paired-experiment entries count 2x in averages


@dataclass
class BooleanImpact:
    feature: str
    dimension: str  # "overall" or a specific dim name
    n_true: int
    n_false: int
    avg_true: float
    avg_false: float
    delta: float  # true_avg - false_avg

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NumericCorrelation:
    feature: str
    dimension: str
    n: int
    pearson_r: float
    feature_mean: float
    feature_stddev: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MinedTrainingPriors:
    sample_count: int
    avg_overall_score: float
    best_bucket: str | None
    best_bucket_avg_score: float
    boolean_impacts: list[BooleanImpact] = field(default_factory=list)
    numeric_correlations: list[NumericCorrelation] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "avg_overall_score": self.avg_overall_score,
            "best_bucket": self.best_bucket,
            "best_bucket_avg_score": self.best_bucket_avg_score,
            "boolean_impacts": [i.to_dict() for i in self.boolean_impacts],
            "numeric_correlations": [c.to_dict() for c in self.numeric_correlations],
            "recommendations": list(self.recommendations),
            "generated_at": self.generated_at,
        }


_DIMS = (
    "overall",
    "technical", "visual", "audio", "structural",
    "shot_list", "coherence", "arc_shape", "engagement",
)


def aggregate(entries: list[Any]) -> MinedTrainingPriors:
    """Produce mined priors from a list of RenderJournalEntry (or dicts).

    Empty input produces an empty MinedTrainingPriors — consumers use
    ``.sample_count == 0`` to detect "no data yet."
    """
    if not entries:
        return MinedTrainingPriors(
            sample_count=0,
            avg_overall_score=0.0,
            best_bucket=None,
            best_bucket_avg_score=0.0,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    # Normalize to dict shape so callers can pass dataclasses or dicts.
    data: list[dict[str, Any]] = []
    for e in entries:
        if hasattr(e, "to_dict"):
            data.append(e.to_dict())
        elif isinstance(e, dict):
            data.append(e)

    # --- Overall baselines ---
    scores = [float(d.get("overall_score") or 0.0) for d in data]
    avg = sum(scores) / len(scores)

    # --- Per-bucket averages ---
    bucket_scores: dict[str, list[float]] = {}
    for d in data:
        b = d.get("edit_type") or "unknown"
        bucket_scores.setdefault(b, []).append(float(d.get("overall_score") or 0.0))
    best_bucket: str | None = None
    best_bucket_avg = 0.0
    for b, s in bucket_scores.items():
        if s and (sum(s) / len(s)) > best_bucket_avg:
            best_bucket_avg = sum(s) / len(s)
            best_bucket = b

    # --- Boolean / categorical impact ---
    # Synthetic bootstrap rows are calibrated bucket placeholders, not real
    # render outcomes. Including them in boolean_impacts produces spurious
    # cross-bucket deltas (a craft feature looks "bad" just because the
    # buckets it fires in happen to have lower synthetic calibration than
    # the buckets it doesn't). Filter them out here — numeric correlations
    # stay all-data since those are per-feature, not per-feature×bucket.
    real_data = [
        d for d in data
        if not str(d.get("render_id") or "").startswith("synthetic-")
    ]
    impact_data = real_data if len(real_data) >= MIN_SAMPLES_FOR_CLAIM * 2 else []

    boolean_features = [
        "mfv_craft_enabled",
    ]
    # Every individual craft weight becomes a "was active for this render?"
    # boolean impact: we read craft_weights[feature] >= 0.5.
    craft_features = set()
    for d in impact_data:
        for k in (d.get("craft_weights") or {}).keys():
            craft_features.add(k)

    impacts: list[BooleanImpact] = []
    for feat in boolean_features:
        impacts.extend(_bool_impacts_for_all_dims(impact_data, feat, lambda d, f=feat: bool(d.get(f))))
    for cf in sorted(craft_features):
        impacts.extend(_bool_impacts_for_all_dims(
            impact_data, f"craft.{cf}",
            lambda d, name=cf: ((d.get("craft_weights") or {}).get(name) or 0.0) >= 0.5,
        ))

    # Keep only impacts with enough data and meaningful delta
    impacts = [
        i for i in impacts
        if i.n_true >= MIN_SAMPLES_FOR_CLAIM
        and i.n_false >= MIN_SAMPLES_FOR_CLAIM
        and abs(i.delta) >= SIGNIFICANT_SCORE_DELTA
    ]
    impacts.sort(key=lambda i: abs(i.delta), reverse=True)

    # --- Numeric correlations ---
    numeric_features = [
        "target_duration_sec",
        "pre_drop_dropout_sec",
        "j_cut_lead_sec",
        "target_cpm",
        "shot_count",
        "avg_shot_duration_sec",
        "source_diversity_entropy",
        "num_sources_used",
        "hero_reserved_count",
        "drum_fill_count",
        "lyric_sync_count",
        "dropout_windows_count",
    ]
    correlations: list[NumericCorrelation] = []
    for feat in numeric_features:
        feature_series = [d.get(feat) for d in data]
        pairs = [(float(v), s) for v, s in zip(feature_series, scores) if isinstance(v, (int, float))]
        if len(pairs) < MIN_SAMPLES_FOR_CLAIM:
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        # Per-dimension correlations
        for dim in _DIMS:
            ys_dim = _series_for_dim(data, feat, dim)
            if len(ys_dim) < MIN_SAMPLES_FOR_CLAIM:
                continue
            xs_dim = [float(d.get(feat)) for d in data if isinstance(d.get(feat), (int, float))]
            r = _pearson(xs_dim, ys_dim)
            if r is None:
                continue
            if abs(r) >= SIGNIFICANT_CORRELATION:
                correlations.append(NumericCorrelation(
                    feature=feat,
                    dimension=dim,
                    n=len(xs_dim),
                    pearson_r=round(r, 3),
                    feature_mean=round(sum(xs_dim) / len(xs_dim), 3),
                    feature_stddev=round(_stddev(xs_dim), 3),
                ))
    correlations.sort(key=lambda c: abs(c.pearson_r), reverse=True)

    priors = MinedTrainingPriors(
        sample_count=len(data),
        avg_overall_score=round(avg, 2),
        best_bucket=best_bucket,
        best_bucket_avg_score=round(best_bucket_avg, 2),
        boolean_impacts=impacts,
        numeric_correlations=correlations,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    priors.recommendations = format_recommendations(priors)
    return priors


def format_recommendations(priors: MinedTrainingPriors) -> list[str]:
    """Emit plain-English recommendations derived from the stats."""
    if priors.sample_count < MIN_SAMPLES_FOR_CLAIM:
        return [
            f"only {priors.sample_count} render(s) in the journal — "
            f"need at least {MIN_SAMPLES_FOR_CLAIM} for training claims"
        ]
    lines: list[str] = []
    for imp in priors.boolean_impacts[:8]:
        direction = "↑" if imp.delta > 0 else "↓"
        target = "overall" if imp.dimension == "overall" else f"dim={imp.dimension}"
        lines.append(
            f"{direction} {imp.feature}=True {target}: "
            f"{imp.avg_true:.1f} vs {imp.avg_false:.1f} "
            f"(Δ{imp.delta:+.1f}, n_true={imp.n_true}/n_false={imp.n_false})"
        )
    for cor in priors.numeric_correlations[:8]:
        direction = "↑" if cor.pearson_r > 0 else "↓"
        target = "overall" if cor.dimension == "overall" else f"dim={cor.dimension}"
        lines.append(
            f"{direction} {cor.feature} correlates with {target}: "
            f"r={cor.pearson_r:+.2f} (n={cor.n})"
        )
    if not lines:
        lines.append(
            f"no feature crossed significance thresholds yet across "
            f"{priors.sample_count} renders — keep feeding the journal"
        )
    if priors.best_bucket:
        lines.append(
            f"engine scores best on '{priors.best_bucket}' edits: "
            f"{priors.best_bucket_avg_score:.1f} avg"
        )
    return lines


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------


def _entry_weight(entry: dict[str, Any]) -> float:
    """Paired experiment entries count for EXPERIMENT_WEIGHT. Observational
    entries count 1.0. Lets the aggregator learn faster from controlled
    A/B comparisons than from passive renders."""
    if entry.get("experiment_id"):
        return EXPERIMENT_WEIGHT
    return 1.0


def _weighted_avg(values: list[float], weights: list[float]) -> float:
    if not values:
        return 0.0
    total_w = sum(weights) or float(len(values))
    return sum(v * w for v, w in zip(values, weights)) / total_w


def _bool_impacts_for_all_dims(
    data: list[dict[str, Any]],
    feature_name: str,
    predicate,
) -> list[BooleanImpact]:
    """Compute the true/false delta for ``feature_name`` against every
    dimension + the overall score. Paired-experiment entries are
    weighted EXPERIMENT_WEIGHT-times vs observational entries."""
    impacts: list[BooleanImpact] = []
    for dim in _DIMS:
        true_scores: list[float] = []
        false_scores: list[float] = []
        true_weights: list[float] = []
        false_weights: list[float] = []
        for d in data:
            score = _score_for_dim(d, dim)
            w = _entry_weight(d)
            if predicate(d):
                true_scores.append(score)
                true_weights.append(w)
            else:
                false_scores.append(score)
                false_weights.append(w)
        if not true_scores or not false_scores:
            continue
        avg_t = _weighted_avg(true_scores, true_weights)
        avg_f = _weighted_avg(false_scores, false_weights)
        impacts.append(BooleanImpact(
            feature=feature_name,
            dimension=dim,
            n_true=len(true_scores),
            n_false=len(false_scores),
            avg_true=round(avg_t, 2),
            avg_false=round(avg_f, 2),
            delta=round(avg_t - avg_f, 2),
        ))
    return impacts


def _score_for_dim(entry: dict[str, Any], dim: str) -> float:
    if dim == "overall":
        return float(entry.get("overall_score") or 0.0)
    return float(entry.get(f"dim_{dim}") or 0.0)


def _series_for_dim(entries: list[dict[str, Any]], feature: str, dim: str) -> list[float]:
    out: list[float] = []
    for d in entries:
        if not isinstance(d.get(feature), (int, float)):
            continue
        out.append(_score_for_dim(d, dim))
    return out


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den_x = math.sqrt(sum((v - mean_x) ** 2 for v in xs))
    den_y = math.sqrt(sum((v - mean_y) ** 2 for v in ys))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = sum(values) / len(values)
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)
