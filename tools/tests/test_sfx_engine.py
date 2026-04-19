"""Tests for the action SFX engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from fandomforge.intelligence.sfx_engine import (
    _sfx_kinds_for_shot,
    build_sfx_plan,
    default_sfx_library,
    resolve_sfx_file,
)


class TestSfxKinds:
    def test_punch_description_maps_to_punch(self) -> None:
        shot = {"description": "throws a heavy punch", "mood_tags": [], "role": "action"}
        assert "punch" in _sfx_kinds_for_shot(shot)

    def test_gunshot_description_maps_to_gunshot(self) -> None:
        shot = {"description": "fires the pistol", "mood_tags": [], "role": "action"}
        assert "gunshot" in _sfx_kinds_for_shot(shot)

    def test_generic_action_falls_back_to_impact(self) -> None:
        shot = {"description": "", "mood_tags": ["action", "intense"], "role": "action"}
        assert "impact" in _sfx_kinds_for_shot(shot)

    def test_non_action_shot_has_no_sfx(self) -> None:
        shot = {"description": "", "mood_tags": ["calm"], "role": "environment"}
        assert _sfx_kinds_for_shot(shot) == []


class TestBuildSfxPlan:
    def _shot(self, id: str, desc: str, *, start_frame: int = 0,
              mood: list[str] | None = None, role: str = "action") -> dict:
        return {
            "id": id, "act": 1, "start_frame": start_frame,
            "duration_frames": 24, "source_id": "src1",
            "source_timecode": "0:00:01.000", "role": role,
            "description": desc, "mood_tags": mood or [],
        }

    def test_generates_events_for_action_shots(self) -> None:
        shot_list = {"shots": [self._shot("s1", "throws punch")]}
        plan = build_sfx_plan(project_slug="t", shot_list=shot_list, seed=42)
        assert plan["schema_version"] == 1
        assert len(plan["events"]) >= 1
        assert plan["events"][0]["kind"] == "punch"

    def test_variant_rotation_picks_different_files(self) -> None:
        # Two punch shots should produce two different variant filenames.
        shot_list = {
            "shots": [
                self._shot("s1", "punch one", start_frame=0),
                self._shot("s2", "punch two", start_frame=48),
                self._shot("s3", "punch three", start_frame=96),
            ]
        }
        plan = build_sfx_plan(project_slug="t", shot_list=shot_list, seed=42)
        variants = [e["variant"] for e in plan["events"] if e["kind"] == "punch"]
        # At least 2 distinct variants (library has 5 punch variants)
        assert len(set(variants)) >= 2

    def test_drops_add_sub_boom(self) -> None:
        shot_list = {"shots": []}
        beat_map = {"drops": [{"time": 30.0, "intensity": 0.9, "type": "main"}]}
        plan = build_sfx_plan(project_slug="t", shot_list=shot_list, beat_map=beat_map)
        sub_boom_events = [e for e in plan["events"] if e["kind"] == "sub_boom"]
        assert len(sub_boom_events) == 1
        assert sub_boom_events[0]["time_sec"] == 30.0

    def test_scene_audio_defaults_enabled(self) -> None:
        plan = build_sfx_plan(project_slug="t", shot_list={"shots": []})
        assert plan["scene_audio_blend"]["enabled"] is True
        assert plan["scene_audio_blend"]["gain_db"] == -20.0

    def test_beat_snap(self) -> None:
        shot_list = {
            "shots": [
                self._shot("s1", "throws punch", start_frame=0),  # ~= 0.0s
            ]
        }
        # Put a beat at 0.05s (within the 0.15s snap window of the 0.0s shot start)
        beat_map = {"beats": [0.05, 0.5, 1.0]}
        plan = build_sfx_plan(project_slug="t", shot_list=shot_list, beat_map=beat_map, seed=1)
        punch = next(e for e in plan["events"] if e["kind"] == "punch")
        assert punch["beat_aligned"] is True
        assert abs(punch["time_sec"] - 0.05) < 1e-6


class TestResolveSfxFile:
    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert resolve_sfx_file("nope.wav", "punch", tmp_path) is None

    def test_finds_project_local_kind_dir(self, tmp_path: Path) -> None:
        target = tmp_path / "sfx" / "punch" / "punch-heavy-01.wav"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"")
        found = resolve_sfx_file("punch-heavy-01.wav", "punch", tmp_path)
        assert found == target
