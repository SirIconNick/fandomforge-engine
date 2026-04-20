"""End-to-end regression suite for FandomForge rendered edits.

Compares a freshly-rendered project's `ff review` scores against a stored
baseline snapshot and reports PASS / WARN / FAIL.  Designed to run in CI
after every commit that touches render-side code.

Tier classification (Phase 4.5 thresholds):
  Exceptional  overall >= 90  AND  all dim scores >= 80  AND  coherence >= 85
               (coherence dimension is optional — omitted = not blocking)
  Competent    overall >= 75  AND  no dim score < 60
  Amateur      everything else

The ``freeze`` command refuses to lock a new baseline unless the project
meets the requested tier floor (default: Competent).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------

TIER_EXCEPTIONAL = "exceptional"
TIER_COMPETENT = "competent"
TIER_AMATEUR = "amateur"

# Human-readable tier labels map (lower -> higher)
_TIER_RANK: dict[str, int] = {
    TIER_AMATEUR: 0,
    TIER_COMPETENT: 1,
    TIER_EXCEPTIONAL: 2,
}


def classify_tier(review: dict[str, Any]) -> str:
    """Classify a review dict into Exceptional / Competent / Amateur.

    Args:
        review: Parsed post-render-review JSON dict (schema_version 1).

    Returns:
        One of the TIER_* constants.
    """
    overall = float(review.get("score", 0))
    dimensions = review.get("dimensions", [])
    dim_scores = {d["name"]: float(d.get("score", 0)) for d in dimensions}

    # Coherence is optional — absence is not blocking
    coherence_score = dim_scores.get("coherence")

    if (
        overall >= 90
        and all(s >= 80 for s in dim_scores.values())
        and (coherence_score is None or coherence_score >= 85)
    ):
        return TIER_EXCEPTIONAL

    if overall >= 75 and all(s >= 60 for s in dim_scores.values()):
        return TIER_COMPETENT

    return TIER_AMATEUR


def tier_meets_floor(tier: str, floor: str) -> bool:
    """Return True when *tier* is at or above *floor*."""
    return _TIER_RANK.get(tier, -1) >= _TIER_RANK.get(floor, 0)


# ---------------------------------------------------------------------------
# Baseline I/O
# ---------------------------------------------------------------------------

def load_baseline(path: Path) -> dict[str, Any]:
    """Load and return a baseline JSON file.  Raises FileNotFoundError / json.JSONDecodeError."""
    return json.loads(path.read_text())


def write_baseline(review: dict[str, Any], path: Path) -> None:
    """Write a review dict as a baseline JSON file (pretty-printed)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(review, indent=2) + "\n")


def baseline_name_from_project(project_slug: str) -> str:
    return f"{project_slug}.review.json"


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------

DEFAULT_OVERALL_TOLERANCE = 2.0    # max overall score drop before FAIL
DEFAULT_DIM_TOLERANCE = 5.0        # max per-dimension score drop before FAIL


@dataclass
class DimResult:
    name: str
    baseline_score: float
    current_score: float
    delta: float                   # current - baseline (negative = regression)
    status: str                    # "pass" | "warn" | "fail"


@dataclass
class ProjectRegressionResult:
    project_slug: str
    baseline_score: float
    current_score: float
    baseline_grade: str
    current_grade: str
    overall_delta: float           # current - baseline
    dim_results: list[DimResult] = field(default_factory=list)
    status: str = "pass"           # "pass" | "warn" | "fail"
    reason: str = ""


