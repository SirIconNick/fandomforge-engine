"""Tests for the emotion-arc inference module."""

from __future__ import annotations

from fandomforge.intelligence.emotion_arc import (
    DIMENSIONS, detect_dead_zones, infer_arc,
)
from fandomforge.validation import validate


def _shot(**kw):
    return {
        "id": kw.get("id", "s001"),
        "act": kw.get("act", 1),
        "start_frame": kw.get("start_frame", 0),
        "duration_frames": kw.get("duration_frames", 24),
        "source_id": kw.get("source_id", "src"),
        "source_timecode": kw.get("source_timecode", "0:00:00.000"),
        "role": kw.get("role", "action"),
        "mood_tags": kw.get("mood_tags", []),
        "scores": kw.get("scores", {}),
        "beat_sync": kw.get("beat_sync", {}),
    }


def _shot_list(shots):
    return {
        "schema_version": 1,
        "project_slug": "test",
        "fps": 24,
        "resolution": {"width": 1920, "height": 1080},
        "shots": shots,
    }


def test_arc_output_is_schema_valid():
    arc = infer_arc(_shot_list([_shot(role="hero", mood_tags=["triumph"])]))
    validate(arc, "emotion-arc")


def test_arc_uses_all_dimensions():
    arc = infer_arc(_shot_list([_shot()]))
    assert arc["dimensions"] == list(DIMENSIONS)


def test_hero_shot_boosts_triumph():
    arc = infer_arc(_shot_list([_shot(role="hero", mood_tags=["triumph"])]))
    sample = arc["samples"][0]
    triumph_idx = DIMENSIONS.index("triumph")
    assert sample["vector"][triumph_idx] > 0.5
    assert sample["dominant"] == "triumph"


def test_grief_tag_boosts_grief_dimension():
    arc = infer_arc(_shot_list([_shot(role="reaction", mood_tags=["mentor-loss"])]))
    sample = arc["samples"][0]
    grief_idx = DIMENSIONS.index("grief")
    assert sample["vector"][grief_idx] > 0.5


def test_empty_mood_tags_still_produces_vector_from_role():
    arc = infer_arc(_shot_list([_shot(role="action", mood_tags=[])]))
    sample = arc["samples"][0]
    assert max(sample["vector"]) > 0


def test_intensity_in_range():
    arc = infer_arc(_shot_list([
        _shot(id="s1", role="hero", mood_tags=["triumph"]),
        _shot(id="s2", role="reaction", mood_tags=["grief"]),
    ]))
    for s in arc["samples"]:
        assert 0.0 <= s["intensity"] <= 1.0


def test_detect_dead_zones_flat_intensity():
    arc = {
        "samples": [
            {"shot_id": "s1", "time_sec": 0, "vector": [0] * 8, "intensity": 0.3},
            {"shot_id": "s2", "time_sec": 10, "vector": [0] * 8, "intensity": 0.31},
            {"shot_id": "s3", "time_sec": 25, "vector": [0] * 8, "intensity": 0.29},
            {"shot_id": "s4", "time_sec": 40, "vector": [0] * 8, "intensity": 0.9},
        ]
    }
    zones = detect_dead_zones(arc, min_gap_sec=20.0, flat_tolerance=0.05)
    assert len(zones) == 1
    assert zones[0][0] == 0
    assert zones[0][1] == 25


def test_detect_dead_zones_none_when_variance_high():
    arc = {
        "samples": [
            {"shot_id": "s1", "time_sec": 0, "vector": [0] * 8, "intensity": 0.1},
            {"shot_id": "s2", "time_sec": 5, "vector": [0] * 8, "intensity": 0.9},
            {"shot_id": "s3", "time_sec": 10, "vector": [0] * 8, "intensity": 0.3},
        ]
    }
    zones = detect_dead_zones(arc, min_gap_sec=5.0, flat_tolerance=0.05)
    assert zones == []


def test_time_sec_comes_from_start_frame_when_present():
    arc = infer_arc(_shot_list([_shot(start_frame=48)]))
    # 48 frames at 24fps = 2.0s
    assert arc["samples"][0]["time_sec"] == 2.0
