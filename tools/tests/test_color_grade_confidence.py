"""Phase 3.3 — tests for color_grade_confidence stamping."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fandomforge.intelligence.color_grader import (
    _TIER_CONFIDENCE,
    compute_shot_confidence,
)


class TestComputeShotConfidence:
    def test_none_profile_returns_unknown_default(self):
        assert compute_shot_confidence(None) == _TIER_CONFIDENCE["UNKNOWN"]

    def test_empty_profile_returns_unknown_default(self):
        assert compute_shot_confidence({}) == _TIER_CONFIDENCE["UNKNOWN"]

    @pytest.mark.parametrize("tier,expected", [
        ("S", 1.00),
        ("A", 0.90),
        ("B", 0.78),
        ("C", 0.65),
        ("D", 0.45),
    ])
    def test_tier_drives_base_confidence(self, tier: str, expected: float):
        c = compute_shot_confidence({"quality_tier": tier})
        assert c == pytest.approx(expected, abs=0.001)

    def test_d_tier_below_qa_floor(self):
        """D-tier sources always land below the 0.6 floor so qa.color_grade_confidence
        warns. That's the core contract."""
        c = compute_shot_confidence({"quality_tier": "D"})
        assert c < 0.6

    def test_extreme_cast_penalty_applied(self):
        c_no_cast = compute_shot_confidence({"quality_tier": "A"})
        c_extreme = compute_shot_confidence({
            "quality_tier": "A",
            "color_cast": {"severity": "extreme"},
        })
        assert c_extreme < c_no_cast
        assert c_no_cast - c_extreme == pytest.approx(0.15, abs=0.001)

    def test_strong_cast_penalty_smaller(self):
        c_no_cast = compute_shot_confidence({"quality_tier": "A"})
        c_strong = compute_shot_confidence({
            "quality_tier": "A",
            "color_cast": {"severity": "strong"},
        })
        assert c_no_cast - c_strong == pytest.approx(0.08, abs=0.001)

    def test_saturation_out_of_range_penalty(self):
        c_normal = compute_shot_confidence({
            "quality_tier": "A", "saturation_avg": 120,
        })
        c_hot = compute_shot_confidence({
            "quality_tier": "A", "saturation_avg": 220,
        })
        c_flat = compute_shot_confidence({
            "quality_tier": "A", "saturation_avg": 20,
        })
        assert c_hot < c_normal
        assert c_flat < c_normal

    def test_confidence_clamped_0_to_1(self):
        """Stacked penalties can't drag below 0 or push above 1."""
        # Stack every penalty on an S-tier to confirm cap.
        c_capped_high = compute_shot_confidence({
            "quality_tier": "S",
        })
        assert c_capped_high <= 1.0

        # Stack every penalty on a D-tier to confirm floor.
        c_capped_low = compute_shot_confidence({
            "quality_tier": "D",
            "color_cast": {"severity": "extreme"},
            "saturation_avg": 240,
        })
        assert c_capped_low >= 0.0

    def test_unknown_tier_falls_back_to_default(self):
        c = compute_shot_confidence({"quality_tier": "Z"})
        assert c == _TIER_CONFIDENCE["UNKNOWN"]

    def test_lowercase_tier_is_normalized(self):
        """Source-profiles store tiers as 'A'/'B' but we shouldn't crash on 'a'/'b'."""
        c = compute_shot_confidence({"quality_tier": "a"})
        assert c == _TIER_CONFIDENCE["A"]


