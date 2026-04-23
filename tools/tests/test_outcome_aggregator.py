"""Outcome aggregator tests — boolean-impact + Pearson correlation math."""

from __future__ import annotations

from fandomforge.intelligence.outcome_aggregator import (
    MIN_SAMPLES_FOR_CLAIM,
    aggregate,
    format_recommendations,
)
from fandomforge.intelligence.training_journal import RenderJournalEntry


def _entry(**kwargs) -> dict:
    defaults = dict(
        project_slug="test",
        generated_at="2026-04-21T00:00:00+00:00",
        edit_type="action",
        overall_score=75.0,
        overall_grade="B",
        mfv_craft_enabled=True,
        craft_weights={},
        pre_drop_dropout_sec=0.333,
        j_cut_lead_sec=0.125,
        target_cpm=50.0,
        shot_count=42,
        avg_shot_duration_sec=1.4,
        source_diversity_entropy=2.0,
        num_sources_used=10,
        hero_reserved_count=3,
        drum_fill_count=0,
        lyric_sync_count=0,
        dropout_windows_count=3,
        dim_technical=100.0,
        dim_visual=100.0,
        dim_audio=100.0,
        dim_structural=100.0,
        dim_shot_list=100.0,
        dim_coherence=100.0,
        dim_arc_shape=100.0,
        dim_engagement=100.0,
    )
    defaults.update(kwargs)
    return RenderJournalEntry(**defaults).to_dict()


class TestAggregateEmpty:
    def test_empty_list_returns_zero_sample(self) -> None:
        r = aggregate([])
        assert r.sample_count == 0
        assert r.best_bucket is None
        assert r.recommendations == []


class TestBestBucket:
    def test_finds_highest_scoring_bucket(self) -> None:
        entries = [
            _entry(edit_type="action", overall_score=90),
            _entry(edit_type="action", overall_score=95),
            _entry(edit_type="sad", overall_score=70),
            _entry(edit_type="sad", overall_score=75),
        ]
        r = aggregate(entries)
        assert r.best_bucket == "action"
        assert r.best_bucket_avg_score == 92.5


class TestBooleanImpacts:
    def test_cascade_enabled_vs_disabled(self) -> None:
        entries = []
        # 3 renders with cascade ON at 90, 3 with it OFF at 80 → Δ=+10
        for _ in range(3):
            entries.append(_entry(mfv_craft_enabled=True, overall_score=90))
        for _ in range(3):
            entries.append(_entry(mfv_craft_enabled=False, overall_score=80))
        r = aggregate(entries)
        impacts = {(i.feature, i.dimension): i for i in r.boolean_impacts}
        top = impacts.get(("mfv_craft_enabled", "overall"))
        assert top is not None
        assert top.avg_true == 90.0
        assert top.avg_false == 80.0
        assert top.delta == 10.0

    def test_small_samples_dropped(self) -> None:
        """Need MIN_SAMPLES on both sides to emit a claim."""
        entries = []
        for _ in range(2):
            entries.append(_entry(mfv_craft_enabled=True, overall_score=90))
        for _ in range(5):
            entries.append(_entry(mfv_craft_enabled=False, overall_score=80))
        r = aggregate(entries)
        # Only 2 with True = below the 3-sample floor
        for imp in r.boolean_impacts:
            assert imp.n_true >= MIN_SAMPLES_FOR_CLAIM
            assert imp.n_false >= MIN_SAMPLES_FOR_CLAIM

    def test_craft_weight_boolean_derived_per_feature(self) -> None:
        entries = [
            _entry(craft_weights={"dropout": 1.0}, overall_score=95),
            _entry(craft_weights={"dropout": 1.0}, overall_score=92),
            _entry(craft_weights={"dropout": 1.0}, overall_score=90),
            _entry(craft_weights={"dropout": 0.0}, overall_score=75),
            _entry(craft_weights={"dropout": 0.0}, overall_score=78),
            _entry(craft_weights={"dropout": 0.0}, overall_score=76),
        ]
        r = aggregate(entries)
        dropout_impact = next(
            (i for i in r.boolean_impacts
             if i.feature == "craft.dropout" and i.dimension == "overall"),
            None,
        )
        assert dropout_impact is not None
        assert dropout_impact.avg_true > dropout_impact.avg_false


class TestNumericCorrelations:
    def test_positive_correlation_detected(self) -> None:
        entries = [
            _entry(shot_count=20, overall_score=70),
            _entry(shot_count=30, overall_score=78),
            _entry(shot_count=40, overall_score=85),
            _entry(shot_count=50, overall_score=92),
            _entry(shot_count=60, overall_score=98),
        ]
        r = aggregate(entries)
        cors = {(c.feature, c.dimension): c for c in r.numeric_correlations}
        hit = cors.get(("shot_count", "overall"))
        assert hit is not None
        assert hit.pearson_r > 0.9

    def test_no_correlation_below_threshold(self) -> None:
        entries = [
            _entry(shot_count=20, overall_score=80),
            _entry(shot_count=30, overall_score=82),
            _entry(shot_count=40, overall_score=79),
            _entry(shot_count=50, overall_score=81),
            _entry(shot_count=60, overall_score=80),
        ]
        r = aggregate(entries)
        for c in r.numeric_correlations:
            assert c.feature != "shot_count" or c.dimension != "overall"


class TestRecommendations:
    def test_too_few_samples_warns(self) -> None:
        r = aggregate([_entry(overall_score=80)])
        lines = format_recommendations(r)
        assert any("need at least" in l for l in lines)

    def test_signals_become_recommendations(self) -> None:
        entries = []
        for _ in range(4):
            entries.append(_entry(mfv_craft_enabled=True, overall_score=95))
        for _ in range(4):
            entries.append(_entry(mfv_craft_enabled=False, overall_score=75))
        r = aggregate(entries)
        recs = format_recommendations(r)
        assert any("mfv_craft_enabled" in l for l in recs)