def compare_reviews(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    overall_tolerance: float = DEFAULT_OVERALL_TOLERANCE,
    dim_tolerance: float = DEFAULT_DIM_TOLERANCE,
    strict: bool = False,
) -> ProjectRegressionResult:
    """Compare a current review against a baseline and return a structured result.

    Args:
        baseline: Baseline review dict.
        current: Current review dict.
        overall_tolerance: Max allowable overall score drop (ignored when strict=True).
        dim_tolerance: Max allowable per-dimension score drop (ignored when strict=True).
        strict: When True, any drop at all fails.

    Returns:
        ProjectRegressionResult with pass/warn/fail status.
    """
    slug = current.get("project_slug", baseline.get("project_slug", "unknown"))
    base_score = float(baseline.get("score", 0))
    curr_score = float(current.get("score", 0))
    overall_delta = curr_score - base_score

    base_grade = baseline.get("grade", "?")
    curr_grade = current.get("grade", "?")

    # Build per-dimension comparison
    base_dims = {d["name"]: float(d.get("score", 0)) for d in baseline.get("dimensions", [])}
    curr_dims = {d["name"]: float(d.get("score", 0)) for d in current.get("dimensions", [])}

    dim_results: list[DimResult] = []
    for name, base_ds in base_dims.items():
        curr_ds = curr_dims.get(name, base_ds)  # missing dim treated as unchanged
        delta = curr_ds - base_ds
        if strict and delta < 0:
            dim_status = "fail"
        elif delta < -dim_tolerance:
            dim_status = "fail"
        else:
            dim_status = "pass"
        dim_results.append(DimResult(
            name=name,
            baseline_score=base_ds,
            current_score=curr_ds,
            delta=delta,
            status=dim_status,
        ))

    # Overall verdict
    if strict:
        if overall_delta < 0:
            status = "fail"
            reason = f"strict mode: overall dropped {abs(overall_delta):.1f} pts"
        elif any(d.status == "fail" for d in dim_results):
            status = "fail"
            bad = [d.name for d in dim_results if d.status == "fail"]
            reason = f"strict mode: dimension(s) dropped: {', '.join(bad)}"
        else:
            status = "pass"
            reason = ""
    else:
        failed_dims = [d for d in dim_results if d.status == "fail"]
        if overall_delta < -overall_tolerance or failed_dims:
            status = "fail"
            parts: list[str] = []
            if overall_delta < -overall_tolerance:
                parts.append(f"overall dropped {abs(overall_delta):.1f} pts (tolerance {overall_tolerance:.0f})")
            for d in failed_dims:
                parts.append(f"{d.name} dropped {abs(d.delta):.1f} pts")
            reason = "; ".join(parts)
        else:
            status = "pass"
            reason = ""

    return ProjectRegressionResult(
        project_slug=slug,
        baseline_score=base_score,
        current_score=curr_score,
        baseline_grade=base_grade,
        current_grade=curr_grade,
        overall_delta=overall_delta,
        dim_results=dim_results,
        status=status,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Freeze validation
# ---------------------------------------------------------------------------

@dataclass
class FreezeResult:
    project_slug: str
    tier: str
    tier_floor: str
    meets_floor: bool
    refused: bool
    baseline_path: Path | None = None
    error: str = ""


def validate_freeze(
    review: dict[str, Any],
    project_slug: str,
    tier_floor: str = TIER_COMPETENT,
) -> FreezeResult:
    """Validate that a review meets the tier floor before freezing as a baseline.

    Args:
        review: Parsed post-render-review dict.
        project_slug: Project name (used for the baseline filename).
        tier_floor: Minimum acceptable tier ("competent" or "exceptional").

    Returns:
        FreezeResult describing whether the freeze is allowed.
    """
    tier = classify_tier(review)
    ok = tier_meets_floor(tier, tier_floor)
    return FreezeResult(
        project_slug=project_slug,
        tier=tier,
        tier_floor=tier_floor,
        meets_floor=ok,
        refused=not ok,
    )


# ---------------------------------------------------------------------------
# Project discovery
# ---------------------------------------------------------------------------

def find_project_dir(
    project_slug: str,
    repo_root: Path,
) -> Path | None:
    """Locate the project directory for a given slug.

    Searches in order:
      1. regression/projects/<slug>/
      2. projects/<slug>/

    Returns None if neither exists.
    """
    for candidate in (
        repo_root / "regression" / "projects" / project_slug,
        repo_root / "projects" / project_slug,
    ):
        if candidate.is_dir():
            return candidate
    return None


def list_baseline_slugs(regression_dir: Path) -> list[str]:
    """Return project slugs for all *.review.json files in regression/baselines/."""
    baselines_dir = regression_dir / "baselines"
    if not baselines_dir.is_dir():
        return []
    return [
        p.stem.removesuffix(".review")
        for p in sorted(baselines_dir.glob("*.review.json"))
    ]


__all__ = [
    "TIER_EXCEPTIONAL",
    "TIER_COMPETENT",
    "TIER_AMATEUR",
    "classify_tier",
    "tier_meets_floor",
    "load_baseline",
    "write_baseline",
    "baseline_name_from_project",
    "compare_reviews",
    "validate_freeze",
    "find_project_dir",
    "list_baseline_slugs",
    "DimResult",
    "FreezeResult",
    "ProjectRegressionResult",
    "DEFAULT_OVERALL_TOLERANCE",
    "DEFAULT_DIM_TOLERANCE",
]
