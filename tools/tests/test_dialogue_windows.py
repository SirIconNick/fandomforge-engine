"""Tests for dialogue-window classification + cue placement evaluation."""

from __future__ import annotations

import pytest

from fandomforge.audio.dialogue_windows import (
    DialogueWindow,
    DialogueWindowsResult,
    build_placement_plan,
    classify_windows,
    evaluate_placement,
)


def _ez(
    duration: float = 10.0,
    *,
    bands: list[dict] | None = None,
    zones: list[dict] | None = None,
) -> dict:
    """Build a minimal energy-zones dict for tests."""
    return {
        "schema_version": 1,
        "duration_sec": duration,
        "sample_rate_hz": 22050,
        "resolution_sec": 0.25,
        "bands": bands or [],
        "zones": zones or [],
        "transients": [],
        "generator": "test",
    }


def _quiet_band(t: float) -> dict:
    return {"time_sec": t, "bass": 0.05, "mid": 0.04, "treble": 0.03}


def _loud_band(t: float) -> dict:
    return {"time_sec": t, "bass": 0.85, "mid": 0.92, "treble": 0.7}


def _mid_band(t: float) -> dict:
    return {"time_sec": t, "bass": 0.4, "mid": 0.5, "treble": 0.3}


class TestClassifyWindows:
    def test_silent_intro_classifies_safe(self) -> None:
        bands = [_quiet_band(0.25 * i) for i in range(8)]
        zones = [{"start_sec": 0.0, "end_sec": 2.0, "label": "low",
                  "confidence": 0.9, "avg_energy": 0.05}]
        ez = _ez(duration=2.0, bands=bands, zones=zones)
        r = classify_windows(ez)
        assert r.safe_window_count == 8
        assert r.blocked_window_count == 0
        assert all(w.flag == "SAFE" for w in r.windows)

    def test_loud_drop_classifies_blocked(self) -> None:
        bands = [_loud_band(0.25 * i) for i in range(4)]
        zones = [{"start_sec": 0.0, "end_sec": 1.0, "label": "drop",
                  "confidence": 0.9, "avg_energy": 0.85}]
        ez = _ez(duration=1.0, bands=bands, zones=zones)
        r = classify_windows(ez)
        assert r.blocked_window_count == 4
        assert r.safe_window_count == 0

    def test_mid_zone_classifies_risky(self) -> None:
        bands = [_mid_band(0.25 * i) for i in range(4)]
        zones = [{"start_sec": 0.0, "end_sec": 1.0, "label": "mid",
                  "confidence": 0.5, "avg_energy": 0.5}]
        ez = _ez(duration=1.0, bands=bands, zones=zones)
        r = classify_windows(ez)
        # Mid energy + no overrides = RISKY
        assert r.risky_window_count >= 3

    def test_post_drop_window_promotes_to_safe(self) -> None:
        # Energy is mid, but a drop just landed → 0-800ms after = SAFE
        bands = [_mid_band(0.25 * i) for i in range(8)]
        zones = [{"start_sec": 0.0, "end_sec": 2.0, "label": "mid",
                  "confidence": 0.5, "avg_energy": 0.5}]
        ez = _ez(duration=2.0, bands=bands, zones=zones)
        beat_map = {"drops": [{"time": 0.0, "intensity": 1.0, "type": "main_drop"}]}
        r = classify_windows(ez, beat_map=beat_map)
        # First few windows (within 800ms) should be SAFE due to post_drop_window
        post_drop_safe = [w for w in r.windows[:4] if w.flag == "SAFE"]
        assert post_drop_safe, "expected at least one SAFE window in post-drop zone"
        assert any("post_drop_window" in w.reason_codes for w in post_drop_safe)

    def test_dense_mid_blocks_even_at_low_rms(self) -> None:
        # Bass + treble silent, mid heavy = vocals or lead synth = block
        bands = [{"time_sec": 0.25 * i, "bass": 0.1, "mid": 0.8, "treble": 0.1}
                 for i in range(4)]
        zones = [{"start_sec": 0.0, "end_sec": 1.0, "label": "mid",
                  "confidence": 0.5, "avg_energy": 0.4}]
        ez = _ez(duration=1.0, bands=bands, zones=zones)
        r = classify_windows(ez)
        assert all(w.flag == "BLOCKED" for w in r.windows)
        assert all("dense_mid_frequencies" in w.reason_codes for w in r.windows)

    def test_min_duration_available_runs_through_safe(self) -> None:
        bands = [_quiet_band(0.25 * i) for i in range(8)]
        zones = [{"start_sec": 0.0, "end_sec": 2.0, "label": "low",
                  "confidence": 0.9, "avg_energy": 0.05}]
        ez = _ez(duration=2.0, bands=bands, zones=zones)
        r = classify_windows(ez)
        # First window's run should equal full duration (8 windows × 0.25s)
        assert r.windows[0].min_duration_available_sec == pytest.approx(2.0, abs=0.01)
        # Last window's run is just one slot (no SAFE successor)
        assert r.windows[-1].min_duration_available_sec == pytest.approx(0.25, abs=0.01)


