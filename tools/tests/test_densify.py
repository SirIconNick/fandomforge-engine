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
