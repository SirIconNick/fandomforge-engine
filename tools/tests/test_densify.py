"""Tests for shot_proposer.densify_shot_list + step_densify_shot_list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fandomforge.intelligence.shot_proposer import (
    _DEFAULT_FILLER_BAND_SEC,
    densify_shot_list,
)


def _sparse_list(*, fps: float = 24.0, slots: list[tuple[float, float]], song_sec: float) -> dict:
    """Build a minimum-valid sparse shot-list from (start_sec, dur_sec) slot pairs."""
    shots = []
    for i, (start_sec, dur_sec) in enumerate(slots, start=1):
        shots.append({
            "id": f"s{i:03d}",
            "act": 1,
            "start_frame": int(round(start_sec * fps)),
            "duration_frames": int(round(dur_sec * fps)),
            "source_id": "src-A",
            "source_timecode": "0:00:00.000",
            "role": "hero",
            "mood_tags": [], "framing": "", "motion_vector": None, "eyeline": "",
            "beat_sync": {"type": "drop", "index": i, "time_sec": start_sec},
            "scores": {"theme_fit": 3.0, "fandom_balance": 3.0,
                       "emotion": 3.0, "beat_sync_score": 4.5},
        })
    return {
        "schema_version": 1,
        "project_slug": "dense-test",
        "fps": fps,
        "resolution": {"width": 1920, "height": 1080},
        "song_duration_sec": song_sec,
        "shots": shots,
        "generated_at": "2026-04-21T00:00:00+00:00",
        "generator": "test",
    }


class TestDensifyShotList:
    def test_fills_tail_gap_to_song_end(self):
        sl = _sparse_list(slots=[(0.0, 1.0)], song_sec=10.0)
        out = densify_shot_list(sl)
        total_frames = sum(s["duration_frames"] for s in out["shots"])
        total_sec = total_frames / out["fps"]
        assert total_sec >= 9.5  # close to song duration (rounding slack)
        # Only the original shot retains its beat-sync type
        assert out["shots"][0]["beat_sync"]["type"] == "drop"
        assert all(s["beat_sync"]["type"] == "free" for s in out["shots"][1:])

    def test_fills_between_slots(self):
        sl = _sparse_list(slots=[(0.0, 1.0), (6.0, 1.0)], song_sec=10.0)
        out = densify_shot_list(sl)
        # Filler shots should appear between index 0 (ends at 1s) and the
        # former index 1 (starts at 6s) — that's a 5s gap.
        fillers_in_gap = [
            s for s in out["shots"]
            if s.get("densified") is True
            and 1.0 <= (s["start_frame"] / out["fps"]) < 6.0
        ]
        assert len(fillers_in_gap) >= 1, f"expected fillers in 1s→6s gap, got {out['shots']}"

    def test_idempotent_marker_on_fillers(self):
        sl = _sparse_list(slots=[(0.0, 1.0), (5.0, 1.0)], song_sec=10.0)
        out = densify_shot_list(sl)
        fillers = [s for s in out["shots"] if s.get("densified")]
        slot_shots = [s for s in out["shots"] if not s.get("densified")]
        assert len(slot_shots) == 2  # originals preserved
        assert len(fillers) >= 1  # fillers added
        assert all(f["role"] == "insert" for f in fillers)

    def test_reuses_flanking_source_id_for_fillers(self):
        sl = _sparse_list(slots=[(0.0, 1.0), (5.0, 1.0)], song_sec=10.0)
        # Change the first shot's source so we can check inheritance.
        sl["shots"][0]["source_id"] = "src-FLANK"
        out = densify_shot_list(sl)
        fillers_after_shot0 = [
            s for s in out["shots"]
            if s.get("densified") and s["start_frame"] < int(5 * out["fps"])
        ]
        assert fillers_after_shot0, "expected at least one filler after shot 0"
        assert all(f["source_id"] == "src-FLANK" for f in fillers_after_shot0)

    def test_renumbers_ids_sequentially(self):
        sl = _sparse_list(slots=[(0.0, 1.0), (5.0, 1.0)], song_sec=10.0)
        out = densify_shot_list(sl)
        ids = [s["id"] for s in out["shots"]]
        assert ids == [f"s{i+1:03d}" for i in range(len(ids))]

    def test_zero_song_duration_returns_untouched(self):
        sl = _sparse_list(slots=[(0.0, 1.0)], song_sec=0.0)
        sl.pop("song_duration_sec", None)
        # No song duration → nothing to densify to.
        out = densify_shot_list(sl, song_duration_sec=0.0)
        assert len(out["shots"]) == len(sl["shots"])

    def test_pacing_band_used_when_edit_plan_has_acts(self):
        # Act 1 = slow → shots should be longer than the default band median
        sl = _sparse_list(slots=[(0.0, 1.0)], song_sec=30.0)
        edit_plan = {
            "acts": [{"start_sec": 0.0, "end_sec": 30.0, "pacing": "slow"}]
        }
        out = densify_shot_list(sl, edit_plan=edit_plan)
        fillers = [s for s in out["shots"] if s.get("densified")]
        # Slow pacing median is > 2s typically; default median is 1.5s.
        avg_filler = (
            sum(s["duration_frames"] for s in fillers)
            / max(1, len(fillers))
            / out["fps"]
        )
        # Just check it's noticeably > the default-band median.
        default_median = sum(_DEFAULT_FILLER_BAND_SEC) / 2
        assert avg_filler > default_median

    def test_gap_shorter_than_min_filler_folds_into_neighbor(self):
        # Gap of 0.3s — below _MIN_FILLER_SEC (0.4). Shouldn't create a filler.
        sl = _sparse_list(slots=[(0.0, 1.0), (1.3, 1.0)], song_sec=5.0)
        out = densify_shot_list(sl)
        fillers_between = [
            s for s in out["shots"]
            if s.get("densified") and 1.0 <= (s["start_frame"] / out["fps"]) < 1.3
        ]
        assert len(fillers_between) == 0

    def test_scene_match_picks_from_scenes_dict(self):
        """When scenes_by_source is provided, fillers pick source_id + source_timecode
        from scenes rather than inheriting from flanking shot. Scene rotation
        across sources prevents the engagement-killing repeat."""
        sl = _sparse_list(slots=[(0.0, 1.0)], song_sec=10.0)
        scenes_by_source = {
            "src-A": [
                {"index": 0, "start_sec": 0.0, "end_sec": 2.0, "duration_sec": 2.0,
                 "intensity_tier": "medium", "motion": 0.3, "avg_luma": 0.5},
                {"index": 1, "start_sec": 10.0, "end_sec": 12.0, "duration_sec": 2.0,
                 "intensity_tier": "medium", "motion": 0.3, "avg_luma": 0.5},
            ],
            "src-B": [
                {"index": 0, "start_sec": 5.0, "end_sec": 7.5, "duration_sec": 2.5,
                 "intensity_tier": "medium", "motion": 0.4, "avg_luma": 0.6},
            ],
        }
        out = densify_shot_list(sl, scenes_by_source=scenes_by_source)
        fillers = [s for s in out["shots"] if s.get("densified")]
        # Fillers should come from both sources (rotation) and use scene
        # timecodes — not the flanking shot's 0:00:00.000.
        srcs = {f["source_id"] for f in fillers}
        assert srcs - {"src-A"}, f"expected fillers from multiple sources, got {srcs}"
        timecodes = {f["source_timecode"] for f in fillers}
        assert len(timecodes) > 1, f"expected varied timecodes, got {timecodes}"

    def test_scene_match_respects_intensity_band(self):
        """Frantic pacing should pull high-intensity scenes; slow should pull low."""
        sl = _sparse_list(slots=[(0.0, 1.0)], song_sec=10.0)
        edit_plan = {
            "acts": [{"start_sec": 0.0, "end_sec": 10.0, "pacing": "frantic"}],
        }
        scenes_by_source = {
            "src-A": [
                {"index": 0, "start_sec": 0.0, "end_sec": 2.0, "duration_sec": 2.0,
                 "intensity_tier": "low", "motion": 0.05, "avg_luma": 0.3},
                {"index": 1, "start_sec": 10.0, "end_sec": 12.0, "duration_sec": 2.0,
                 "intensity_tier": "high", "motion": 0.8, "avg_luma": 0.7},
            ],
        }
        out = densify_shot_list(
            sl, edit_plan=edit_plan, scenes_by_source=scenes_by_source,
        )
        fillers = [s for s in out["shots"] if s.get("densified")]
        # First filler in the frantic act should favor the high-intensity scene.
        # Its source_timecode should match scene[1]'s start_sec (10.0s).
        first_filler = fillers[0] if fillers else None
        assert first_filler is not None
        assert first_filler["source_timecode"].startswith("0:00:10"), \
            f"frantic act should pick high-intensity scene, got {first_filler['source_timecode']}"

    def test_budget_governor_reduces_cpm_when_frantic_bands_would_overshoot(self):
        """A 60s gap with frantic band (0.25-0.6, median 0.425) would emit
        ~141 fillers naturally = ~141 cpm. Budget governor should stretch
        those fillers so total cpm lands close to target_cpm=45 for action."""
        sl = _sparse_list(slots=[(0.0, 0.5)], song_sec=60.0)
        edit_plan = {
            "edit_type": "action",
            "acts": [{"start_sec": 0.0, "end_sec": 60.0, "pacing": "frantic"}],
        }
        out = densify_shot_list(sl, edit_plan=edit_plan)
        shot_count = len(out["shots"])
        cpm = shot_count / (60.0 / 60.0)
        # action target is 45 cpm. Band clamping at hi*1.5 (0.9s) means the
        # stretchable ceiling for frantic is ~66 cpm. Just verify we're in
        # that sane band, not the 141 cpm unbounded mode.
        assert cpm <= 70, f"cpm {cpm:.1f} exceeds clamped ceiling (expected <= 70)"
        assert cpm >= 30, f"cpm {cpm:.1f} below reasonable floor (expected >= 30)"

    def test_budget_governor_respects_relative_pacing_between_acts(self):
        """Slow acts should still have longer fillers than frantic acts even
        after the global stretch_factor is applied. The relative difference
        between bands is preserved because stretch is multiplicative."""
        sl = _sparse_list(slots=[(0.0, 0.5), (30.0, 0.5)], song_sec=60.0)
        edit_plan = {
            "edit_type": "action",
            "acts": [
                {"start_sec": 0.0, "end_sec": 30.0, "pacing": "slow"},
                {"start_sec": 30.0, "end_sec": 60.0, "pacing": "frantic"},
            ],
        }
        out = densify_shot_list(sl, edit_plan=edit_plan)
        fps = out["fps"]
        slow_fillers = [
            s for s in out["shots"]
            if s.get("densified") and (s["start_frame"] / fps) < 30.0
        ]
        frantic_fillers = [
            s for s in out["shots"]
            if s.get("densified") and (s["start_frame"] / fps) >= 30.0
        ]
        assert slow_fillers and frantic_fillers
        avg_slow = sum(s["duration_frames"] for s in slow_fillers) / len(slow_fillers) / fps
        avg_frantic = sum(s["duration_frames"] for s in frantic_fillers) / len(frantic_fillers) / fps
        assert avg_slow > avg_frantic, (
            f"slow act fillers ({avg_slow:.2f}s) should be longer than "
            f"frantic act fillers ({avg_frantic:.2f}s)"
        )

    def test_explicit_target_cpm_overrides_edit_type_default(self):
        """When edit_plan.target_cpm is set explicitly, it wins over the
        edit_type default. A 60s sad edit with target_cpm=60 should emit
        way more shots than the edit_type default (18 cpm) would allow."""
        sl = _sparse_list(slots=[(0.0, 0.5)], song_sec=60.0)
        plan_default = {
            "edit_type": "sad",
            "acts": [{"start_sec": 0.0, "end_sec": 60.0, "pacing": "medium"}],
        }
        plan_override = dict(plan_default)
        plan_override["target_cpm"] = 60.0

        out_default = densify_shot_list(sl, edit_plan=plan_default)
        out_override = densify_shot_list(sl, edit_plan=plan_override)

        assert len(out_override["shots"]) > len(out_default["shots"]), (
            f"target_cpm=60 override ({len(out_override['shots'])} shots) "
            f"should produce more than edit_type=sad default "
            f"({len(out_default['shots'])} shots)"
        )

    def test_dark_scene_rejected_when_brighter_sibling_exists(self):
        """A scene with avg_luma < 0.15 must not be picked when a brighter
        scene is available at the same intensity band. Guards the
        black-frame-flicker failure mode. Provides enough bright scenes
        to cover the whole gap so last-resort never has to fire."""
        sl = _sparse_list(slots=[(0.0, 1.0)], song_sec=8.0)
        scenes_by_source = {
            "src-A": [
                # Dark scene at t=0 — should NEVER be picked.
                {"index": 0, "start_sec": 0.0, "end_sec": 2.0, "duration_sec": 2.0,
                 "intensity_tier": "medium", "avg_luma": 0.05},
                # A pile of bright alternatives — enough to fill the 7s gap.
                {"index": 1, "start_sec": 20.0, "end_sec": 22.0, "duration_sec": 2.0,
                 "intensity_tier": "medium", "avg_luma": 0.4},
                {"index": 2, "start_sec": 30.0, "end_sec": 32.0, "duration_sec": 2.0,
                 "intensity_tier": "medium", "avg_luma": 0.5},
                {"index": 3, "start_sec": 40.0, "end_sec": 42.0, "duration_sec": 2.0,
                 "intensity_tier": "medium", "avg_luma": 0.6},
                {"index": 4, "start_sec": 50.0, "end_sec": 52.0, "duration_sec": 2.0,
                 "intensity_tier": "medium", "avg_luma": 0.3},
                {"index": 5, "start_sec": 60.0, "end_sec": 62.0, "duration_sec": 2.0,
                 "intensity_tier": "medium", "avg_luma": 0.45},
            ],
        }
        out = densify_shot_list(sl, scenes_by_source=scenes_by_source)
        fillers = [s for s in out["shots"] if s.get("densified")]
        # No scene-matched filler should land on the 0.0s timecode (dark scene).
        # We distinguish scene-matched from flank-fallback fillers by checking
        # clip_category — scene-matched ones get "action-mid"/"texture" based
        # on intensity; only scene-matched ones mark motion_vector.
        scene_matched = [
            f for f in fillers if f["source_id"] == "src-A"
            and f.get("clip_category") in ("action-mid", "action-high", "reaction-quiet")
        ]
        assert scene_matched, "test fixture should produce scene-matched fillers"
        dark_pick = [
            f for f in scene_matched
            if f["source_timecode"].startswith("0:00:00")
        ]
        assert not dark_pick, \
            f"scene-match picker used a dark scene: {dark_pick}"

    def test_dark_scene_last_resort_when_nothing_brighter(self):
        """When every scene in every source is dark, the picker falls back
        to the brightest of the dark ones rather than returning None."""
        sl = _sparse_list(slots=[(0.0, 1.0)], song_sec=6.0)
        scenes_by_source = {
            "src-A": [
                {"index": 0, "start_sec": 0.0, "end_sec": 2.0, "duration_sec": 2.0,
                 "intensity_tier": "medium", "avg_luma": 0.05},
                {"index": 1, "start_sec": 10.0, "end_sec": 12.0, "duration_sec": 2.0,
                 "intensity_tier": "medium", "avg_luma": 0.12},  # brightest dark
            ],
        }
        out = densify_shot_list(sl, scenes_by_source=scenes_by_source)
        fillers = [s for s in out["shots"] if s.get("densified")]
        # At least one filler picked; should be the 0.12 one (brighter).
        scene_fillers = [f for f in fillers if f["source_id"] == "src-A"]
        assert scene_fillers, "expected at least one scene-matched filler"
        # The 10.0s scene (avg_luma=0.12) is brighter than 0.0s (0.05);
        # at least one filler should point to the brighter of the two.
        picks_brighter = any(
            f["source_timecode"].startswith("0:00:10") for f in scene_fillers
        )
        assert picks_brighter, \
            f"last-resort should prefer brightest dark, got timecodes: " \
            f"{[f['source_timecode'] for f in scene_fillers]}"

    def test_missing_avg_luma_does_not_reject(self):
        """Scenes without avg_luma must not be rejected — the field is
        optional and legacy sources don't carry it."""
        sl = _sparse_list(slots=[(0.0, 1.0)], song_sec=6.0)
        scenes_by_source = {
            "src-A": [
                {"index": 0, "start_sec": 0.0, "end_sec": 2.0, "duration_sec": 2.0,
                 "intensity_tier": "medium"},  # no avg_luma
            ],
        }
        out = densify_shot_list(sl, scenes_by_source=scenes_by_source)
        fillers = [s for s in out["shots"] if s.get("densified")]
        scene_fillers = [f for f in fillers if f["source_id"] == "src-A"]
        assert scene_fillers, \
            "scene without avg_luma should still be usable as a filler"

    def test_target_duration_clamps_tail_fill(self):
        """target_duration_sec < song_duration_sec → tail fill stops at target.
        Guards against the 229s machine-gun render when project-config says 90s."""
        sl = _sparse_list(slots=[(0.0, 1.0)], song_sec=60.0)
        out = densify_shot_list(sl, target_duration_sec=20.0)
        total_sec = sum(s["duration_frames"] for s in out["shots"]) / out["fps"]
        assert abs(total_sec - 20.0) < (1.0 / out["fps"]), \
            f"expected ~20s shot total, got {total_sec:.2f}s"

    def test_target_duration_drops_slot_shots_past_target(self):
        """Slot shots starting past target_duration must be dropped. Without
        this filter, propose_shot_list's full-song sync points bleed through."""
        sl = _sparse_list(
            slots=[(0.0, 1.0), (5.0, 1.0), (15.0, 1.0), (25.0, 1.0)],
            song_sec=30.0,
        )
        out = densify_shot_list(sl, target_duration_sec=10.0)
        # The 15s and 25s slot shots must not survive. Check remaining slot
        # shots (non-densified) all start before 10s.
        slot_shots = [s for s in out["shots"] if not s.get("densified")]
        for s in slot_shots:
            start_sec = s["start_frame"] / out["fps"]
            assert start_sec < 10.0, \
                f"slot shot at {start_sec:.2f}s survived past target=10s"

    def test_target_duration_clamps_final_slot_shot(self):
        """A slot shot whose end crosses target must be clamped, not dropped."""
        sl = _sparse_list(slots=[(0.0, 1.0), (8.0, 5.0)], song_sec=30.0)
        out = densify_shot_list(sl, target_duration_sec=10.0)
        total_sec = sum(s["duration_frames"] for s in out["shots"]) / out["fps"]
        assert abs(total_sec - 10.0) < (1.0 / out["fps"]), \
            f"expected ~10s total after clamp, got {total_sec:.2f}s"
        # The 8s slot shot should still be present but shorter than 5s
        slot_shots = [s for s in out["shots"] if not s.get("densified")]
        clamped = [s for s in slot_shots if (s["start_frame"] / out["fps"]) >= 7.9]
        assert clamped, "expected the 8s slot shot to survive (clamped)"
        assert clamped[0]["duration_frames"] / out["fps"] < 3.0, \
            "8s slot shot should be clamped to <3s (10 - 8 = 2s remaining)"

    def test_target_duration_none_falls_back_to_song(self):
        """target_duration_sec=None keeps legacy behavior — fill to full song."""
        sl = _sparse_list(slots=[(0.0, 1.0)], song_sec=10.0)
        out = densify_shot_list(sl, target_duration_sec=None)
        total_sec = sum(s["duration_frames"] for s in out["shots"]) / out["fps"]
        assert abs(total_sec - 10.0) < (1.0 / out["fps"])

    def test_target_duration_larger_than_song_is_clamped_to_song(self):
        """target > song → don't over-fill. min(song, target) guards this."""
        sl = _sparse_list(slots=[(0.0, 1.0)], song_sec=10.0)
        out = densify_shot_list(sl, target_duration_sec=30.0)
        total_sec = sum(s["duration_frames"] for s in out["shots"]) / out["fps"]
        assert abs(total_sec - 10.0) < (1.0 / out["fps"])

    def test_small_tail_absorbed_by_previous_filler(self):
        """When the last fill segment would be below MIN_FILLER_SEC, the
        remainder gets folded into the previous filler so total coverage
        hits song_duration exactly (within rounding). Guards against the
        1.5s qa.duration residual that bit the first live test."""
        # Song = 10s, one slot at 0s (1s dur). Fill 1s→10s = 9s gap.
        # With 1.5s fillers, we get ~6 full fillers (9s total). Edge case:
        # if the last partial would be < 0.4s, fold it in.
        sl = _sparse_list(slots=[(0.0, 1.0)], song_sec=10.0)
        out = densify_shot_list(sl)
        total_frames = sum(s["duration_frames"] for s in out["shots"])
        total_sec = total_frames / out["fps"]
        # Must be within one frame of song_duration — absorb-tail guarantees
        # we don't drop multi-tenths across segments.
        assert abs(total_sec - 10.0) < (1.0 / out["fps"])