class TestEvaluatePlacement:
    def _safe_windows(self, n: int = 20) -> list[DialogueWindow]:
        out = []
        for i in range(n):
            t = i * 0.25
            out.append(DialogueWindow(
                start_sec=t, end_sec=t + 0.25,
                flag="SAFE", reason_codes=["low_energy_zone"],
                min_duration_available_sec=(n - i) * 0.25,
                rms_at_start=0.1, mid_density_at_start=0.05,
            ))
        return out

    def test_place_when_safe_with_room(self) -> None:
        windows = self._safe_windows(20)
        p = evaluate_placement(windows, requested_start_sec=1.0, cue_duration_sec=2.0)
        assert p.decision == "PLACE"
        assert p.flag_at_placement == "SAFE"

    def test_shift_to_nearest_safe(self) -> None:
        # Mix: blocked at requested start, safe nearby
        windows = []
        for i in range(20):
            t = i * 0.25
            blocked = i < 4  # first 1.0s blocked
            windows.append(DialogueWindow(
                start_sec=t, end_sec=t + 0.25,
                flag="BLOCKED" if blocked else "SAFE",
                reason_codes=["high_energy_zone"] if blocked else ["low_energy_zone"],
                min_duration_available_sec=0.0 if blocked else (20 - i) * 0.25,
                rms_at_start=0.8 if blocked else 0.1,
                mid_density_at_start=0.7 if blocked else 0.05,
            ))
        p = evaluate_placement(windows, requested_start_sec=0.5, cue_duration_sec=2.0,
                               allow_shift_sec=2.0)
        assert p.decision == "SHIFT"
        assert p.placed_start_sec >= 1.0  # shifted to first SAFE window
        assert p.flag_at_placement == "SAFE"

    def test_reject_when_no_safe_room_anywhere_near(self) -> None:
        # All windows BLOCKED
        windows = [DialogueWindow(
            start_sec=i * 0.25, end_sec=i * 0.25 + 0.25,
            flag="BLOCKED", reason_codes=["high_energy_zone"],
            min_duration_available_sec=0.0,
            rms_at_start=0.9, mid_density_at_start=0.8,
        ) for i in range(40)]
        p = evaluate_placement(windows, requested_start_sec=2.0, cue_duration_sec=1.5,
                               allow_shift_sec=1.0)
        assert p.decision == "REJECT"
        assert p.flag_at_placement == "BLOCKED"

    def test_risky_acceptable_when_no_safe_alt(self) -> None:
        windows = [DialogueWindow(
            start_sec=i * 0.25, end_sec=i * 0.25 + 0.25,
            flag="RISKY", reason_codes=["beat_proximity"],
            min_duration_available_sec=0.0,
            rms_at_start=0.4, mid_density_at_start=0.3,
        ) for i in range(20)]
        p = evaluate_placement(windows, requested_start_sec=1.0, cue_duration_sec=2.0)
        assert p.decision == "PLACE"
        assert p.flag_at_placement == "RISKY"


class TestBuildPlacementPlan:
    def test_processes_all_cues(self) -> None:
        windows = [DialogueWindow(
            start_sec=i * 0.25, end_sec=i * 0.25 + 0.25,
            flag="SAFE", reason_codes=["low_energy_zone"],
            min_duration_available_sec=(40 - i) * 0.25,
            rms_at_start=0.1, mid_density_at_start=0.05,
        ) for i in range(40)]
        cues = [
            {"start": 0.5, "duration": 2.0},
            {"start": 4.0, "duration": 1.5},
            {"start": 7.0, "duration": 1.0},
        ]
        plans = build_placement_plan(cues, windows)
        assert len(plans) == 3
        assert all(p.decision == "PLACE" for p in plans)
        assert plans[0].cue_index == 0
        assert plans[2].cue_index == 2
