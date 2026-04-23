"""Training journal tests — append atomicity, filter semantics, summary stats."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fandomforge.intelligence.training_journal import (
    RenderJournalEntry,
    append_entry,
    iter_entries,
    summary,
)


def _make_entry(**kwargs) -> RenderJournalEntry:
    defaults = dict(
        project_slug="test",
        generated_at="2026-04-21T00:00:00+00:00",
        edit_type="action",
        overall_score=90.0,
        overall_grade="A",
        tier="Exceptional",
    )
    defaults.update(kwargs)
    return RenderJournalEntry(**defaults)


class TestAppendAndIter:
    def test_append_then_iter(self, tmp_path: Path) -> None:
        p = tmp_path / "journal.jsonl"
        append_entry(_make_entry(project_slug="a", overall_score=90), path=p)
        append_entry(_make_entry(project_slug="b", overall_score=85), path=p)
        rows = list(iter_entries(p))
        assert len(rows) == 2
        assert rows[0].project_slug == "a"
        assert rows[1].project_slug == "b"

    def test_jsonl_format_one_per_line(self, tmp_path: Path) -> None:
        p = tmp_path / "journal.jsonl"
        append_entry(_make_entry(project_slug="a"), path=p)
        append_entry(_make_entry(project_slug="b"), path=p)
        lines = p.read_text().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            json.loads(line)  # each line must be valid JSON

    def test_missing_file_iter_yields_nothing(self, tmp_path: Path) -> None:
        assert list(iter_entries(tmp_path / "ghost.jsonl")) == []

    def test_malformed_lines_skipped(self, tmp_path: Path) -> None:
        p = tmp_path / "journal.jsonl"
        p.write_text('{"project_slug":"ok","overall_score":90.0,"generated_at":"2026-01-01T00:00:00+00:00"}\n')
        p.write_text(p.read_text() + "not-json\n")
        rows = list(iter_entries(p))
        assert len(rows) == 1

    def test_filter_by_bucket(self, tmp_path: Path) -> None:
        p = tmp_path / "journal.jsonl"
        append_entry(_make_entry(project_slug="a", edit_type="action"), path=p)
        append_entry(_make_entry(project_slug="b", edit_type="sad"), path=p)
        action_only = list(iter_entries(p, filter_bucket="action"))
        assert len(action_only) == 1
        assert action_only[0].project_slug == "a"

    def test_filter_by_min_score(self, tmp_path: Path) -> None:
        p = tmp_path / "journal.jsonl"
        append_entry(_make_entry(project_slug="low", overall_score=60), path=p)
        append_entry(_make_entry(project_slug="hi", overall_score=95), path=p)
        rows = list(iter_entries(p, min_score=80))
        assert len(rows) == 1
        assert rows[0].project_slug == "hi"


class TestSummary:
    def test_empty_journal(self, tmp_path: Path) -> None:
        s = summary(tmp_path / "empty.jsonl")
        assert s["total"] == 0

    def test_per_bucket_and_dimensions(self, tmp_path: Path) -> None:
        p = tmp_path / "journal.jsonl"
        append_entry(_make_entry(project_slug="a", edit_type="action",
                                  overall_score=90, dim_visual=100.0,
                                  dim_audio=90.0), path=p)
        append_entry(_make_entry(project_slug="b", edit_type="action",
                                  overall_score=80, dim_visual=80.0,
                                  dim_audio=70.0), path=p)
        append_entry(_make_entry(project_slug="c", edit_type="sad",
                                  overall_score=70, dim_visual=100.0,
                                  dim_audio=60.0), path=p)
        s = summary(p)
        assert s["total"] == 3
        assert s["per_bucket"]["action"]["count"] == 2
        assert s["per_bucket"]["action"]["avg_score"] == 85.0
        assert s["per_bucket"]["sad"]["count"] == 1
        assert s["dimension_averages"]["visual"] == pytest.approx(93.33, abs=0.1)

    def test_grade_distribution(self, tmp_path: Path) -> None:
        p = tmp_path / "journal.jsonl"
        append_entry(_make_entry(overall_grade="A+"), path=p)
        append_entry(_make_entry(overall_grade="A+"), path=p)
        append_entry(_make_entry(overall_grade="B"), path=p)
        s = summary(p)
        assert s["grade_distribution"]["A+"] == 2
        assert s["grade_distribution"]["B"] == 1


class TestForwardCompatibleDeserialize:
    def test_entries_with_extra_unknown_fields_still_load(self, tmp_path: Path) -> None:
        """A journal from a newer version of the engine has fields the
        current dataclass doesn't know about. Iter should drop them and
        still return usable entries."""
        p = tmp_path / "journal.jsonl"
        p.write_text(
            '{"project_slug":"x","generated_at":"2026-04-21T00:00:00+00:00",'
            '"overall_score":88.0,"future_field":"ignored"}\n',
            encoding="utf-8",
        )
        rows = list(iter_entries(p))
        assert len(rows) == 1
        assert rows[0].project_slug == "x"
        assert rows[0].overall_score == 88.0
