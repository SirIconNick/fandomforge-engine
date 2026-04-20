"""Tests for the quality-gap mitigator (Phase 3.2)."""

from __future__ import annotations

import pytest

from fandomforge.intelligence.quality_filler import (
    quality_distribution,
    treat_all,
    treat_source,
)


def _profile(tier: str, sid: str = "src") -> dict:
    return {"source_id": sid, "quality_tier": tier}


class TestTierTreatment:
    def test_s_tier_no_op(self):
        t = treat_source(_profile("S"))
        assert t.ffmpeg_filter == ""
        assert not t.flagged_for_review
        assert not t.refused

    def test_b_tier_light_denoise(self):
        t = treat_source(_profile("B"))
        assert "hqdn3d" in t.ffmpeg_filter
        assert "unsharp" not in t.ffmpeg_filter
        assert not t.flagged_for_review

    def test_c_tier_denoise_unsharp_flagged(self):
        t = treat_source(_profile("C"))
        assert "hqdn3d" in t.ffmpeg_filter
        assert "unsharp" in t.ffmpeg_filter
        assert t.flagged_for_review

    def test_d_tier_default_refused(self):
        t = treat_source(_profile("D"))
        assert t.refused
        assert t.flagged_for_review
        assert "refused" in t.reason

    def test_d_tier_allowed_when_flag_set(self):
        t = treat_source(_profile("D"), allow_dtier=True)
        assert not t.refused
        assert "hqdn3d" in t.ffmpeg_filter
        assert "unsharp" in t.ffmpeg_filter
        assert t.flagged_for_review


class TestDistribution:
    def test_quality_distribution(self):
        profiles = [
            _profile("S", "a"), _profile("A", "b"), _profile("A", "c"),
            _profile("B", "d"), _profile("D", "e"),
        ]
        dist = quality_distribution(profiles)
        assert dist == {"S": 1, "A": 2, "B": 1, "C": 0, "D": 1}


class TestTreatAll:
    def test_treats_each_source(self):
        profiles = [_profile("S", "a"), _profile("D", "b")]
        out = treat_all(profiles, allow_dtier=True)
        assert len(out) == 2
        assert out[0]["refused"] is False
        assert out[1]["refused"] is False
