"""Tests for the slot-fit scorer (Phase 2.2)."""

from __future__ import annotations

import pytest

from fandomforge.intelligence.slot_fit import (
    DEFAULT_WEIGHTS,
    SlotContext,
    build_context,
    find_act_for_time,
    find_zone_for_time,
    pick_best,
    score_candidate,
)


def _ctx(**overrides) -> SlotContext:
    base = dict(
        act_index=2,
        act_pacing="fast",
        act_energy_target=70.0,
        act_arc_role="escalation",
        slot_time_sec=30.0,
        slot_duration_sec=0.8,
        energy_zone_label="high",
        edit_type="action",
        tone_target=[0.0, 1.0, 0.0, 0.5, 0.5, 0.0, 0.0, 0.0],  # triumph + awe + tension
        prev_shot=None,
        next_shot=None,
    )
    base.update(overrides)
    return SlotContext(**base)


def _candidate(**overrides) -> dict:
    base = {
        "clip_category": "action-high",
        "intended_duration_sec": 0.7,
        "emotional_register": [0.0, 0.9, 0.0, 0.6, 0.5, 0.0, 0.0, 0.0],
        "motion_vector": 90.0,
    }
    base.update(overrides)
    return base


class TestComponentScores:
    def test_perfect_emotional_match_scores_high(self):
        cand = _candidate(emotional_register=_ctx().tone_target)
        score = score_candidate(cand, _ctx())
        assert score.breakdown["emotional_register_match"] > 0.95

    def test_zero_emotional_register_falls_back_neutral(self):
        cand = _candidate()
        cand.pop("emotional_register")
        score = score_candidate(cand, _ctx())
        assert score.breakdown["emotional_register_match"] == 0.5

    def test_action_high_in_high_zone_full_affinity(self):
        cand = _candidate(clip_category="action-high")
        score = score_candidate(cand, _ctx(energy_zone_label="high"))
        assert score.breakdown["energy_zone_fit"] == 1.0

    def test_action_high_in_low_zone_penalized(self):
        cand = _candidate(clip_category="action-high")
        score = score_candidate(cand, _ctx(energy_zone_label="low"))
        assert score.breakdown["energy_zone_fit"] < 0.5

    def test_duration_inside_band_scores_one(self):
        # fast band = 0.5-1.0
        cand = _candidate(intended_duration_sec=0.7)
        score = score_candidate(cand, _ctx(act_pacing="fast"))
        assert score.breakdown["duration_fit"] == 1.0

    def test_duration_too_long_penalized(self):
        cand = _candidate(intended_duration_sec=4.0)
        score = score_candidate(cand, _ctx(act_pacing="fast"))
        # 1.0 / 4.0 = 0.25
        assert score.breakdown["duration_fit"] < 0.5

    def test_motion_continuity_smooth(self):
        prev = {"motion_vector": 88.0}
        nxt = {"motion_vector": 92.0}
        cand = _candidate(motion_vector=90.0)
        score = score_candidate(cand, _ctx(prev_shot=prev, next_shot=nxt))
        assert score.breakdown["motion_continuity"] > 0.9

    def test_motion_continuity_180_flip_low(self):
        prev = {"motion_vector": 0.0}
        nxt = {"motion_vector": 0.0}
        cand = _candidate(motion_vector=180.0)
        score = score_candidate(cand, _ctx(prev_shot=prev, next_shot=nxt))
        assert score.breakdown["motion_continuity"] < 0.1

    def test_color_continuity_close_luma(self):
        prev = {"avg_luma": 0.5}
        nxt = {"avg_luma": 0.55}
        cand = _candidate()
        cand["avg_luma"] = 0.52
        score = score_candidate(cand, _ctx(prev_shot=prev, next_shot=nxt))
        assert score.breakdown["color_continuity"] > 0.8

    def test_color_continuity_big_jump_low(self):
        prev = {"avg_luma": 0.1}
        nxt = {"avg_luma": 0.1}
        cand = _candidate()
        cand["avg_luma"] = 0.9
        score = score_candidate(cand, _ctx(prev_shot=prev, next_shot=nxt))
        assert score.breakdown["color_continuity"] < 0.2

    def test_edit_type_preference_pulls_from_taxonomy(self):
        # action-high has bias 1.6 for action → score = 1.6/2 = 0.8
        cand = _candidate(clip_category="action-high")
        score = score_candidate(cand, _ctx(edit_type="action"))
        assert score.breakdown["edit_type_preference"] == pytest.approx(0.8, abs=0.01)


