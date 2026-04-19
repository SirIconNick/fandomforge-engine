"""Composite quality score + quality_tier tests."""

from __future__ import annotations

from fandomforge.intelligence.reference_library import score_quality


class TestScoreQuality:
    def _video(self, **extras) -> dict:
        base = {
            "id": "x", "title": "t",
            "metrics": {
                "cuts_on_beat_pct": 60.0,
                "transitions": {"variety_entropy_normalized": 0.7},
                "motion_cuts": {"continuity_score": 70.0},
                "lyric_alignment": {
                    "available": True,
                    "cuts_on_word_boundary_pct": 55.0,
                    "cuts_on_phrase_boundary_pct": 35.0,
                },
            },
            "youtube_metadata": {
                "view_count": 5_000_000,
                "like_ratio": 0.03,
            },
        }
        base.update(extras)
        return base

    def test_full_signals_lands_in_mid_range(self) -> None:
        # Realistic mid-tier signals (60% beat-sync, 50% of p90 views, 3%
        # likes) should land in B-C band.
        v = self._video()
        q = score_quality(v, corpus_audience_reference=10_000_000)
        assert 55 <= q["quality_score"] <= 80
        assert q["quality_tier"] in ("B", "C")
        assert set(q["components"].keys()) == {
            "audience", "variety", "beat_sync", "lyric_sync", "motion", "approval",
        }

    def test_missing_lyric_signal_falls_to_neutral(self) -> None:
        # When whisper never ran, we don't know — neutral 50 instead of 0.
        v = self._video()
        v["metrics"]["lyric_alignment"] = {"available": False}
        q = score_quality(v, corpus_audience_reference=10_000_000)
        assert q["components"]["lyric_sync"] == 50.0

    def test_top_audience_floors_correctly(self) -> None:
        # view_count >= corpus reference → audience capped at 100
        v = self._video()
        v["youtube_metadata"]["view_count"] = 10_000_000
        q = score_quality(v, corpus_audience_reference=10_000_000)
        assert q["components"]["audience"] == 100.0

    def test_view_count_above_reference_still_caps_at_100(self) -> None:
        # Outlier video with way more views than the reference stays at 100
        v = self._video()
        v["youtube_metadata"]["view_count"] = 100_000_000
        q = score_quality(v, corpus_audience_reference=10_000_000)
        assert q["components"]["audience"] == 100.0

    def test_no_metadata_uses_neutral_fallbacks(self) -> None:
        v = self._video()
        v["youtube_metadata"] = {}
        q = score_quality(v, corpus_audience_reference=None)
        assert q["components"]["audience"] == 50.0
        assert q["components"]["approval"] == 50.0
        assert q["quality_score"] >= 0
        assert q["quality_tier"] in ("S", "A", "B", "C", "D")

    def test_all_zero_signals_scores_neutral_fallback(self) -> None:
        # Completely empty video gets all neutral fallbacks = 50 avg
        v = {"id": "x", "title": "t", "metrics": {}, "youtube_metadata": {}}
        q = score_quality(v)
        assert q["quality_score"] == 50.0
        assert q["quality_tier"] == "D"  # 50 is below new C threshold of 55

    def test_perfect_signals_hit_S_tier(self) -> None:
        v = self._video()
        v["metrics"]["cuts_on_beat_pct"] = 100.0
        v["metrics"]["transitions"]["variety_entropy_normalized"] = 1.0
        v["metrics"]["motion_cuts"]["continuity_score"] = 100.0
        v["metrics"]["lyric_alignment"] = {
            "available": True,
            "cuts_on_word_boundary_pct": 100.0,
            "cuts_on_phrase_boundary_pct": 100.0,
        }
        v["youtube_metadata"]["view_count"] = 10_000_000
        v["youtube_metadata"]["like_ratio"] = 0.03
        q = score_quality(v, corpus_audience_reference=10_000_000)
        assert q["quality_tier"] == "S"
        assert q["quality_score"] >= 82
