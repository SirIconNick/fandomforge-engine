"""Tests for resolve_diff (Phase 7.1) + prior_updater (Phase 7.2 + 7.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from fandomforge.intelligence.prior_updater import (
    DEFAULT_MIN_DIFFS,
    aggregate_signals,
    append_to_journal,
    load_journal,
    update_priors,
)
from fandomforge.intelligence.resolve_diff import (
    DiffReport,
    TimelineCut,
    diff_cuts,
    parse_fcpxml,
    shot_list_to_cuts,
)


def _cut(src: str, in_sec: float, dur: float = 1.0, pos: float = 0.0) -> TimelineCut:
    return TimelineCut(
        timeline_position_sec=pos, source_id=src,
        source_in_sec=in_sec, source_out_sec=in_sec + dur, duration_sec=dur,
    )


class TestDiffCuts:
    def test_identical_lists_no_diff(self):
        cuts = [_cut("a", 0), _cut("b", 5)]
        report = diff_cuts(cuts, list(cuts))
        assert not report.cuts_added
        assert not report.cuts_removed
        assert not report.cuts_duration_changed

    def test_added_cuts_detected(self):
        orig = [_cut("a", 0)]
        edited = [_cut("a", 0), _cut("b", 5)]
        report = diff_cuts(orig, edited)
        assert len(report.cuts_added) == 1
        assert report.cuts_added[0].source_id == "b"

    def test_removed_cuts_detected(self):
        orig = [_cut("a", 0), _cut("b", 5)]
        edited = [_cut("a", 0)]
        report = diff_cuts(orig, edited)
        assert len(report.cuts_removed) == 1
        assert report.cuts_removed[0].source_id == "b"

    def test_duration_change_detected(self):
        orig = [_cut("a", 0, dur=2.0)]
        edited = [_cut("a", 0, dur=4.0)]
        report = diff_cuts(orig, edited)
        assert len(report.cuts_duration_changed) == 1
        assert report.cuts_duration_changed[0]["edited_duration_sec"] == 4.0

    def test_reorder_detected(self):
        orig = [_cut("a", 0), _cut("b", 5)]
        edited = [_cut("b", 5), _cut("a", 0)]
        report = diff_cuts(orig, edited)
        assert report.cuts_reordered


class TestShotListConversion:
    def test_shot_list_to_cuts_basic(self):
        sl = {
            "fps": 24,
            "shots": [
                {"id": "s1", "start_frame": 0, "duration_frames": 24,
                 "source_id": "src1", "source_timecode": "0:00:10.000"},
                {"id": "s2", "start_frame": 48, "duration_frames": 48,
                 "source_id": "src2", "source_timecode": "0:01:30.500"},
            ],
        }
        cuts = shot_list_to_cuts(sl)
        assert len(cuts) == 2
        assert cuts[0].timeline_position_sec == 0.0
        assert cuts[0].source_in_sec == 10.0
        assert cuts[0].duration_sec == 1.0
        assert cuts[1].source_in_sec == 90.5
        assert cuts[1].duration_sec == 2.0


class TestFcpxml:
    def test_minimal_fcpxml_parse(self, tmp_path: Path):
        fcpxml = """<?xml version="1.0" encoding="UTF-8"?>
        <fcpxml>
          <library>
            <project>
              <sequence>
                <spine>
                  <clip name="src1" offset="0s" start="10s" duration="2s" />
                  <clip name="src2" offset="2s" start="50s" duration="3s" />
                </spine>
              </sequence>
            </project>
          </library>
        </fcpxml>
        """
        p = tmp_path / "t.fcpxml"
        p.write_text(fcpxml)
        cuts = parse_fcpxml(p)
        assert len(cuts) == 2
        assert cuts[0].duration_sec == 2.0
        assert cuts[1].source_in_sec == 50.0


class TestPriorUpdater:
    def test_journal_round_trip(self, tmp_path: Path):
        report = DiffReport(
            project_slug="demo",
            original_cut_count=2, edited_cut_count=2,
            cuts_added=[_cut("new", 0)],
        )
        append_to_journal(report.to_dict(), tmp_path)
        journal = load_journal(tmp_path)
        assert len(journal) == 1
        assert journal[0]["project_slug"] == "demo"

    def test_aggregate_signals(self, tmp_path: Path):
        for src, action in [("a", "added"), ("a", "added"), ("b", "removed")]:
            r = DiffReport(project_slug="demo", original_cut_count=2, edited_cut_count=2)
            if action == "added":
                r.cuts_added = [_cut(src, 0)]
            else:
                r.cuts_removed = [_cut(src, 0)]
            append_to_journal(r.to_dict(), tmp_path)
        journal = load_journal(tmp_path)
        signals = aggregate_signals(journal)
        assert signals["source_add_counts"]["a"] == 2
        assert signals["source_remove_counts"]["b"] == 1

    def test_retrain_refuses_below_threshold(self, tmp_path: Path):
        # Only 1 diff in journal
        r = DiffReport(project_slug="demo", original_cut_count=1, edited_cut_count=1)
        append_to_journal(r.to_dict(), tmp_path)
        result = update_priors(tmp_path, min_diffs=5)
        assert not result.get("applied")
        assert "1 diffs" in result["reason"]

    def test_retrain_with_enough_diffs_runs_dry(self, tmp_path: Path):
        for i in range(6):
            r = DiffReport(project_slug="demo", original_cut_count=1, edited_cut_count=1)
            r.cuts_removed = [_cut("dropped", float(i))]
            append_to_journal(r.to_dict(), tmp_path)
        result = update_priors(tmp_path, min_diffs=5, apply=False)
        assert result["diff_count"] == 6
        assert result.get("dry_run") is True
        assert any(n["source_id"] == "dropped" for n in result["source_nudges"])

    def test_retrain_apply_writes_file(self, tmp_path: Path):
        for i in range(6):
            r = DiffReport(project_slug="demo", original_cut_count=1, edited_cut_count=1)
            r.cuts_added = [_cut("loved", float(i))]
            append_to_journal(r.to_dict(), tmp_path)
        result = update_priors(tmp_path, min_diffs=5, apply=True)
        assert result.get("applied") is True
        bias_file = tmp_path / "references" / "priors" / "user-bias.json"
        assert bias_file.exists()
        import json
        data = json.loads(bias_file.read_text())
        assert "loved" in data["source_bias"]
        assert data["source_bias"]["loved"] > 0
