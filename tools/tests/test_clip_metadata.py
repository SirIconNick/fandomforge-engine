"""Tests for the Phase 1.3 clip metadata extractor."""

from __future__ import annotations

from pathlib import Path

import pytest

from fandomforge.intelligence.clip_metadata import (
    EMOTION_DIMS,
    coverage_report,
    enrich_shot,
    enrich_shot_list,
)
from fandomforge.validation import validate


def _basic_shot(**overrides) -> dict:
    base = {
        "id": "s001",
        "act": 1,
        "start_frame": 0,
        "duration_frames": 24,
        "source_id": "test_source",
        "source_timecode": "0:00:05.000",
        "role": "action",
        "mood_tags": ["combat"],
    }
    base.update(overrides)
    return base


class TestEnrichShotMinimal:
    def test_register_populated(self):
        out = enrich_shot(_basic_shot())
        assert isinstance(out["emotional_register"], list)
        assert len(out["emotional_register"]) == 8
        # combat → high tension
        idx = EMOTION_DIMS.index("tension")
        assert out["emotional_register"][idx] > 0.5

    def test_clip_category_picked(self):
        out = enrich_shot(_basic_shot(role="action", mood_tags=["combat"]))
        assert out["clip_category"] in {
            "action-mid", "action-high", "climactic", "transitional"
        }

    def test_visual_style_default_live_action(self):
        out = enrich_shot(_basic_shot())
        assert out["visual_style"] == "live_action"

    def test_visual_style_inherits_from_profile(self):
        out = enrich_shot(_basic_shot(), source_profile={"source_type": "anime"})
        assert out["visual_style"] == "anime"

    def test_dialogue_fields_null_when_no_transcript(self):
        out = enrich_shot(_basic_shot())
        assert out["dialogue_clarity_score"] is None
        assert out["lip_sync_confidence"] is None

    def test_action_intensity_default_when_no_scene_data(self):
        out = enrich_shot(_basic_shot())
        assert out["action_intensity_pct"] == 50.0

    def test_energy_zone_fit_3dim(self):
        out = enrich_shot(_basic_shot())
        ezf = out["energy_zone_fit"]
        assert isinstance(ezf, list) and len(ezf) == 3
        for v in ezf:
            assert 0.0 <= v <= 1.0


class TestCategoryRouting:
    def test_explicit_dialogue_mood_picks_dialogue_primary(self):
        out = enrich_shot(_basic_shot(mood_tags=["dialogue"]))
        assert out["clip_category"] == "dialogue-primary"

    def test_explicit_climax_mood_picks_climactic(self):
        out = enrich_shot(_basic_shot(mood_tags=["climax"]))
        assert out["clip_category"] == "climactic"

    def test_high_grief_picks_reaction_emotional(self):
        # Strong death tag → grief peaks → reaction-emotional
        out = enrich_shot(_basic_shot(mood_tags=["death", "loss"], role="reaction"))
        assert out["clip_category"] == "reaction-emotional"

    def test_explosion_mood_picks_action_high(self):
        out = enrich_shot(_basic_shot(mood_tags=["explosion"]))
        assert out["clip_category"] == "action-high"

    def test_role_fallback_when_no_strong_mood(self):
        out = enrich_shot(_basic_shot(role="environment", mood_tags=[]))
        assert out["clip_category"] == "establishing"


class TestActionIntensity:
    def test_scene_motion_drives_intensity(self):
        scene_data = {
            "scenes": [
                {"start_sec": 0.0, "end_sec": 10.0, "motion": 0.8},
            ],
        }
        out = enrich_shot(
            _basic_shot(source_timecode="0:00:05.000"),
            scene_data=scene_data,
            source_motion_baseline=0.4,
        )
        # 0.8 / 0.4 * 50 = 100
        assert out["action_intensity_pct"] == 100.0

    def test_low_motion_low_intensity(self):
        scene_data = {
            "scenes": [{"start_sec": 0.0, "end_sec": 10.0, "motion": 0.1}],
        }
        out = enrich_shot(
            _basic_shot(source_timecode="0:00:05.000"),
            scene_data=scene_data,
            source_motion_baseline=0.5,
        )
        # 0.1 / 0.5 * 50 = 10
        assert out["action_intensity_pct"] == 10.0


