"""Tests for scene_enricher — avg_luma backfill on scenes.json."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fandomforge.intelligence.scene_enricher import (
    enrich_project,
    enrich_scenes,
)


def _write_scenes(path: Path, scenes: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 1,
        "source_id": "test-source",
        "detector": "adaptive",
        "threshold": 3.0,
        "scenes": scenes,
    }
    path.write_text(json.dumps(data))


class TestEnrichScenes:
    def test_returns_error_when_scenes_file_missing(self, tmp_path: Path):
        result = enrich_scenes(tmp_path / "nope.json", tmp_path / "video.mp4")
        assert not result["ok"]
        assert result["reason"] == "scenes_json_not_found"

    def test_returns_error_when_video_missing(self, tmp_path: Path):
        scenes_path = tmp_path / "scenes.json"
        _write_scenes(scenes_path, [{"index": 0, "start_sec": 0.0, "end_sec": 2.0}])
        result = enrich_scenes(scenes_path, tmp_path / "missing.mp4")
        assert not result["ok"]
        assert result["reason"] == "video_not_found"

    def test_skips_when_all_scenes_already_enriched(self, tmp_path: Path):
        scenes_path = tmp_path / "scenes.json"
        video = tmp_path / "v.mp4"
        video.write_bytes(b"fake")
        _write_scenes(scenes_path, [
            {"index": 0, "start_sec": 0.0, "end_sec": 2.0, "avg_luma": 0.4},
            {"index": 1, "start_sec": 2.0, "end_sec": 4.0, "avg_luma": 0.5},
        ])
        # Mock ffmpeg available so the short-circuit hits before video probe
        with patch(
            "fandomforge.intelligence.scene_enricher._ffmpeg_available",
            return_value=True,
        ):
            result = enrich_scenes(scenes_path, video)
        assert result["ok"]
        assert result["skipped"]
        assert result["scenes"] == 2

    def test_enriches_missing_luma(self, tmp_path: Path):
        """Scene without avg_luma should get one written back after sampling."""
        scenes_path = tmp_path / "scenes.json"
        video = tmp_path / "v.mp4"
        video.write_bytes(b"fake")
        _write_scenes(scenes_path, [
            {"index": 0, "start_sec": 0.0, "end_sec": 2.0},
        ])

        def fake_sample_frame(_v, _t, frame_path, **_kw):
            frame_path.write_bytes(b"fakeframe")
            return True

        def fake_frame_stats(_p):
            return {"luma": 0.37, "hue_median_deg": 120.0, "saturation_mean": 0.5}

        with patch(
            "fandomforge.intelligence.scene_enricher._ffmpeg_available",
            return_value=True,
        ), patch(
            "fandomforge.intelligence.scene_enricher._sample_frame",
            side_effect=fake_sample_frame,
        ), patch(
            "fandomforge.intelligence.scene_enricher._frame_stats",
            side_effect=fake_frame_stats,
        ):
            result = enrich_scenes(scenes_path, video)

        assert result["ok"], f"expected ok, got {result}"
        assert result["enriched"] == 1
        data = json.loads(scenes_path.read_text())
        assert data["scenes"][0]["avg_luma"] == 0.37
        assert data["scenes"][0]["peak_luma"] == 0.37

    def test_force_recomputes_existing_luma(self, tmp_path: Path):
        scenes_path = tmp_path / "scenes.json"
        video = tmp_path / "v.mp4"
        video.write_bytes(b"fake")
        _write_scenes(scenes_path, [
            {"index": 0, "start_sec": 0.0, "end_sec": 2.0, "avg_luma": 0.99},
        ])

        def fake_sample_frame(_v, _t, frame_path, **_kw):
            frame_path.write_bytes(b"fakeframe")
            return True

        def fake_frame_stats(_p):
            return {"luma": 0.2, "hue_median_deg": 0.0, "saturation_mean": 0.0}

        with patch(
            "fandomforge.intelligence.scene_enricher._ffmpeg_available",
            return_value=True,
        ), patch(
            "fandomforge.intelligence.scene_enricher._sample_frame",
            side_effect=fake_sample_frame,
        ), patch(
            "fandomforge.intelligence.scene_enricher._frame_stats",
            side_effect=fake_frame_stats,
        ):
            result = enrich_scenes(scenes_path, video, force=True)

        assert result["ok"]
        assert result["enriched"] == 1
        data = json.loads(scenes_path.read_text())
        assert data["scenes"][0]["avg_luma"] == 0.2

    def test_handles_sample_failure_gracefully(self, tmp_path: Path):
        """If every sample_frame call fails, the scene is counted as failed
        but we don't crash; other scenes still proceed."""
        scenes_path = tmp_path / "scenes.json"
        video = tmp_path / "v.mp4"
        video.write_bytes(b"fake")
        _write_scenes(scenes_path, [
            {"index": 0, "start_sec": 0.0, "end_sec": 2.0},
            {"index": 1, "start_sec": 2.0, "end_sec": 4.0},
        ])

        call_count = {"n": 0}

        def flaky_sample(_v, _t, frame_path, **_kw):
            # First scene fails all 3 samples; second succeeds.
            call_count["n"] += 1
            if call_count["n"] <= 3:
                return False
            frame_path.write_bytes(b"ok")
            return True

        def fake_stats(_p):
            return {"luma": 0.5, "hue_median_deg": 0.0, "saturation_mean": 0.0}

        with patch(
            "fandomforge.intelligence.scene_enricher._ffmpeg_available",
            return_value=True,
        ), patch(
            "fandomforge.intelligence.scene_enricher._sample_frame",
            side_effect=flaky_sample,
        ), patch(
            "fandomforge.intelligence.scene_enricher._frame_stats",
            side_effect=fake_stats,
        ):
            result = enrich_scenes(scenes_path, video)

        assert result["ok"]
        assert result["enriched"] == 1
        assert result["failed"] == 1

    def test_zero_duration_scene_counted_as_failed(self, tmp_path: Path):
        scenes_path = tmp_path / "scenes.json"
        video = tmp_path / "v.mp4"
        video.write_bytes(b"fake")
        _write_scenes(scenes_path, [
            {"index": 0, "start_sec": 5.0, "end_sec": 5.0},  # zero duration
        ])
        with patch(
            "fandomforge.intelligence.scene_enricher._ffmpeg_available",
            return_value=True,
        ):
            result = enrich_scenes(scenes_path, video)
        assert result["ok"]
        assert result["enriched"] == 0
        assert result["failed"] == 1


