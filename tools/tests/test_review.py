"""Tests for the post-render review module.

Core pure-logic functions are exercised with mocked ffprobe / ffmpeg output.
The integration shape is covered by the action-legends live test (captured in
the memory file feedback_always_review_renders.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fandomforge.review import (
    DIMENSION_WEIGHTS,
    DimensionReport,
    ReviewReport,
    _dim_shot_list,
    _dim_structural,
    _overall_label,
    _roll_up,
    _ship_recommendation,
    overall_score,
    review_rendered_edit,
    score_to_letter,
)


class TestRollUp:
    def test_empty_is_pass(self) -> None:
        assert _roll_up([]) == "pass"

    def test_all_pass(self) -> None:
        assert _roll_up(["pass", "pass"]) == "pass"

    def test_any_warn_wins_over_pass(self) -> None:
        assert _roll_up(["pass", "warn", "pass"]) == "warn"

    def test_fail_dominates(self) -> None:
        assert _roll_up(["pass", "warn", "fail"]) == "fail"


class TestOverallLabel:
    def test_mapping(self) -> None:
        assert _overall_label("pass") == "green"
        assert _overall_label("warn") == "yellow"
        assert _overall_label("fail") == "red"


class TestStructural:
    def _mock_shot_list(self, shots: list[tuple[int, int]], fps: float = 24.0) -> dict:
        return {
            "fps": fps,
            "shots": [
                {"id": f"s{i:03d}", "start_frame": sf, "duration_frames": df}
                for i, (sf, df) in enumerate(shots, 1)
            ],
        }

    def test_duration_matches(self, tmp_path) -> None:
        video = tmp_path / "fake.mp4"; video.write_bytes(b"")
        shot_list = self._mock_shot_list([(0, 48), (48, 48)])
        # 48+48 frames / 24fps = 4.00s
        d = _dim_structural(video, duration_sec=4.0, shot_list=shot_list)
        assert d.verdict == "pass"
        assert d.measurements["expected_duration_sec"] == 4.0

    def test_minor_delta_is_warn(self, tmp_path) -> None:
        video = tmp_path / "fake.mp4"; video.write_bytes(b"")
        shot_list = self._mock_shot_list([(0, 48), (48, 48)])
        d = _dim_structural(video, duration_sec=4.2, shot_list=shot_list)
        assert d.verdict == "warn"

    def test_huge_delta_is_fail(self, tmp_path) -> None:
        video = tmp_path / "fake.mp4"; video.write_bytes(b"")
        shot_list = self._mock_shot_list([(0, 48), (48, 48)])
        d = _dim_structural(video, duration_sec=60.0, shot_list=shot_list)
        assert d.verdict == "fail"

    def test_missing_shot_list_warns(self, tmp_path) -> None:
        video = tmp_path / "fake.mp4"; video.write_bytes(b"")
        d = _dim_structural(video, duration_sec=4.0, shot_list=None)
        assert d.verdict == "warn"


class TestShotList:
    def _mk(self, shots: list[dict], warnings: list[str] | None = None) -> dict:
        return {"shots": shots, "fps": 24.0, **({"warnings": warnings} if warnings else {})}

    def test_unique_shots_pass(self) -> None:
        # Four sources balanced so no one source dominates > 50%
        shots = [
            {"id": "s001", "source_id": "a", "source_timecode": "0:00:01.000"},
            {"id": "s002", "source_id": "b", "source_timecode": "0:00:02.000"},
            {"id": "s003", "source_id": "c", "source_timecode": "0:00:03.000"},
            {"id": "s004", "source_id": "d", "source_timecode": "0:00:04.000"},
        ]
        d = _dim_shot_list(self._mk(shots))
        assert d.verdict == "pass"
        assert d.measurements["unique_shots"] == 4
        assert d.measurements["accidental_reuse"] == 0

    def test_accidental_reuse_fails(self) -> None:
        shots = [
            {"id": "s001", "source_id": "a", "source_timecode": "0:00:01.000"},
            {"id": "s002", "source_id": "b", "source_timecode": "0:00:02.000"},
            {"id": "s003", "source_id": "c", "source_timecode": "0:00:03.000"},
            {"id": "s004", "source_id": "a", "source_timecode": "0:00:01.000"},  # dup of s001
        ]
        d = _dim_shot_list(self._mk(shots))
        assert d.verdict == "fail"
        assert d.measurements["accidental_reuse"] == 1

    def test_intentional_callback_is_ok(self) -> None:
        shots = [
            {"id": "s001", "source_id": "a", "source_timecode": "0:00:01.000"},
            {"id": "s002", "source_id": "b", "source_timecode": "0:00:02.000"},
            {"id": "s003", "source_id": "c", "source_timecode": "0:00:03.000"},
            {"id": "s004", "source_id": "a", "source_timecode": "0:00:01.000",
             "intent": "callback", "callback_of": "s001"},
        ]
        d = _dim_shot_list(self._mk(shots))
        # same source+timecode but marked as callback → not flagged as reuse
        assert d.measurements["intentional_callbacks"] == 1
        assert d.measurements["accidental_reuse"] == 0
        # verdict is pass because no accidental reuse and no source dominates
        assert d.verdict == "pass"

    def test_one_source_dominates_warns(self) -> None:
        # 10 shots, 9 from the same source = 90% — warns
        shots = [
            {"id": f"s{i:03d}", "source_id": "bully", "source_timecode": f"0:00:{i:02d}.000"}
            for i in range(9)
        ]
        shots.append(
            {"id": "s010", "source_id": "other", "source_timecode": "0:00:10.000"}
        )
        d = _dim_shot_list(self._mk(shots))
        assert d.verdict == "warn"
        assert any("takes 90%" in f for f in d.findings)

    def test_proposer_warnings_surface(self) -> None:
        shots = [
            {"id": "s001", "source_id": "a", "source_timecode": "0:00:01.000"},
        ]
        d = _dim_shot_list(self._mk(shots, warnings=["reuse-dedupe tolerance widened"]))
        assert d.verdict == "warn"
        assert any("proposer warning" in f for f in d.findings)


class TestShipRecommendation:
    def _dim(self, name: str, verdict: str) -> DimensionReport:
        return DimensionReport(name=name, verdict=verdict)

    def test_all_pass_recommends_ship(self) -> None:
        rec = _ship_recommendation("pass", [self._dim("a", "pass")])
        assert "Green across the board" in rec

    def test_fail_recommends_no_ship(self) -> None:
        rec = _ship_recommendation("fail", [
            self._dim("technical", "pass"),
            self._dim("visual", "fail"),
        ])
        assert "Do NOT ship" in rec
        assert "visual" in rec

    def test_warn_recommends_eyeball(self) -> None:
        rec = _ship_recommendation("warn", [
            self._dim("visual", "warn"),
            self._dim("audio", "pass"),
        ])
        assert "eyeball" in rec.lower()
        assert "visual" in rec


class TestReviewReportShape:
    def test_to_dict_is_json_safe(self, tmp_path) -> None:
        import json
        r = ReviewReport(
            project_slug="t",
            video_path=str(tmp_path / "x.mp4"),
            generated_at="2026-04-19T00:00:00+00:00",
            overall="green",
            overall_verdict="pass",
            dimensions=[DimensionReport(name="technical", verdict="pass")],
            ship_recommendation="ok",
        )
        d = r.to_dict()
        # Round-trip through JSON — proves no non-serializable objects.
        json.loads(json.dumps(d))
        assert d["overall"] == "green"
        assert d["dimensions"][0]["name"] == "technical"


class TestReviewRenderedEditMissingVideo:
    def test_raises_if_video_missing(self, tmp_path) -> None:
        proj = tmp_path / "proj"
        (proj / "exports").mkdir(parents=True)
        with pytest.raises(FileNotFoundError):
            review_rendered_edit(proj, video_name="nope.mp4")


class TestGradeAndScore:
    def _dims_all(self, verdict: str, findings_per: int = 0) -> list[DimensionReport]:
        out = []
        for name in DIMENSION_WEIGHTS:
            findings = [f"f{i}" for i in range(findings_per)]
            out.append(DimensionReport(name=name, verdict=verdict, findings=findings))
        return out

    def test_all_pass_no_findings_is_perfect(self) -> None:
        score = overall_score(self._dims_all("pass", 0))
        assert score == 100.0
        assert score_to_letter(score) == "A+"

    def test_all_warn_one_finding_each_is_mid_tier(self) -> None:
        # pass base 1.00 - 0.06 * 1 finding = 0.94 → if warn: 0.75 - 0.06 = 0.69 → 69
        score = overall_score(self._dims_all("warn", 1))
        assert 65 <= score <= 75, score
        letter = score_to_letter(score)
        assert letter.startswith("C") or letter.startswith("D")

    def test_all_fail_is_F(self) -> None:
        score = overall_score(self._dims_all("fail", 2))
        assert score <= 20
        assert score_to_letter(score) == "F"

    def test_one_visual_warn_is_A_range(self) -> None:
        """Visual is the heaviest of the legacy dims (0.22 after Phase 4
        rebalance). Other four pass clean. With weight normalization across
        the legacy 5 dims (sum 0.72), visual contributes (0.22/0.72)*69 = 21.1.
        Others contribute (0.50/0.72)*100 = 69.4. Total ~90.5 → A-/A range."""
        dims = self._dims_all("pass", 0)
        for d in dims:
            if d.name == "visual":
                d.verdict = "warn"
                d.findings = ["dark segment 0.5s"]
        score = overall_score(dims)
        letter = score_to_letter(score)
        # With Phase 4's rebalanced weights, a single visual warn lands in A-/A range
        assert letter in ("A-", "A", "B+"), f"got {letter} from score {score}"

    def test_letter_bands_are_monotonic(self) -> None:
        """Sanity — higher scores should never map to worse letters."""
        letters = [score_to_letter(s) for s in (100, 95, 90, 85, 80, 75, 70, 65, 60, 50)]
        # Strict ordering of letter ranks
        rank = {
            "A+": 12, "A": 11, "A-": 10,
            "B+": 9, "B": 8, "B-": 7,
            "C+": 6, "C": 5, "C-": 4,
            "D+": 3, "D": 2, "D-": 1,
            "F": 0,
        }
        ranks = [rank[l] for l in letters]
        assert ranks == sorted(ranks, reverse=True)

    def test_boundary_scores_resolve_to_expected_letters(self) -> None:
        assert score_to_letter(97.0) == "A+"
        assert score_to_letter(96.99) == "A"
        assert score_to_letter(93.0) == "A"
        assert score_to_letter(92.99) == "A-"
        assert score_to_letter(60.0) == "D-"
        assert score_to_letter(59.99) == "F"

    def test_dimension_score_falls_with_findings(self) -> None:
        no_findings = DimensionReport(name="visual", verdict="warn", findings=[])
        lots = DimensionReport(
            name="visual", verdict="warn", findings=[f"f{i}" for i in range(10)]
        )
        assert lots.score < no_findings.score

    def test_report_to_dict_includes_grade_and_score(self) -> None:
        import json
        dims = [DimensionReport(name=n, verdict="pass") for n in DIMENSION_WEIGHTS]
        r = ReviewReport(
            project_slug="t", video_path="x.mp4",
            generated_at="2026-04-19T00:00:00+00:00",
            overall="green", overall_verdict="pass",
            score=100.0, grade="A+",
            dimensions=dims, ship_recommendation="ok",
        )
        d = r.to_dict()
        json.loads(json.dumps(d))
        assert d["grade"] == "A+"
        assert d["score"] == 100.0
        # Per-dimension score surfaces too
        assert d["dimensions"][0]["score"] == 100.0