class TestComposite:
    def test_perfect_candidate_scores_high(self):
        cand = _candidate(emotional_register=_ctx().tone_target)
        score = score_candidate(cand, _ctx())
        assert score.composite > 0.7

    def test_misfit_candidate_scores_low(self):
        # Wrong category for high-energy action zone, wrong duration, wrong register
        cand = _candidate(
            clip_category="reaction-quiet",
            intended_duration_sec=4.0,
            emotional_register=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # grief
        )
        score = score_candidate(cand, _ctx())
        assert score.composite < 0.5

    def test_notes_surface_bottom_scores(self):
        cand = _candidate(intended_duration_sec=4.0)
        score = score_candidate(cand, _ctx())
        assert any("duration" in n for n in score.notes)


class TestPickBest:
    def test_returns_highest_composite(self):
        good = _candidate(emotional_register=_ctx().tone_target)
        bad = _candidate(intended_duration_sec=5.0, clip_category="reaction-quiet")
        winner, score = pick_best([good, bad], _ctx())
        assert winner is good
        assert score.composite > 0.5

    def test_empty_candidates_returns_none(self):
        winner, score = pick_best([], _ctx())
        assert winner is None and score is None


class TestFindHelpers:
    def test_find_act_for_time(self):
        acts = [
            {"number": 1, "start_sec": 0.0, "end_sec": 12.0},
            {"number": 2, "start_sec": 12.0, "end_sec": 42.0},
            {"number": 3, "start_sec": 42.0, "end_sec": 60.0},
        ]
        assert find_act_for_time(acts, 5.0)["number"] == 1
        assert find_act_for_time(acts, 25.0)["number"] == 2
        assert find_act_for_time(acts, 50.0)["number"] == 3
        # Beyond end → last act
        assert find_act_for_time(acts, 100.0)["number"] == 3

    def test_find_zone_for_time(self):
        zones = [
            {"start_sec": 0.0, "end_sec": 5.0, "label": "low"},
            {"start_sec": 5.0, "end_sec": 30.0, "label": "mid"},
            {"start_sec": 30.0, "end_sec": 35.0, "label": "drop"},
        ]
        assert find_zone_for_time(zones, 2.0) == "low"
        assert find_zone_for_time(zones, 15.0) == "mid"
        assert find_zone_for_time(zones, 31.0) == "drop"
        # Beyond → mid fallback
        assert find_zone_for_time(zones, 100.0) == "mid"


class TestBuildContext:
    def test_constructs_from_artifacts(self):
        edit_plan = {
            "acts": [
                {"number": 1, "name": "S", "start_sec": 0, "end_sec": 12,
                 "energy_target": 30, "emotional_goal": "x", "pacing": "medium",
                 "tension_target": 0.2, "arc_role": "setup"},
                {"number": 2, "name": "E", "start_sec": 12, "end_sec": 42,
                 "energy_target": 70, "emotional_goal": "y", "pacing": "fast",
                 "tension_target": 0.7, "arc_role": "escalation"},
            ],
        }
        intent = {"edit_type": "action", "tone_vector": [0.5] * 8}
        energy_zones = {"zones": [{"start_sec": 0, "end_sec": 50, "label": "high"}]}
        ctx = build_context(
            edit_plan=edit_plan, intent=intent, energy_zones=energy_zones,
            slot_time_sec=20.0, slot_duration_sec=0.8,
        )
        assert ctx.act_index == 2
        assert ctx.act_pacing == "fast"
        assert ctx.energy_zone_label == "high"
        assert ctx.edit_type == "action"
