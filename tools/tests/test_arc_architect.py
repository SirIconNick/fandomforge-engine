"""Tests for the arc architect (Phase 2.1)."""

from __future__ import annotations

import pytest

from fandomforge.intelligence.arc_architect import (
    ARC_TEMPLATES,
    LONG_EDIT_THRESHOLD_SEC,
    SHORT_EDIT_THRESHOLD_SEC,
    build_acts,
    shot_duration_band,
)


def _intent(edit_type: str = "action", duration: float = 60.0) -> dict:
    return {
        "schema_version": 1,
        "prompt_text": "test",
        "edit_type": edit_type,
        "edit_type_source": "explicit",
        "tone_vector": [0.0] * 8,
        "speakers": [],
        "auto_template": edit_type,
        "target_duration_sec": duration,
        "duration_source": "default",
        "fandoms": [],
        "confidence": 0.9,
        "needs_user_confirmation": False,
        "generated_at": "2026-04-19T00:00:00Z",
        "generator": "test",
    }


class TestTemplateCoverage:
    def test_all_v2_edit_types_have_templates(self):
        # Existing 8 + 3 new from intent classifier
        for t in ("action", "emotional", "tribute", "shipping", "speed_amv",
                  "cinematic", "comedy", "hype_trailer",
                  "dialogue_narrative", "dance_movement", "sad_emotional"):
            assert t in ARC_TEMPLATES, f"missing template for {t}"

    def test_every_template_has_climax(self):
        for edit_type, acts in ARC_TEMPLATES.items():
            roles = [a.arc_role for a in acts]
            assert "climax" in roles, f"{edit_type} template missing climax"

    def test_every_template_duration_pcts_sum_to_one(self):
        for edit_type, acts in ARC_TEMPLATES.items():
            total = sum(a.duration_pct for a in acts)
            assert abs(total - 1.0) < 1e-3, f"{edit_type} pcts sum {total} != 1.0"


class TestBuildActs:
    def test_default_action_produces_4_acts(self):
        acts = build_acts(_intent("action", 60))
        assert len(acts) == 4
        assert acts[0]["number"] == 1
        assert acts[-1]["number"] == 4

    def test_acts_cover_full_duration(self):
        acts = build_acts(_intent("action", 222))
        assert acts[0]["start_sec"] == 0.0
        assert acts[-1]["end_sec"] == pytest.approx(222.0, abs=0.01)
        # Contiguous
        for prev, nxt in zip(acts[:-1], acts[1:]):
            assert nxt["start_sec"] == prev["end_sec"]

    def test_pacing_progression_action_climbs(self):
        acts = build_acts(_intent("action", 60))
        roles_by_act = {a["arc_role"]: a for a in acts}
        # Setup is medium, climax is frantic
        assert roles_by_act["setup"]["pacing"] == "medium"
        assert roles_by_act["climax"]["pacing"] == "frantic"

    def test_emotional_climax_is_medium_not_frantic(self):
        """Cross-type test (A3): same Act schema, different pacing per type."""
        acts = build_acts(_intent("emotional", 120))
        climax = next(a for a in acts if a["arc_role"] == "climax")
        assert climax["pacing"] == "medium"

    def test_dialogue_narrative_climax_is_medium(self):
        acts = build_acts(_intent("dialogue_narrative", 120))
        climax = next(a for a in acts if a["arc_role"] == "climax")
        assert climax["pacing"] == "medium"


class TestVariableLength:
    def test_short_edit_collapses_to_two_acts(self):
        acts = build_acts(_intent("action", 20))  # under SHORT_EDIT_THRESHOLD
        assert len(acts) <= 2
        assert acts[-1]["arc_role"] == "climax"

    def test_very_short_edit_collapses_to_one_beat(self):
        acts = build_acts(_intent("action", 10))
        assert len(acts) == 1
        assert acts[0]["arc_role"] == "climax"

    def test_long_edit_inserts_interlude(self):
        acts = build_acts(_intent("action", 300))  # over LONG_EDIT_THRESHOLD
        roles = [a["arc_role"] for a in acts]
        assert "interlude" in roles
        # Total still 1.0 worth of duration_pct (now ~5 acts)
        total = sum(a["end_sec"] - a["start_sec"] for a in acts)
        assert total == pytest.approx(300.0, abs=0.5)


class TestBeatAlignment:
    def test_climax_snaps_to_drop_when_within_tolerance(self):
        # Action template: setup 20% (0-12s), escalation 50% (12-42s), climax 20% (42-54s), release 10% (54-60s)
        # Drop at 45s should snap climax start from 42 → 45 (within 30% tolerance of climax span 12s = 3.6s)
        beat_map = {"drops": [{"time": 45.0, "intensity": 0.95, "type": "main_drop"}]}
        acts = build_acts(_intent("action", 60), beat_map=beat_map)
        climax = next(a for a in acts if a["arc_role"] == "climax")
        assert abs(climax["start_sec"] - 45.0) < 1.0, (
            f"climax start {climax['start_sec']} should snap near drop@45.0"
        )

    def test_climax_does_not_snap_when_drop_too_far(self):
        beat_map = {"drops": [{"time": 5.0, "intensity": 0.95, "type": "main_drop"}]}
        acts = build_acts(_intent("action", 60), beat_map=beat_map)
        climax = next(a for a in acts if a["arc_role"] == "climax")
        # Drop at 5s is way before planned climax (~42s), shouldn't snap
        assert climax["start_sec"] > 30.0


class TestShotDurationBand:
    def test_pacing_to_band_mapping(self):
        assert shot_duration_band("slow") == (2.0, 4.5)
        assert shot_duration_band("medium") == (1.0, 2.0)
        assert shot_duration_band("fast") == (0.5, 1.0)
        assert shot_duration_band("frantic") == (0.25, 0.6)

    def test_unknown_pacing_falls_back_to_medium(self):
        assert shot_duration_band("turbo-funk") == (1.0, 2.0)


class TestSchemaIntegration:
    def test_acts_validate_against_edit_plan_schema(self):
        from fandomforge.validation import validate
        acts = build_acts(_intent("action", 60))
        # Build a minimal edit-plan around the acts and validate the whole thing
        plan = {
            "schema_version": 1,
            "project_slug": "test",
            "concept": {"theme": "test", "one_sentence": "this is a test concept"},
            "song": {"title": "T", "artist": "A"},
            "fandoms": [{"name": "F"}],
            "vibe": "action",
            "length_seconds": 60.0,
            "platform_target": "youtube",
            "acts": acts,
        }
        validate(plan, "edit-plan")
