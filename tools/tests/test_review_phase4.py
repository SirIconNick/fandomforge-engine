"""Tests for Phase 4 review extensions: coherence, arc_shape, engagement,
type-specific weights, tier classification."""

from __future__ import annotations

import pytest

from fandomforge.intelligence.review_metrics import (
    score_arc_shape,
    score_coherence,
    score_engagement,
)
from fandomforge.review import (
    DIMENSION_WEIGHTS,
    DimensionReport,
    ReviewReport,
    TYPE_DIMENSION_WEIGHTS,
    classify_tier,
    overall_score,
)


def _shot(**overrides) -> dict:
    base = {
        "id": "s001", "act": 1, "start_frame": 0, "duration_frames": 24,
        "source_id": "x", "source_timecode": "0:00:00.000", "role": "hero",
    }
    base.update(overrides)
    return base


class TestCoherence:
    def test_smooth_motion_high_score(self):
        shots = [
            _shot(motion_vector=90.0),
            _shot(motion_vector=92.0),
            _shot(motion_vector=88.0),
        ]
        sl = {"shots": shots, "fps": 24}
        rep = score_coherence(sl)
        assert rep.motion_continuity > 95

    def test_opposing_motion_low_score(self):
        shots = [
            _shot(motion_vector=0.0),
            _shot(motion_vector=180.0),
            _shot(motion_vector=0.0),
        ]
        sl = {"shots": shots, "fps": 24}
        rep = score_coherence(sl)
        assert rep.motion_continuity < 10

    def test_color_continuity_with_close_lumas(self):
        shots = [
            _shot(color_notes="luma=0.4"),
            _shot(color_notes="luma=0.45"),
            _shot(color_notes="luma=0.42"),
        ]
        sl = {"shots": shots, "fps": 24}
        rep = score_coherence(sl)
        assert rep.color_continuity == 100.0

    def test_pace_continuity_act_boundary_forgives_jump(self):
        shots = [
            _shot(start_frame=0, duration_frames=72),  # 3.0s at 24fps
            _shot(start_frame=72, duration_frames=12),  # 0.5s — 6x jump
        ]
        acts = [
            {"number": 1, "start_sec": 0, "end_sec": 3,
             "energy_target": 30, "emotional_goal": "x"},
            {"number": 2, "start_sec": 3, "end_sec": 10,
             "energy_target": 70, "emotional_goal": "y"},
        ]
        sl = {"shots": shots, "fps": 24}
        rep = score_coherence(sl, edit_plan={"acts": acts})
        assert rep.pace_continuity == 100.0

    def test_no_motion_data_skipped(self):
        sl = {"shots": [_shot(), _shot()], "fps": 24}
        rep = score_coherence(sl)
        assert "motion_vector" in str(rep.notes).lower() or "motion" in str(rep.notes).lower()


class TestArcShape:
    def test_no_curve_returns_zero(self):
        rep = score_arc_shape(None)
        assert rep.composite == 0.0
        assert any("no tension-curve" in n for n in rep.notes)

    def test_strong_build_to_climax_scores_high(self):
        curve = {
            "samples": [
                {"actual_tension": 0.1, "arc_role": "setup"},
                {"actual_tension": 0.2, "arc_role": "setup"},
                {"actual_tension": 0.9, "arc_role": "climax"},
                {"actual_tension": 0.95, "arc_role": "climax"},
                {"actual_tension": 0.3, "arc_role": "release"},
            ],
            "summary": {"rms_delta": 0.1},
        }
        rep = score_arc_shape(curve)
        assert rep.builds_to_climax == 100.0
        assert rep.resolves == 100.0
        assert rep.intent_match >= 80

    def test_no_climax_resolution_drop_low(self):
        curve = {
            "samples": [
                {"actual_tension": 0.5, "arc_role": "setup"},
                {"actual_tension": 0.6, "arc_role": "climax"},
                {"actual_tension": 0.95, "arc_role": "release"},  # release exceeds climax
            ],
            "summary": {"rms_delta": 0.4},
        }
        rep = score_arc_shape(curve)
        assert rep.resolves < 50


