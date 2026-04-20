"""Tests for the regression suite module (fandomforge.regress).

All render and review calls are mocked — these tests exercise only the
comparison logic, tier classification, freeze validation, and baseline I/O.
No ffmpeg is called.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fandomforge.regress import (
    TIER_AMATEUR,
    TIER_COMPETENT,
    TIER_EXCEPTIONAL,
    DimResult,
    FreezeResult,
    ProjectRegressionResult,
    baseline_name_from_project,
    classify_tier,
    compare_reviews,
    find_project_dir,
    list_baseline_slugs,
    load_baseline,
    tier_meets_floor,
    validate_freeze,
    write_baseline,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _make_review(
    slug: str = "test-project",
    score: float = 90.0,
    grade: str = "A-",
    overall_verdict: str = "warn",
    overall: str = "yellow",
    dimensions: list[dict] | None = None,
) -> dict:
    """Build a minimal post-render-review dict for use in tests."""
    if dimensions is None:
        dimensions = [
            {"name": "technical",  "verdict": "pass", "score": 100.0, "findings": [], "measurements": {}},
            {"name": "visual",     "verdict": "warn", "score": 69.0,  "findings": ["dark seg"], "measurements": {}},
            {"name": "audio",      "verdict": "pass", "score": 100.0, "findings": [], "measurements": {}},
            {"name": "structural", "verdict": "pass", "score": 100.0, "findings": [], "measurements": {}},
            {"name": "shot_list",  "verdict": "pass", "score": 100.0, "findings": [], "measurements": {}},
        ]
    return {
        "schema_version": 1,
        "project_slug": slug,
        "video_path": f"projects/{slug}/exports/graded.mp4",
        "generated_at": "2026-04-19T00:00:00+00:00",
        "overall": overall,
        "overall_verdict": overall_verdict,
        "score": score,
        "grade": grade,
        "ship_recommendation": "ok",
        "dimensions": dimensions,
    }


def _all_pass_dims(score: float = 100.0) -> list[dict]:
    return [
        {"name": n, "verdict": "pass", "score": score, "findings": [], "measurements": {}}
        for n in ["technical", "visual", "audio", "structural", "shot_list"]
    ]


# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------

class TestClassifyTier:
    def test_exceptional_all_dims_high(self):
        review = _make_review(score=95.0, grade="A", dimensions=_all_pass_dims(score=95.0))
        assert classify_tier(review) == TIER_EXCEPTIONAL

    def test_exceptional_boundary_exactly_90_all_80(self):
        review = _make_review(score=90.0, grade="A-", dimensions=_all_pass_dims(score=80.0))
        assert classify_tier(review) == TIER_EXCEPTIONAL

    def test_not_exceptional_if_one_dim_below_80(self):
        dims = _all_pass_dims(score=90.0)
        dims[1]["score"] = 79.0  # visual just under
        review = _make_review(score=91.0, grade="A-", dimensions=dims)
        tier = classify_tier(review)
        # With overall >= 75 and no dim < 60 it should be Competent, not Exceptional
        assert tier == TIER_COMPETENT

    def test_competent_overall_75_no_dim_below_60(self):
        dims = _all_pass_dims(score=60.0)
        review = _make_review(score=75.0, grade="C+", dimensions=dims)
        assert classify_tier(review) == TIER_COMPETENT

    def test_amateur_overall_below_75(self):
        review = _make_review(score=70.0, grade="C-", dimensions=_all_pass_dims(score=70.0))
        assert classify_tier(review) == TIER_AMATEUR

    def test_amateur_if_any_dim_below_60(self):
        dims = _all_pass_dims(score=80.0)
        dims[0]["score"] = 55.0  # technical tanks it
        review = _make_review(score=76.0, grade="C+", dimensions=dims)
        assert classify_tier(review) == TIER_AMATEUR

    def test_coherence_absence_not_blocking_for_exceptional(self):
        """Coherence dimension is optional — its absence must not prevent Exceptional."""
        # All standard five dims at 90, overall 92, no coherence key
        dims = _all_pass_dims(score=90.0)
        review = _make_review(score=92.0, grade="A-", dimensions=dims)
        assert classify_tier(review) == TIER_EXCEPTIONAL


class TestTierMeetsFloor:
    def test_exceptional_meets_exceptional(self):
        assert tier_meets_floor(TIER_EXCEPTIONAL, TIER_EXCEPTIONAL) is True

    def test_exceptional_meets_competent(self):
        assert tier_meets_floor(TIER_EXCEPTIONAL, TIER_COMPETENT) is True

    def test_competent_meets_competent(self):
        assert tier_meets_floor(TIER_COMPETENT, TIER_COMPETENT) is True

    def test_competent_does_not_meet_exceptional(self):
        assert tier_meets_floor(TIER_COMPETENT, TIER_EXCEPTIONAL) is False

    def test_amateur_meets_nothing(self):
        assert tier_meets_floor(TIER_AMATEUR, TIER_COMPETENT) is False
        assert tier_meets_floor(TIER_AMATEUR, TIER_EXCEPTIONAL) is False


# ---------------------------------------------------------------------------
# compare_reviews — tolerance logic
# ---------------------------------------------------------------------------

class TestCompareReviews:
    def _baseline(self, score: float = 90.0) -> dict:
        return _make_review(score=score, grade="A-")

    def _current(self, score: float, dim_scores: dict[str, float] | None = None) -> dict:
        if dim_scores is None:
            # Default dims match the baseline shape
            dims = [
                {"name": "technical",  "verdict": "pass", "score": 100.0, "findings": [], "measurements": {}},
                {"name": "visual",     "verdict": "warn", "score": 69.0,  "findings": [],  "measurements": {}},
                {"name": "audio",      "verdict": "pass", "score": 100.0, "findings": [], "measurements": {}},
                {"name": "structural", "verdict": "pass", "score": 100.0, "findings": [], "measurements": {}},
                {"name": "shot_list",  "verdict": "pass", "score": 100.0, "findings": [], "measurements": {}},
            ]
        else:
            dims = [
                {"name": n, "verdict": "pass", "score": s, "findings": [], "measurements": {}}
                for n, s in dim_scores.items()
            ]
        return _make_review(score=score, grade="A-", dimensions=dims)

    # --- pass cases ---

    def test_pass_when_score_within_tolerance(self):
        """Baseline 90, current 89 — within default -2 tolerance."""
        baseline = self._baseline(90.0)
        current = self._current(89.0)
        result = compare_reviews(baseline, current)
        assert result.status == "pass"

    def test_pass_when_score_identical(self):
        baseline = self._baseline(90.0)
        current = self._current(90.0)
        result = compare_reviews(baseline, current)
        assert result.status == "pass"
        assert result.overall_delta == pytest.approx(0.0)

    def test_pass_when_score_improves(self):
        baseline = self._baseline(90.0)
        current = self._current(95.0)
        result = compare_reviews(baseline, current)
        assert result.status == "pass"

    # --- fail cases ---

    def test_fail_when_score_drops_too_far(self):
        """Baseline 90, current 80 — drop of 10 exceeds default tolerance of 2."""
        baseline = self._baseline(90.0)
        current = self._current(80.0)
        result = compare_reviews(baseline, current)
        assert result.status == "fail"
        assert "dropped" in result.reason.lower()

    def test_fail_respects_custom_tolerance(self):
        """Custom tolerance of 5: baseline 90, current 84 = 6 drop → fail."""
        baseline = self._baseline(90.0)
        current = self._current(84.0)
        result = compare_reviews(baseline, current, overall_tolerance=5.0)
        assert result.status == "fail"

    def test_pass_respects_custom_tolerance(self):
        """Custom tolerance of 10: baseline 90, current 82 = 8 drop → pass."""
        baseline = self._baseline(90.0)
        current = self._current(82.0)
        result = compare_reviews(baseline, current, overall_tolerance=10.0)
        assert result.status == "pass"

    # --- strict mode ---

    def test_strict_fails_any_drop(self):
        """Baseline 90, current 89 with --strict → fail (1-pt drop)."""
        baseline = self._baseline(90.0)
        current = self._current(89.0)
        result = compare_reviews(baseline, current, strict=True)
        assert result.status == "fail"
        assert "strict" in result.reason.lower()

    def test_strict_passes_when_no_drop(self):
        baseline = self._baseline(90.0)
        current = self._current(90.0)
        result = compare_reviews(baseline, current, strict=True)
        assert result.status == "pass"

    def test_strict_passes_when_score_improves(self):
        baseline = self._baseline(90.0)
        current = self._current(92.0)
        result = compare_reviews(baseline, current, strict=True)
        assert result.status == "pass"

    # --- dimension-level ---

    def test_dim_drop_beyond_tolerance_fails(self):
        baseline_dims = {
            "technical": 100.0, "visual": 69.0, "audio": 100.0,
            "structural": 100.0, "shot_list": 100.0,
        }
        current_dims = dict(baseline_dims)
        current_dims["audio"] = 60.0  # drop of 40 > default 5
        baseline = _make_review(score=90.0, dimensions=[
            {"name": n, "verdict": "pass", "score": s, "findings": [], "measurements": {}}
            for n, s in baseline_dims.items()
        ])
        current = _make_review(score=90.0, dimensions=[
            {"name": n, "verdict": "pass", "score": s, "findings": [], "measurements": {}}
            for n, s in current_dims.items()
        ])
        result = compare_reviews(baseline, current)
        assert result.status == "fail"
        audio_dim = next(d for d in result.dim_results if d.name == "audio")
        assert audio_dim.status == "fail"

    def test_delta_is_accurate(self):
        baseline = self._baseline(90.0)
        current = self._current(88.0)
        result = compare_reviews(baseline, current)
        assert result.overall_delta == pytest.approx(-2.0)


# ---------------------------------------------------------------------------
# Baseline I/O
# ---------------------------------------------------------------------------

class TestBaselineIO:
    def test_write_and_load_roundtrip(self, tmp_path):
        review = _make_review()
        path = tmp_path / "test.review.json"
        write_baseline(review, path)
        loaded = load_baseline(path)
        assert loaded["project_slug"] == review["project_slug"]
        assert loaded["score"] == review["score"]

    def test_write_creates_parent_dirs(self, tmp_path):
        review = _make_review()
        path = tmp_path / "nested" / "dirs" / "test.review.json"
        write_baseline(review, path)
        assert path.exists()

    def test_load_raises_on_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_baseline(tmp_path / "nope.json")

    def test_baseline_name_format(self):
        assert baseline_name_from_project("action-legends") == "action-legends.review.json"


# ---------------------------------------------------------------------------
# list_baseline_slugs and find_project_dir
# ---------------------------------------------------------------------------

class TestListBaselineSlugs:
    def test_empty_when_no_baselines_dir(self, tmp_path):
        regression_dir = tmp_path / "regression"
        regression_dir.mkdir()
        assert list_baseline_slugs(regression_dir) == []

    def test_finds_slugs(self, tmp_path):
        regression_dir = tmp_path / "regression"
        baselines = regression_dir / "baselines"
        baselines.mkdir(parents=True)
        (baselines / "action-legends.review.json").write_text("{}")
        (baselines / "tribute.review.json").write_text("{}")
        slugs = list_baseline_slugs(regression_dir)
        assert "action-legends" in slugs
        assert "tribute" in slugs
        assert len(slugs) == 2

    def test_ignores_non_review_json(self, tmp_path):
        regression_dir = tmp_path / "regression"
        baselines = regression_dir / "baselines"
        baselines.mkdir(parents=True)
        (baselines / "action-legends.review.json").write_text("{}")
        (baselines / "README.md").write_text("hi")
        slugs = list_baseline_slugs(regression_dir)
        assert slugs == ["action-legends"]


class TestFindProjectDir:
    def test_finds_in_regression_projects(self, tmp_path):
        reg_proj = tmp_path / "regression" / "projects" / "my-edit"
        reg_proj.mkdir(parents=True)
        result = find_project_dir("my-edit", tmp_path)
        assert result == reg_proj

    def test_finds_in_projects_fallback(self, tmp_path):
        proj = tmp_path / "projects" / "action-legends"
        proj.mkdir(parents=True)
        result = find_project_dir("action-legends", tmp_path)
        assert result == proj

    def test_prefers_regression_projects_over_projects(self, tmp_path):
        reg_proj = tmp_path / "regression" / "projects" / "overlap"
        reg_proj.mkdir(parents=True)
        real_proj = tmp_path / "projects" / "overlap"
        real_proj.mkdir(parents=True)
        result = find_project_dir("overlap", tmp_path)
        assert result == reg_proj

    def test_returns_none_when_not_found(self, tmp_path):
        assert find_project_dir("ghost-project", tmp_path) is None


# ---------------------------------------------------------------------------
# validate_freeze
# ---------------------------------------------------------------------------

class TestValidateFreeze:
    def test_freeze_refuses_amateur_tier(self):
        """A project scoring Amateur is refused when floor=Competent."""
        review = _make_review(score=60.0, grade="D-", dimensions=_all_pass_dims(score=60.0))
        # 60 overall < 75 → Amateur
        result = validate_freeze(review, "bad-project", tier_floor=TIER_COMPETENT)
        assert result.refused is True
        assert result.meets_floor is False
        assert result.tier == TIER_AMATEUR

    def test_freeze_refuses_competent_when_floor_exceptional(self):
        """Competent project refused when floor=exceptional."""
        dims = _all_pass_dims(score=65.0)
        review = _make_review(score=77.0, grade="C+", dimensions=dims)
        result = validate_freeze(review, "mid-project", tier_floor=TIER_EXCEPTIONAL)
        assert result.refused is True
        assert result.tier == TIER_COMPETENT

    def test_freeze_writes_baseline_when_competent_meets_competent_floor(self, tmp_path):
        """Competent project passes Competent floor check."""
        dims = _all_pass_dims(score=75.0)
        review = _make_review(score=76.0, grade="C+", dimensions=dims)
        result = validate_freeze(review, "good-project", tier_floor=TIER_COMPETENT)
        assert result.refused is False
        assert result.meets_floor is True
        assert result.tier == TIER_COMPETENT

    def test_freeze_accepts_exceptional_project(self):
        dims = _all_pass_dims(score=90.0)
        review = _make_review(score=92.0, grade="A-", dimensions=dims)
        result = validate_freeze(review, "great-project", tier_floor=TIER_COMPETENT)
        assert result.refused is False
        assert result.tier == TIER_EXCEPTIONAL


# ---------------------------------------------------------------------------
# Regression run — "no baselines returns pass" behavior via CLI
# ---------------------------------------------------------------------------

class TestRegressCLINoBaselines:
    """Test the CLI command's behavior when there are no baselines to run."""

    def test_regress_no_baselines_exits_zero(self, tmp_path):
        """Empty baselines dir -> list_baseline_slugs returns [] -> exit 0 path."""
        from click.testing import CliRunner
        from fandomforge.cli import regress_run_cmd

        # Create a bare regression dir with empty baselines/
        regression_dir = tmp_path / "regression"
        (regression_dir / "baselines").mkdir(parents=True)

        runner = CliRunner()
        result = runner.invoke(
            regress_run_cmd,
            ["--repo-root", str(tmp_path)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "no baselines" in result.output.lower()

    def test_regress_finds_action_legends_baseline(self, tmp_path):
        """Given a fixture baseline file, list_baseline_slugs finds it."""
        regression_dir = tmp_path / "regression"
        baselines = regression_dir / "baselines"
        baselines.mkdir(parents=True)

        baseline_review = _make_review(slug="action-legends", score=90.7, grade="A-")
        (baselines / "action-legends.review.json").write_text(
            json.dumps(baseline_review, indent=2)
        )

        slugs = list_baseline_slugs(regression_dir)
        assert "action-legends" in slugs
