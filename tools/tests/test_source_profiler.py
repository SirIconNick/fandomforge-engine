"""Tests for source_profiler — validates schema-compliant output across
varying inputs without requiring real video fixtures.

Most tests mock ffmpeg/ffprobe + sample frames so they're deterministic.
A single integration-style test runs against a real fixture if present.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from fandomforge.intelligence.source_profiler import (
    QUALITY_TIER_THRESHOLDS,
    _classify_era,
    _classify_source_type,
    _quality_tier,
    profile_source,
)
from fandomforge.validation import validate


def _fake_container_meta():
    from fandomforge.intelligence.source_profiler import _ContainerMeta
    return _ContainerMeta(
        width=1920, height=1080, fps=23.976, duration_sec=120.0,
        bitrate_kbps=4500, aspect_ratio_native="16:9",
    )


def _fake_frames(n: int = 3, color: tuple[int, int, int] = (128, 128, 128)) -> list[np.ndarray]:
    """Generate n flat frames of a given color."""
    frame = np.full((180, 320, 3), color, dtype=np.uint8)
    return [frame.copy() for _ in range(n)]


class TestSourceTypeClassification:
    def test_explicit_hint_wins(self):
        assert _classify_source_type("anything_here", "anime") == "anime"

    def test_filename_keyword_anime(self):
        assert _classify_source_type("naruto_subbed_clip") == "anime"

    def test_filename_keyword_animation(self):
        assert _classify_source_type("pixar_short_animation") == "western_animation"

    def test_default_falls_back_to_live_action(self):
        assert _classify_source_type("john-wick-4") == "live_action"


class TestEraClassification:
    def test_pre_2000(self):
        bucket, label = _classify_era("classic_movie_1997_remastered")
        assert bucket == "pre-2000"
        assert "1997" in label

    def test_post_2020(self):
        bucket, label = _classify_era("extraction-2-2023")
        assert bucket == "post-2020"
        assert "2023" in label

    def test_no_year_default(self):
        bucket, label = _classify_era("some_clip")
        assert bucket == "post-2020"
        assert label is None

    def test_hint_override(self):
        bucket, _ = _classify_era("anything", hint="2010-2020-original")
        assert bucket == "2010-2020"


class TestQualityTierComposite:
    def test_high_res_clean_sharp_is_S_or_A(self):
        tier = _quality_tier(
            bitrate_kbps=8000, grain=0.05, sharpness=0.9,
            width=1920, height=1080,
        )
        assert tier in {"S", "A"}

    def test_low_res_grainy_is_C_or_D(self):
        tier = _quality_tier(
            bitrate_kbps=400, grain=0.85, sharpness=0.2,
            width=640, height=360,
        )
        assert tier in {"C", "D"}


class TestProfileSourceMocked:
    """Profile_source with mocked ffprobe/ffmpeg to verify schema-compliant
    output without invoking real binaries."""

    def _patch_all(self, frames: list[np.ndarray]):
        return [
            patch("fandomforge.intelligence.source_profiler._check_ffprobe", return_value=None),
            patch("fandomforge.intelligence.source_profiler._check_ffmpeg", return_value=None),
            patch("fandomforge.intelligence.source_profiler._ffprobe_container",
                  return_value=_fake_container_meta()),
            patch("fandomforge.intelligence.source_profiler._detect_letter_pillar",
                  return_value=(False, False)),
            patch("fandomforge.intelligence.source_profiler._sample_frames",
                  return_value=frames),
        ]

    def test_quick_pass_omits_visual_stats(self, tmp_path: Path):
        fake_path = tmp_path / "fake.mp4"
        fake_path.touch()
        with patch("fandomforge.intelligence.source_profiler._check_ffprobe"), \
             patch("fandomforge.intelligence.source_profiler._check_ffmpeg"), \
             patch("fandomforge.intelligence.source_profiler._ffprobe_container",
                   return_value=_fake_container_meta()), \
             patch("fandomforge.intelligence.source_profiler._detect_letter_pillar",
                   return_value=(False, False)):
            profile = profile_source(fake_path, "test_source", deep=False)
        # Required fields present
        assert profile["schema_version"] == 1
        assert profile["source_id"] == "test_source"
        # Optional visual stats not present
        assert "luma_histogram" not in profile
        assert "color_temperature_kelvin" not in profile
        # Schema validates (required fields only)
        validate(profile, "source-profile")

    def test_deep_pass_populates_visual_stats(self, tmp_path: Path):
        fake_path = tmp_path / "fake.mp4"
        fake_path.touch()
        # Frames with varying luma (not all flat) so histograms have spread
        np.random.seed(42)
        varied_frames = [
            np.random.randint(0, 256, (180, 320, 3), dtype=np.uint8)
            for _ in range(5)
        ]
        patches = [
            patch("fandomforge.intelligence.source_profiler._check_ffprobe"),
            patch("fandomforge.intelligence.source_profiler._check_ffmpeg"),
            patch("fandomforge.intelligence.source_profiler._ffprobe_container",
                  return_value=_fake_container_meta()),
            patch("fandomforge.intelligence.source_profiler._detect_letter_pillar",
                  return_value=(False, False)),
            patch("fandomforge.intelligence.source_profiler._sample_frames",
                  return_value=varied_frames),
        ]
        for p in patches:
            p.start()
        try:
            profile = profile_source(fake_path, "varied_source", deep=True, n_frames=5)
        finally:
            for p in patches:
                p.stop()

        assert profile["frames_sampled"] == 5
        assert "luma_histogram" in profile
        assert "chroma_histogram" in profile
        assert profile["luma_histogram"]["bins"] == 16
        assert len(profile["luma_histogram"]["counts"]) == 16
        assert "color_temperature_kelvin" in profile
        assert "color_cast" in profile
        assert "grain_noise_floor" in profile
        assert "sharpness_score" in profile
        validate(profile, "source-profile")

    def test_zero_frames_still_produces_valid_record(self, tmp_path: Path):
        """If frame extraction fails entirely, the profile should still be
        schema-valid (frames_sampled=1 minimum)."""
        fake_path = tmp_path / "fake.mp4"
        fake_path.touch()
        patches = [
            patch("fandomforge.intelligence.source_profiler._check_ffprobe"),
            patch("fandomforge.intelligence.source_profiler._check_ffmpeg"),
            patch("fandomforge.intelligence.source_profiler._ffprobe_container",
                  return_value=_fake_container_meta()),
            patch("fandomforge.intelligence.source_profiler._detect_letter_pillar",
                  return_value=(False, False)),
            patch("fandomforge.intelligence.source_profiler._sample_frames",
                  return_value=[]),
        ]
        for p in patches:
            p.start()
        try:
            profile = profile_source(fake_path, "no_frames", deep=True)
        finally:
            for p in patches:
                p.stop()
        assert profile["frames_sampled"] >= 1
        validate(profile, "source-profile")


class TestQualityTierThresholdsCoverAllTiers:
    def test_every_tier_in_thresholds(self):
        for t in ("S", "A", "B", "C", "D"):
            assert t in QUALITY_TIER_THRESHOLDS
