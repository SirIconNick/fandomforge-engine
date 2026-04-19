"""Tests for the deep reference-video analyzer.

Heavy pieces (scenedetect, librosa, opencv) require real video input, so
these tests exercise the pure-logic helpers: duration stats, pacing curve,
act pacing. The end-to-end pipeline is smoke-tested in test_reference_library.
"""

from __future__ import annotations

import pytest

from fandomforge.intelligence.reference_analyzer_deep import (
    _act_pacing_pct,
    _duration_stats,
    _pacing_curve,
)


class TestDurationStats:
    def test_empty_returns_zero_count(self) -> None:
        assert _duration_stats([]) == {"shot_count": 0}

    def test_single_shot(self) -> None:
        stats = _duration_stats([(0.0, 5.0)])
        assert stats["shot_count"] == 1
        assert stats["avg_shot_duration_sec"] == 5.0
        assert stats["cuts_per_minute"] == 12.0  # 1 shot in 5s = 12 per minute

    def test_percentiles(self) -> None:
        # 10 shots of increasing duration: 1, 2, ..., 10s (total 55s)
        boundaries = []
        start = 0.0
        for dur in range(1, 11):
            boundaries.append((start, start + dur))
            start += dur
        stats = _duration_stats(boundaries)
        assert stats["shot_count"] == 10
        assert stats["min_shot_duration_sec"] == 1.0
        assert stats["max_shot_duration_sec"] == 10.0
        # p25 and p75 come from the sorted list [1..10]
        assert stats["shot_duration_p25"] <= stats["shot_duration_p75"]


class TestPacingCurve:
    def test_empty_returns_empty(self) -> None:
        assert _pacing_curve([]) == []

    def test_windows_cover_full_duration(self) -> None:
        boundaries = [(i * 2.0, i * 2.0 + 2.0) for i in range(30)]  # 60s, 2s shots
        curve = _pacing_curve(boundaries, window_sec=30.0, step_sec=15.0)
        assert len(curve) >= 3
        assert curve[0]["t_sec"] == 0.0
        assert all(c["cpm"] >= 0 for c in curve)

    def test_fast_section_shows_high_cpm(self) -> None:
        # First 30s: slow (5s shots → 12 cpm). Last 30s: fast (1s shots → 60 cpm).
        boundaries = []
        for i in range(6):
            boundaries.append((i * 5.0, i * 5.0 + 5.0))
        start = 30.0
        for i in range(30):
            boundaries.append((start + i, start + i + 1))
        curve = _pacing_curve(boundaries, window_sec=30.0, step_sec=30.0)
        assert curve[1]["cpm"] > curve[0]["cpm"]


class TestActPacing:
    def test_empty_returns_even_thirds(self) -> None:
        assert _act_pacing_pct([]) == [33.3, 33.3, 33.3]

    def test_front_loaded_edit_skews_act1(self) -> None:
        # 10 shots in first third, 0 in others
        boundaries = [(i * 1.0, i * 1.0 + 0.9) for i in range(10)]
        boundaries.append((30.0, 30.5))  # make video 30s long
        pct = _act_pacing_pct(boundaries)
        assert pct[0] > pct[1]
        assert pct[0] > pct[2]

    def test_distribution_sums_to_100(self) -> None:
        boundaries = [(i * 1.0, i * 1.0 + 0.9) for i in range(30)]
        pct = _act_pacing_pct(boundaries)
        assert abs(sum(pct) - 100.0) < 0.5
