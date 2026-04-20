"""Tests for the tension curve constructor (Phase 2.3)."""

from __future__ import annotations

import pytest

from fandomforge.intelligence.tension_curve import build_tension_curve
from fandomforge.validation import validate


def _plan_with_acts() -> dict:
    return {
        "schema_version": 1,
        "project_slug": "t",
        "concept": {"theme": "x", "one_sentence": "test concept"},
        "song": {"title": "T", "artist": "A"},
        "fandoms": [{"name": "F"}],
        "vibe": "action",
        "length_seconds": 30.0,
        "platform_target": "youtube",
        "acts": [
            {"number": 1, "name": "Setup", "start_sec": 0, "end_sec": 8,
             "energy_target": 30, "emotional_goal": "x", "pacing": "medium",
             "tension_target": 0.2, "arc_role": "setup"},
            {"number": 2, "name": "Build", "start_sec": 8, "end_sec": 22,
             "energy_target": 70, "emotional_goal": "y", "pacing": "fast",
             "tension_target": 0.7, "arc_role": "escalation"},
            {"number": 3, "name": "Climax", "start_sec": 22, "end_sec": 27,
             "energy_target": 95, "emotional_goal": "z", "pacing": "frantic",
             "tension_target": 1.0, "arc_role": "climax"},
            {"number": 4, "name": "Release", "start_sec": 27, "end_sec": 30,
             "energy_target": 50, "emotional_goal": "r", "pacing": "medium",
             "tension_target": -0.2, "arc_role": "release"},
        ],
    }


def _flat_energy_curve(duration: float = 30.0, energy: float = 0.5) -> list[list[float]]:
    return [[float(t), float(energy)] for t in range(int(duration))]


def _ramp_energy_curve(duration: float = 30.0) -> list[list[float]]:
    return [[float(t), min(1.0, t / 25.0)] for t in range(int(duration))]


class TestBuildTensionCurve:
    def test_outputs_one_sample_per_resolution(self):
        plan = _plan_with_acts()
        bm = {"energy_curve": _flat_energy_curve()}
        curve = build_tension_curve(plan, beat_map=bm, resolution_sec=1.0)
        # 0..30 inclusive at 1s = 31 samples
        assert len(curve["samples"]) >= 30

    def test_target_follows_act_targets(self):
        plan = _plan_with_acts()
        bm = {"energy_curve": _flat_energy_curve()}
        curve = build_tension_curve(plan, beat_map=bm)
        # In setup (0-8s), target should drift from prev (0.2) to act (0.2)
        first = curve["samples"][0]
        assert first["arc_role"] == "setup"
        # In climax (22-27s), target should peak near 1.0
        climax = next(s for s in curve["samples"] if s["arc_role"] == "climax")
        assert climax["target_tension"] >= 0.7

    def test_validates_schema(self):
        plan = _plan_with_acts()
        bm = {"energy_curve": _flat_energy_curve()}
        curve = build_tension_curve(plan, beat_map=bm)
        validate(curve, "tension-curve")

    def test_summary_builds_to_climax_when_energy_ramps(self):
        plan = _plan_with_acts()
        bm = {"energy_curve": _ramp_energy_curve()}
        curve = build_tension_curve(plan, beat_map=bm)
        assert curve["summary"]["builds_to_climax"] is True

    def test_summary_records_peak_actual_time(self):
        plan = _plan_with_acts()
        bm = {"energy_curve": _ramp_energy_curve()}
        curve = build_tension_curve(plan, beat_map=bm)
        # Energy peaks near end → peak_actual_time should be late
        assert curve["summary"]["peak_actual_time_sec"] >= 20.0

    def test_no_beat_map_falls_back_to_target(self):
        plan = _plan_with_acts()
        curve = build_tension_curve(plan)
        # All actuals = targets → delta = 0
        for s in curve["samples"]:
            assert s["delta"] == 0.0

    def test_emotion_arc_contributes(self):
        plan = _plan_with_acts()
        bm = {"energy_curve": _flat_energy_curve()}
        emotion = {
            "samples": [
                {"start_sec": 25.0, "intensity": 1.0,
                 "vector": [0.0, 1.0, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0]},  # high triumph + tension
            ]
        }
        with_emotion = build_tension_curve(plan, beat_map=bm, emotion_arc=emotion)
        without = build_tension_curve(plan, beat_map=bm)
        # Sample at 25s: with_emotion should have higher actual tension
        sample_with = next(s for s in with_emotion["samples"] if abs(s["time_sec"] - 25.0) < 0.5)
        sample_without = next(s for s in without["samples"] if abs(s["time_sec"] - 25.0) < 0.5)
        assert sample_with["actual_tension"] > sample_without["actual_tension"]
