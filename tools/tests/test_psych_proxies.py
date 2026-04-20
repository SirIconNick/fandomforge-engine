"""Tests for the psychology proxy telemetry (Phase 5.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fandomforge.intelligence.psych_proxies import (
    build_report,
    load_history,
    write_report,
    _heart_rate_band,
    _eyeline_to_camera_pct,
    _character_screen_time,
    _color_grouping_pct,
    _fandom_diversity,
    _beat_sync_pct,
)
from fandomforge.validation import validate


def _shot(**overrides) -> dict:
    base = {
        "id": "s001", "act": 1,
        "start_frame": 0, "duration_frames": 24,
        "source_id": "src", "source_timecode": "0:00:00.000",
        "role": "hero",
    }
    base.update(overrides)
    return base


class TestHeartRateBand:
    def test_resting(self):
        assert _heart_rate_band(60) == "resting"

    def test_calm(self):
        assert _heart_rate_band(80) == "calm"

    def test_active(self):
        assert _heart_rate_band(120) == "active"

    def test_hype(self):
        assert _heart_rate_band(150) == "hype"

    def test_frantic(self):
        assert _heart_rate_band(180) == "frantic"

    def test_off_band(self):
        assert _heart_rate_band(20) == "off-band"


class TestProxyHelpers:
    def test_eyeline_to_camera_pct(self):
        shots = [
            _shot(eyeline="camera"),
            _shot(eyeline=""),
            _shot(eyeline="camera"),
            _shot(eyeline="left"),
        ]
        assert _eyeline_to_camera_pct(shots) == 50.0

    def test_character_screen_time_sums_per_character(self):
        shots = [
            _shot(characters=["Leon"], duration_frames=48),
            _shot(characters=["Leon", "Ada"], duration_frames=24),
            _shot(characters=["Ada"], duration_frames=24),
        ]
        times = _character_screen_time(shots, fps=24.0)
        assert times["Leon"] == 3.0
        assert times["Ada"] == 2.0

    def test_character_screen_time_falls_back_to_fandom(self):
        shots = [
            _shot(fandom="Resident Evil", duration_frames=48),
        ]
        times = _character_screen_time(shots, fps=24.0)
        assert times["Resident Evil"] == 2.0

    def test_color_grouping_pct(self):
        shots = [
            _shot(color_notes="luma=0.50"),
            _shot(color_notes="luma=0.55"),
            _shot(color_notes="luma=0.92"),
            _shot(color_notes="luma=0.20"),
        ]
        # pairs: (0.50,0.55) Δ=0.05 grouped; (0.55,0.92) Δ=0.37 not; (0.92,0.20) Δ=0.72 not
        # 1/3 = 33.3%
        assert _color_grouping_pct(shots) == 33.3

    def test_fandom_diversity_two_equal(self):
        shots = [
            _shot(fandom="A", duration_frames=24),
            _shot(fandom="B", duration_frames=24),
        ]
        times, div = _fandom_diversity(shots, fps=24.0)
        assert div == 1.0  # max entropy for 2 equal classes

    def test_fandom_diversity_one_dominant(self):
        shots = [
            _shot(fandom="A", duration_frames=240),
            _shot(fandom="B", duration_frames=24),
        ]
        _, div = _fandom_diversity(shots, fps=24.0)
        assert div < 0.7

    def test_beat_sync_pct(self):
        shots = [
            _shot(start_frame=0,
                  beat_sync={"type": "beat", "index": 0, "time_sec": 0.0}),
            _shot(start_frame=24,
                  beat_sync={"type": "beat", "index": 1, "time_sec": 1.0}),  # exact
            _shot(start_frame=10,
                  beat_sync={"type": "beat", "index": 2, "time_sec": 1.0}),  # 10 frames off → fail
            _shot(start_frame=0,
                  beat_sync={"type": "free"}),  # ignored
        ]
        # 2 of 3 relevant aligned → 66.7%
        assert _beat_sync_pct(shots, fps=24.0) == 66.7


class TestBuildReport:
    def test_minimal_project_produces_valid_report(self, tmp_path: Path):
        # No data files at all
        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        report = build_report(project_dir)
        validate(report, "psychology-report")
        # Empty proxies
        assert report["proxies"]["parasocial"]["character_screen_time_sec"] == {}

    def test_build_from_real_artifacts(self, tmp_path: Path):
        project_dir = tmp_path / "demo"
        (project_dir / "data").mkdir(parents=True)
        # Minimal shot list with characters + beat sync
        shot_list = {
            "schema_version": 1,
            "project_slug": "demo",
            "fps": 24,
            "resolution": {"width": 1920, "height": 1080},
            "shots": [
                {**_shot(id="s001",
                         characters=["Leon Kennedy"],
                         fandom="Resident Evil",
                         beat_sync={"type": "beat", "index": 0, "time_sec": 0.0},
                         color_notes="luma=0.40")},
                {**_shot(id="s002", start_frame=48,
                         characters=["Leon Kennedy"],
                         fandom="Resident Evil",
                         color_notes="luma=0.42",
                         eyeline="camera")},
            ],
        }
        (project_dir / "data" / "shot-list.json").write_text(json.dumps(shot_list))
        beat_map = {"bpm": 128, "duration_sec": 60}
        (project_dir / "data" / "beat-map.json").write_text(json.dumps(beat_map))
        intent = {"edit_type": "tribute"}
        (project_dir / "data" / "intent.json").write_text(json.dumps(intent))

        report = build_report(project_dir)
        validate(report, "psychology-report")
        assert report["edit_type"] == "tribute"
        assert "Leon Kennedy" in report["proxies"]["parasocial"]["character_screen_time_sec"]
        assert report["proxies"]["beat_entrainment"]["song_bpm"] == 128.0
        assert report["proxies"]["beat_entrainment"]["heart_rate_band"] == "active"
        assert report["proxies"]["parasocial"]["eyeline_to_camera_pct"] == 50.0


class TestWriteAndHistory:
    def test_write_appends_history(self, tmp_path: Path):
        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        for i in range(3):
            report = build_report(project_dir)
            report["generated_at"] = f"2026-04-19T22:00:0{i}+00:00"
            write_report(report, project_dir)
        history = load_history(project_dir, limit=10)
        assert len(history) == 3

    def test_history_respects_limit(self, tmp_path: Path):
        project_dir = tmp_path / "demo"
        project_dir.mkdir()
        for i in range(5):
            r = build_report(project_dir)
            r["generated_at"] = f"2026-04-19T22:00:0{i}+00:00"
            write_report(r, project_dir)
        history = load_history(project_dir, limit=2)
        assert len(history) == 2