class TestStampStep:
    """Integration test for the autopilot step that stamps confidence into shot-list.json."""

    def _minimal_shot_list(self, source_ids: list[str]) -> dict:
        return {
            "schema_version": 1,
            "project_slug": "test-stamp",
            "fps": 24,
            "resolution": {"width": 1920, "height": 1080},
            "shots": [
                {
                    "id": f"s{i:03d}",
                    "act": 1,
                    "start_frame": i * 48,
                    "duration_frames": 48,
                    "source_id": sid,
                    "source_timecode": "0:00:01.000",
                    "role": "action",
                }
                for i, sid in enumerate(source_ids)
            ],
            "generated_at": "2026-04-20T00:00:00+00:00",
            "generator": "test",
        }

    def _minimal_profile(self, source_id: str, tier: str) -> dict:
        return {
            "schema_version": 1,
            "source_id": source_id,
            "quality_tier": tier,
            "source_type": "live_action",
            "resolution_native": {"width": 1920, "height": 1080},
            "framerate_native": 24,
            "aspect_ratio_native": 1.78,
            "luma_histogram": [0.0] * 16,
            "chroma_histogram": [0.0] * 16,
            "saturation_avg": 120,
            "grain_noise_floor": 0.1,
            "sharpness_score": 0.7,
            "color_temperature_kelvin": 5500,
            "color_cast": {"direction": "neutral", "severity": "none"},
            "era_bucket": "post_2015",
            "letterbox_detected": False,
            "pillarbox_detected": False,
            "frames_sampled": 100,
            "visual_hazards": [],
            "generated_at": "2026-04-20T00:00:00+00:00",
            "generator": "test",
        }

    def test_step_stamps_confidence_from_profiles(self, tmp_path: Path):
        from fandomforge.autopilot import (
            AutopilotContext, step_stamp_color_grade_confidence,
        )

        # Scaffold a tiny project under tmp_path.
        proj = tmp_path / "projects" / "stamp-smoke"
        (proj / "data" / "source-profiles").mkdir(parents=True)

        shot_list = self._minimal_shot_list(["src-A", "src-D"])
        (proj / "data" / "shot-list.json").write_text(
            json.dumps(shot_list, indent=2), encoding="utf-8",
        )
        for sid, tier in (("src-A", "A"), ("src-D", "D")):
            (proj / "data" / "source-profiles" / f"{sid}.json").write_text(
                json.dumps(self._minimal_profile(sid, tier)), encoding="utf-8",
            )

        ctx = AutopilotContext(
            project_slug="stamp-smoke",
            project_dir=proj,
            run_id="test-run",
            song_path=None,
            source_glob=None,
            prompt="",
        )
        event = step_stamp_color_grade_confidence(ctx)

        assert event.status == "ok", f"step should succeed, got {event.status}: {event.message}"
        assert event.evidence["stamped"] == 2
        assert event.evidence["low_confidence_count"] == 1  # D-tier below 0.6

        # Verify shot-list.json got updated with the field.
        updated = json.loads((proj / "data" / "shot-list.json").read_text())
        confidences = [s["color_grade_confidence"] for s in updated["shots"]]
        assert confidences[0] == pytest.approx(_TIER_CONFIDENCE["A"], abs=0.001)
        assert confidences[1] == pytest.approx(_TIER_CONFIDENCE["D"], abs=0.001)

    def test_step_handles_missing_profile_gracefully(self, tmp_path: Path):
        """When a source has no profile, stamp the UNKNOWN default instead of crashing."""
        from fandomforge.autopilot import (
            AutopilotContext, step_stamp_color_grade_confidence,
        )

        proj = tmp_path / "projects" / "missing-profile"
        (proj / "data" / "source-profiles").mkdir(parents=True)
        shot_list = self._minimal_shot_list(["src-with-profile", "src-without"])
        (proj / "data" / "shot-list.json").write_text(
            json.dumps(shot_list, indent=2), encoding="utf-8",
        )
        (proj / "data" / "source-profiles" / "src-with-profile.json").write_text(
            json.dumps(self._minimal_profile("src-with-profile", "A")), encoding="utf-8",
        )
        # src-without has no profile on disk — step must still succeed.

        ctx = AutopilotContext(
            project_slug="missing-profile",
            project_dir=proj,
            run_id="test-run",
            song_path=None,
            source_glob=None,
            prompt="",
        )
        event = step_stamp_color_grade_confidence(ctx)
        assert event.status == "ok"
        updated = json.loads((proj / "data" / "shot-list.json").read_text())
        # Shot 0 got A-tier; shot 1 got UNKNOWN default.
        assert updated["shots"][0]["color_grade_confidence"] == pytest.approx(
            _TIER_CONFIDENCE["A"], abs=0.001,
        )
        assert updated["shots"][1]["color_grade_confidence"] == pytest.approx(
            _TIER_CONFIDENCE["UNKNOWN"], abs=0.001,
        )

    def test_step_skipped_when_no_shot_list(self, tmp_path: Path):
        from fandomforge.autopilot import (
            AutopilotContext, step_stamp_color_grade_confidence,
        )
        proj = tmp_path / "projects" / "empty"
        (proj / "data").mkdir(parents=True)
        ctx = AutopilotContext(
            project_slug="empty",
            project_dir=proj,
            run_id="test-run",
            song_path=None,
            source_glob=None,
            prompt="",
        )
        event = step_stamp_color_grade_confidence(ctx)
        assert event.status == "skipped"