class TestEnrichProject:
    def test_fails_when_catalog_missing(self, tmp_path: Path):
        result = enrich_project(tmp_path)
        assert not result["ok"]
        assert result["reason"] == "catalog_not_found"

    def test_skips_sources_without_scene_files(self, tmp_path: Path):
        data = tmp_path / "data"
        data.mkdir()
        (data / "source-catalog.json").write_text(json.dumps({
            "sources": [
                {"id": "b2:abc", "path": "raw/fake.mp4"},
            ],
        }))
        # Don't create any scenes files
        with patch(
            "fandomforge.intelligence.scene_enricher._ffmpeg_available",
            return_value=True,
        ):
            result = enrich_project(tmp_path)
        assert result["ok"]
        assert result["sources_total"] == 1
        assert result["failed"] == 1
        assert result["details"][0]["reason"] == "no_scenes_file"

    def test_enriches_derived_scenes_path(self, tmp_path: Path):
        (tmp_path / "raw").mkdir()
        (tmp_path / "raw" / "fake.mp4").write_bytes(b"v")
        data = tmp_path / "data"
        data.mkdir()
        (data / "source-catalog.json").write_text(json.dumps({
            "sources": [{"id": "b2:abc", "path": "raw/fake.mp4"}],
        }))
        derived_scenes = tmp_path / "derived" / "b2:abc" / "scenes.json"
        _write_scenes(derived_scenes, [
            {"index": 0, "start_sec": 0.0, "end_sec": 2.0},
        ])

        def fake_sample(_v, _t, frame_path, **_kw):
            frame_path.write_bytes(b"ok")
            return True

        def fake_stats(_p):
            return {"luma": 0.42, "hue_median_deg": 0.0, "saturation_mean": 0.0}

        with patch(
            "fandomforge.intelligence.scene_enricher._ffmpeg_available",
            return_value=True,
        ), patch(
            "fandomforge.intelligence.scene_enricher._sample_frame",
            side_effect=fake_sample,
        ), patch(
            "fandomforge.intelligence.scene_enricher._frame_stats",
            side_effect=fake_stats,
        ):
            result = enrich_project(tmp_path)

        assert result["ok"]
        assert result["enriched"] == 1
        data_back = json.loads(derived_scenes.read_text())
        assert data_back["scenes"][0]["avg_luma"] == 0.42
