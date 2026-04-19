"""Tests for cross-source action complement pairing."""

from __future__ import annotations

from fandomforge.intelligence.complement_matcher import (
    _extract_cues,
    _motion_continuity,
    _pair_score,
    apply_pairs_to_shot_list,
    build_complement_plan,
)


class TestMotionContinuity:
    def test_inverse_directions_are_perfect(self) -> None:
        # 0 (right) and 180 (left) — classic match-cut
        assert _motion_continuity(0.0, 180.0) == 1.0

    def test_same_direction_is_ok(self) -> None:
        assert _motion_continuity(90.0, 90.0) >= 0.6

    def test_orthogonal_is_mediocre(self) -> None:
        assert _motion_continuity(0.0, 90.0) <= 0.4

    def test_unknown_is_neutral(self) -> None:
        assert _motion_continuity(None, 90.0) == 0.5


class TestBuildComplementPlan:
    def _shot(self, **kwargs) -> dict:
        base = {
            "id": kwargs.get("id", "s1"),
            "act": kwargs.get("act", 1),
            "start_frame": kwargs.get("start_frame", 0),
            "duration_frames": kwargs.get("duration_frames", 24),
            "source_id": kwargs.get("source_id", "src1"),
            "source_timecode": kwargs.get("source_timecode", "0:00:01.000"),
            "role": kwargs.get("role", "action"),
            "description": kwargs.get("description", ""),
            "mood_tags": kwargs.get("mood_tags", []),
            "fandom": kwargs.get("fandom", ""),
            "framing": kwargs.get("framing", ""),
            "motion_vector": kwargs.get("motion_vector"),
        }
        return base

    def test_pairs_punch_thrown_with_received_different_source(self) -> None:
        shots = [
            self._shot(id="s1", source_id="src1", description="throws a punch",
                       mood_tags=["combat"], fandom="John Wick",
                       motion_vector=0.0, framing="CU"),
            self._shot(id="s2", source_id="src2", description="takes a hit, falls back",
                       role="reaction", mood_tags=["impact"], fandom="Mad Max",
                       motion_vector=180.0, framing="CU", start_frame=48),
        ]
        shot_list = {"shots": shots}
        plan = build_complement_plan(project_slug="t", shot_list=shot_list)
        assert len(plan["pairs"]) == 1
        pair = plan["pairs"][0]
        assert pair["thrown_shot_id"] == "s1"
        assert pair["received_shot_id"] == "s2"
        assert pair["kind"] == "punch"
        assert pair["score"] >= 0.7

    def test_same_source_never_pairs(self) -> None:
        shots = [
            self._shot(id="s1", source_id="src1", description="throws a punch",
                       mood_tags=["combat"]),
            self._shot(id="s2", source_id="src1", description="takes a hit",
                       role="reaction"),
        ]
        plan = build_complement_plan(project_slug="t", shot_list={"shots": shots})
        assert len(plan["pairs"]) == 0

    def test_unpaired_thrown_surfaces_when_no_match(self) -> None:
        shots = [
            self._shot(id="s1", source_id="src1", description="fires gun",
                       mood_tags=["combat"]),
            self._shot(id="s2", source_id="src2", description="throws a punch",
                       mood_tags=["combat"]),
        ]
        plan = build_complement_plan(project_slug="t", shot_list={"shots": shots})
        # No receives — both should show as unpaired_thrown
        assert len(plan["pairs"]) == 0
        assert len(plan["unpaired_thrown"]) == 2


class TestApplyPairsToShotList:
    def _shot(self, sid: str, duration_frames: int = 24) -> dict:
        return {
            "id": sid, "act": 1, "start_frame": 0,
            "duration_frames": duration_frames,
            "source_id": "s", "source_timecode": "0:00:01.000",
            "role": "action",
        }

    def test_no_pairs_returns_unchanged(self) -> None:
        shot_list = {"schema_version": 1, "fps": 24, "shots": [self._shot("s1")]}
        result = apply_pairs_to_shot_list(shot_list, {"pairs": []})
        assert result == shot_list

    def test_pair_moves_received_next_to_thrown(self) -> None:
        shot_list = {
            "schema_version": 1, "fps": 24,
            "shots": [
                self._shot("s1"),  # thrown
                self._shot("s2"),  # unrelated
                self._shot("s3"),  # received
                self._shot("s4"),
            ],
        }
        plan = {"pairs": [{"thrown_shot_id": "s1", "received_shot_id": "s3"}]}
        out = apply_pairs_to_shot_list(shot_list, plan)
        ids = [s["id"] for s in out["shots"]]
        assert ids == ["s1", "s3", "s2", "s4"]

    def test_start_frames_are_recontiguous(self) -> None:
        shot_list = {
            "schema_version": 1, "fps": 24,
            "shots": [
                self._shot("s1", duration_frames=48),
                self._shot("s2", duration_frames=24),
                self._shot("s3", duration_frames=12),
            ],
        }
        plan = {"pairs": [{"thrown_shot_id": "s1", "received_shot_id": "s3"}]}
        out = apply_pairs_to_shot_list(shot_list, plan)
        # s1 at 0 (48), s3 at 48 (12), s2 at 60 (24)
        assert out["shots"][0]["start_frame"] == 0
        assert out["shots"][1]["start_frame"] == 48
        assert out["shots"][2]["start_frame"] == 60

    def test_never_drops_shots(self) -> None:
        shot_list = {
            "schema_version": 1, "fps": 24,
            "shots": [self._shot(f"s{i}") for i in range(5)],
        }
        plan = {"pairs": [{"thrown_shot_id": "s0", "received_shot_id": "s4"}]}
        out = apply_pairs_to_shot_list(shot_list, plan)
        assert len(out["shots"]) == 5
        assert set(s["id"] for s in out["shots"]) == {"s0", "s1", "s2", "s3", "s4"}