class TestEngagement:
    def test_diverse_shots_score_well(self):
        shots = [
            _shot(fandom="A", role="hero", framing="CU"),
            _shot(fandom="B", role="action", framing="MS"),
            _shot(fandom="C", role="reaction", framing="medium"),
        ]
        sl = {"shots": shots, "fps": 24}
        rep = score_engagement(sl)
        assert rep.visual_variety > 80

    def test_target_cpm_off_drops_pacing_score(self):
        shots = [_shot(start_frame=24 * i) for i in range(5)]  # 1 cut/sec → 60 cpm
        sl = {"shots": shots, "fps": 24}
        # Target very slow — deviation big
        rep = score_engagement(sl, edit_type_priors={"target_cuts_per_minute": 10})
        assert rep.pacing_curve_match < 50

    def test_no_complement_pairs_zero(self):
        shots = [_shot(start_frame=0), _shot(start_frame=24)]
        sl = {"shots": shots, "fps": 24}
        rep = score_engagement(sl, complement_plan={"pairs": []})
        assert rep.complement_usage == 0


class TestTypeSpecificWeights:
    def _legacy_dims(self, vals: dict[str, str]) -> list[DimensionReport]:
        return [DimensionReport(name=n, verdict=v) for n, v in vals.items()]

    def test_action_weights_emphasize_engagement(self):
        # Action: engagement=0.15 (high). If engagement scores 100 and others 50,
        # action total > legacy default total
        dims_engagement_high = [
            DimensionReport(name="technical", verdict="warn"),
            DimensionReport(name="visual", verdict="warn"),
            DimensionReport(name="audio", verdict="warn"),
            DimensionReport(name="structural", verdict="warn"),
            DimensionReport(name="shot_list", verdict="warn"),
            DimensionReport(name="coherence", verdict="warn"),
            DimensionReport(name="arc_shape", verdict="warn"),
            DimensionReport(name="engagement", verdict="pass"),
        ]
        action_score = overall_score(dims_engagement_high, edit_type="action")
        default_score = overall_score(dims_engagement_high)
        assert action_score >= default_score

    def test_unknown_type_falls_back_to_default(self):
        dims = [
            DimensionReport(name="technical", verdict="pass"),
            DimensionReport(name="visual", verdict="pass"),
        ]
        s_unknown = overall_score(dims, edit_type="not-a-real-type")
        s_default = overall_score(dims)
        assert s_unknown == s_default


class TestTierClassification:
    def _report(self, score: float, dims: list[DimensionReport]) -> ReviewReport:
        return ReviewReport(
            project_slug="x", video_path="x", generated_at="x",
            overall="green", overall_verdict="pass",
            score=score, grade="A", dimensions=dims,
        )

    def test_exceptional_requires_high_overall_and_coherence(self):
        dims = [
            DimensionReport(name=n, verdict="pass") for n in (
                "technical", "visual", "audio", "structural", "shot_list",
                "coherence", "arc_shape", "engagement"
            )
        ]
        # All dims pass = score 100. Manually set composite scores via measurements
        # since the verdict alone determines _dimension_score.
        report = self._report(95, dims)
        # Need coherence dim score >= 85 for Exceptional. verdict=pass gives 100.
        tier = classify_tier(report)
        assert tier == "Exceptional"

    def test_competent_at_75_with_no_below_60(self):
        dims = [
            DimensionReport(name="technical", verdict="pass"),
            DimensionReport(name="visual", verdict="warn"),
            DimensionReport(name="audio", verdict="pass"),
            DimensionReport(name="structural", verdict="pass"),
            DimensionReport(name="shot_list", verdict="pass"),
            DimensionReport(name="coherence", verdict="warn"),
            DimensionReport(name="arc_shape", verdict="warn"),
            DimensionReport(name="engagement", verdict="warn"),
        ]
        report = self._report(78, dims)
        tier = classify_tier(report)
        # warn = 75 score, all dims >= 60 → Competent
        assert tier in ("Competent", "Exceptional")

    def test_amateur_when_dim_below_60(self):
        dims = [
            DimensionReport(name="technical", verdict="pass"),
            DimensionReport(name="visual", verdict="fail"),  # 25 score
            DimensionReport(name="audio", verdict="pass"),
        ]
        report = self._report(50, dims)
        tier = classify_tier(report)
        assert tier == "Amateur"