class TestStepDensifyShotList:
    def test_step_densifies_and_validates(self, tmp_path: Path):
        from fandomforge.autopilot import AutopilotContext, step_densify_shot_list

        proj = tmp_path / "projects" / "dense-smoke"
        (proj / "data").mkdir(parents=True)
        sparse = _sparse_list(slots=[(0.0, 1.0), (8.0, 1.0)], song_sec=20.0)
        (proj / "data" / "shot-list.json").write_text(json.dumps(sparse))

        ctx = AutopilotContext(
            project_slug="dense-smoke", project_dir=proj,
            run_id="test-run", song_path=None, source_glob=None, prompt="",
        )
        event = step_densify_shot_list(ctx)
        assert event.status == "ok", f"step status={event.status} msg={event.message}"

        updated = json.loads((proj / "data" / "shot-list.json").read_text())
        total_sec = sum(s["duration_frames"] for s in updated["shots"]) / updated["fps"]
        assert total_sec >= 19.0  # covers the 20s song (with rounding slack)

    def test_step_idempotent_on_already_densified(self, tmp_path: Path):
        from fandomforge.autopilot import AutopilotContext, step_densify_shot_list

        proj = tmp_path / "projects" / "dense-twice"
        (proj / "data").mkdir(parents=True)
        sparse = _sparse_list(slots=[(0.0, 1.0), (5.0, 1.0)], song_sec=10.0)
        sparse["shots"][0]["densified"] = True  # fake already-densified
        (proj / "data" / "shot-list.json").write_text(json.dumps(sparse))

        ctx = AutopilotContext(
            project_slug="dense-twice", project_dir=proj,
            run_id="test-run", song_path=None, source_glob=None, prompt="",
        )
        event = step_densify_shot_list(ctx)
        assert event.status == "skipped"
        assert "already densified" in event.message.lower()

    def test_step_skipped_when_no_shot_list(self, tmp_path: Path):
        from fandomforge.autopilot import AutopilotContext, step_densify_shot_list

        proj = tmp_path / "projects" / "no-shot-list"
        (proj / "data").mkdir(parents=True)
        ctx = AutopilotContext(
            project_slug="no-shot-list", project_dir=proj,
            run_id="test-run", song_path=None, source_glob=None, prompt="",
        )
        event = step_densify_shot_list(ctx)
        assert event.status == "skipped"