class TestDialogueClarity:
    def test_word_confidence_drives_clarity(self):
        transcript = {
            "words": [
                {"start_sec": 5.1, "end_sec": 5.3, "text": "hello", "confidence": 0.9},
                {"start_sec": 5.3, "end_sec": 5.5, "text": "world", "confidence": 0.7},
            ],
        }
        # default duration_frames=24 / 24fps = 1.0s; src_tc=5.0 → window 5.0-6.0
        out = enrich_shot(_basic_shot(), transcript=transcript)
        assert out["dialogue_clarity_score"] is not None
        assert 75 <= out["dialogue_clarity_score"] <= 90

    def test_no_words_in_window_returns_null(self):
        transcript = {"words": [{"start_sec": 100.0, "end_sec": 100.5, "text": "x", "confidence": 0.9}]}
        out = enrich_shot(_basic_shot(), transcript=transcript)
        assert out["dialogue_clarity_score"] is None


class TestAudioType:
    def test_dialogue_when_words_present(self):
        transcript = {
            "words": [
                {"start_sec": 5.1, "end_sec": 5.3, "text": "hi", "confidence": 0.9},
            ],
        }
        out = enrich_shot(_basic_shot(), transcript=transcript)
        assert out["audio_type"] == "dialogue_present"

    def test_sfx_when_explosion_mood(self):
        out = enrich_shot(_basic_shot(mood_tags=["explosion"]))
        assert out["audio_type"] == "sfx_only"

    def test_scene_audio_default(self):
        out = enrich_shot(_basic_shot(mood_tags=["combat"]))
        assert out["audio_type"] == "scene_audio"


class TestIdempotency:
    def test_existing_fields_preserved(self):
        s = _basic_shot()
        s["emotional_register"] = [0.0] * 8
        s["clip_category"] = "texture"
        out = enrich_shot(s)
        assert out["emotional_register"] == [0.0] * 8
        assert out["clip_category"] == "texture"


class TestSchemaIntegration:
    def test_enriched_shot_validates_in_shot_list(self):
        s = enrich_shot(_basic_shot())
        sl = {
            "schema_version": 1,
            "project_slug": "t",
            "fps": 24,
            "resolution": {"width": 1920, "height": 1080},
            "shots": [s],
        }
        validate(sl, "shot-list")


class TestCoverageReport:
    def test_full_coverage(self):
        s = enrich_shot(_basic_shot())
        sl = {"shots": [s]}
        report = coverage_report(sl)
        # All non-null fields populated
        assert report["emotional_register"] == 1.0
        assert report["clip_category"] == 1.0
        assert report["energy_zone_fit"] == 1.0
        # dialogue/lip nullable, no transcript → 0
        assert report["dialogue_clarity_score"] == 0.0


class TestEnrichShotList:
    def test_walks_all_shots(self, tmp_path: Path):
        sl = {
            "schema_version": 1,
            "project_slug": "t",
            "fps": 24,
            "resolution": {"width": 1920, "height": 1080},
            "shots": [
                _basic_shot(id="s001", source_id="src1"),
                _basic_shot(id="s002", source_id="src1"),
                _basic_shot(id="s003", source_id="src2"),
            ],
        }
        # No data files in tmp_path — extractor should still work with defaults
        out = enrich_shot_list(sl, tmp_path)
        assert len(out["shots"]) == 3
        for s in out["shots"]:
            assert "emotional_register" in s
            assert "clip_category" in s
        # Validates against shot-list schema
        validate(out, "shot-list")
