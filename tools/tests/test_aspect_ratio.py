"""Tests for the aspect ratio arbiter (Phase 3.1)."""

from __future__ import annotations

import pytest

from fandomforge.intelligence.aspect_ratio import (
    AR_TOLERANCE,
    build_aspect_plan,
    parse_ar,
)
from fandomforge.validation import validate


class TestParseAr:
    def test_16_9(self):
        assert abs(parse_ar("16:9") - 1.778) < 0.005

    def test_4_3(self):
        assert abs(parse_ar("4:3") - 1.333) < 0.005

    def test_2_39_1(self):
        assert abs(parse_ar("2.39:1") - 2.39) < 0.005

    def test_invalid_returns_zero(self):
        assert parse_ar("not-a-ratio") == 0.0
        assert parse_ar("") == 0.0


class TestDecisions:
    def _shot_list(self, shots: list[dict]) -> dict:
        return {
            "schema_version": 1, "project_slug": "demo",
            "fps": 24, "resolution": {"width": 1920, "height": 1080},
            "shots": shots,
        }

    def test_matching_ar_is_none(self):
        sl = self._shot_list([{"id": "s1", "act": 1, "start_frame": 0,
                                "duration_frames": 24, "source_id": "src",
                                "source_timecode": "0:00:00.000", "role": "hero"}])
        plan = build_aspect_plan(sl, target_ar="16:9", source_profiles={
            "src": {"aspect_ratio_native": "16:9"}
        })
        assert plan["decisions"][0]["decision"] == "none"

    def test_4_3_in_16_9_pillarboxes(self):
        sl = self._shot_list([{"id": "s1", "act": 1, "start_frame": 0,
                                "duration_frames": 24, "source_id": "anime",
                                "source_timecode": "0:00:00.000", "role": "hero"}])
        plan = build_aspect_plan(sl, target_ar="16:9", source_profiles={
            "anime": {"aspect_ratio_native": "4:3"}
        })
        d = plan["decisions"][0]
        assert d["decision"] == "pillarbox"
        assert "narrower" in d["reason"]
        assert "pad" in d["ffmpeg_filter"]

    def test_239_in_16_9_letterboxes(self):
        sl = self._shot_list([{"id": "s1", "act": 1, "start_frame": 0,
                                "duration_frames": 24, "source_id": "film",
                                "source_timecode": "0:00:00.000", "role": "hero"}])
        plan = build_aspect_plan(sl, target_ar="16:9", source_profiles={
            "film": {"aspect_ratio_native": "2.39:1"}
        })
        d = plan["decisions"][0]
        assert d["decision"] == "letterbox"
        assert "wider" in d["reason"]

    def test_ar_change_count_tracks_transitions(self):
        sl = self._shot_list([
            {"id": "s1", "act": 1, "start_frame": 0, "duration_frames": 24,
             "source_id": "a", "source_timecode": "0:00:00.000", "role": "hero"},
            {"id": "s2", "act": 1, "start_frame": 24, "duration_frames": 24,
             "source_id": "b", "source_timecode": "0:00:00.000", "role": "hero"},
            {"id": "s3", "act": 1, "start_frame": 48, "duration_frames": 24,
             "source_id": "a", "source_timecode": "0:00:00.000", "role": "hero"},
        ])
        plan = build_aspect_plan(sl, target_ar="16:9", source_profiles={
            "a": {"aspect_ratio_native": "4:3"},
            "b": {"aspect_ratio_native": "2.39:1"},
        })
        assert plan["summary"]["ar_change_count"] == 2

    def test_no_profile_defaults_to_no_op(self):
        sl = self._shot_list([{"id": "s1", "act": 1, "start_frame": 0,
                                "duration_frames": 24, "source_id": "unknown",
                                "source_timecode": "0:00:00.000", "role": "hero"}])
        plan = build_aspect_plan(sl, target_ar="16:9", source_profiles={})
        # Without a profile, source_ar defaults to target_ar so decision is none
        assert plan["decisions"][0]["decision"] == "none"


class TestSummary:
    def test_summary_counts(self):
        sl = {
            "schema_version": 1, "project_slug": "demo", "fps": 24,
            "resolution": {"width": 1920, "height": 1080},
            "shots": [
                {"id": f"s{i+1}", "act": 1, "start_frame": i * 24,
                 "duration_frames": 24, "source_id": s,
                 "source_timecode": "0:00:00.000", "role": "hero"}
                for i, s in enumerate(["a", "a", "b", "c"])
            ],
        }
        plan = build_aspect_plan(sl, target_ar="16:9", source_profiles={
            "a": {"aspect_ratio_native": "16:9"},
            "b": {"aspect_ratio_native": "4:3"},
            "c": {"aspect_ratio_native": "2.39:1"},
        })
        s = plan["summary"]
        assert s["no_op_count"] == 2
        assert s["pillarbox_count"] == 1
        assert s["letterbox_count"] == 1


class TestSchemaCompliance:
    def test_output_validates(self):
        sl = {
            "schema_version": 1, "project_slug": "x", "fps": 24,
            "resolution": {"width": 1920, "height": 1080},
            "shots": [{"id": "s1", "act": 1, "start_frame": 0,
                       "duration_frames": 24, "source_id": "src",
                       "source_timecode": "0:00:00.000", "role": "hero"}],
        }
        plan = build_aspect_plan(sl, target_ar="16:9", source_profiles={
            "src": {"aspect_ratio_native": "4:3"}
        })
        validate(plan, "aspect-plan")
