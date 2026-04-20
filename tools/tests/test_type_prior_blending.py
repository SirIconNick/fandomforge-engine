"""Sync planner blending of edit-type priors with corpus priors."""

from __future__ import annotations

from fandomforge.intelligence.sync_planner import blend_type_and_corpus_priors


class TestBlendPriors:
    def _type_priors(self, target_sec: float = 1.0, target_cpm: float = 50.0) -> dict:
        return {
            "label": "Action",
            "target_shot_duration_sec": target_sec,
            "target_cuts_per_minute": target_cpm,
        }

    def _corpus_priors(self, median_sec: float = 1.4, cpm: float = 42.0) -> dict:
        return {
            "tag": "action-pl1",
            "priors": {
                "median_shot_duration_sec": median_sec,
                "cuts_per_minute": cpm,
                "typical_act_pacing_pct": [25.0, 45.0, 30.0],
            },
        }

    def test_both_none_returns_none(self) -> None:
        assert blend_type_and_corpus_priors(None, None) is None

    def test_only_corpus_returns_corpus(self) -> None:
        c = self._corpus_priors()
        assert blend_type_and_corpus_priors(None, c) is c

    def test_only_type_wraps_as_priors(self) -> None:
        t = self._type_priors()
        result = blend_type_and_corpus_priors(t, None)
        assert result is not None
        assert result["priors"]["median_shot_duration_sec"] == 1.0
        assert result["priors"]["cuts_per_minute"] == 50.0

    def test_blend_60_40_weighted_toward_type(self) -> None:
        t = self._type_priors(target_sec=1.0, target_cpm=50.0)
        c = self._corpus_priors(median_sec=2.0, cpm=30.0)
        result = blend_type_and_corpus_priors(t, c, type_weight=0.6)
        assert result["priors"]["median_shot_duration_sec"] == 1.4
        assert result["priors"]["cuts_per_minute"] == 42.0
        assert result["priors"]["edit_type_target_sec"] == 1.0

    def test_tag_captures_type_blend(self) -> None:
        t = self._type_priors()
        c = self._corpus_priors()
        result = blend_type_and_corpus_priors(t, c)
        assert "Action" in result["tag"]
        assert "action-pl1" in result["tag"]
