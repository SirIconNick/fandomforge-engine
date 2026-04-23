"""Unit tests for the human-correction journal."""

from __future__ import annotations

import os

import pytest

from fandomforge.intelligence.corrections_journal import (
    CorrectionEntry,
    append_correction,
    corrections_path,
    corrections_summary,
    iter_corrections,
    latest_correction_for,
)


@pytest.fixture(autouse=True)
def _isolate_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("FF_CORRECTIONS_JOURNAL", str(tmp_path / "corrections.jsonl"))
    yield


def test_append_and_iter():
    entry = CorrectionEntry(
        forensic_id="abc",
        corrected_bucket="action",
        corrected_craft_weights={"ramp": 1.0},
    )
    path = append_correction(entry)
    assert path.exists()

    entries = list(iter_corrections())
    assert len(entries) == 1
    assert entries[0].forensic_id == "abc"
    assert entries[0].corrected_bucket == "action"


def test_timestamp_auto_populated():
    entry = CorrectionEntry(forensic_id="x", corrected_bucket="sad")
    assert entry.timestamp != ""
    assert "T" in entry.timestamp  # ISO-8601


def test_latest_correction_for_picks_newest():
    e1 = CorrectionEntry(
        forensic_id="v",
        corrected_bucket="action",
        corrected_craft_weights={"ramp": 0.2},
        timestamp="2026-01-01T00:00:00+00:00",
    )
    e2 = CorrectionEntry(
        forensic_id="v",
        corrected_bucket="hype_trailer",
        corrected_craft_weights={"ramp": 0.9},
        timestamp="2026-06-01T00:00:00+00:00",
    )
    append_correction(e1)
    append_correction(e2)
    latest = latest_correction_for("v")
    assert latest is not None
    assert latest.corrected_bucket == "hype_trailer"
    assert latest.corrected_craft_weights["ramp"] == 0.9


def test_summary_counts_and_reclassifications():
    append_correction(CorrectionEntry(
        forensic_id="a",
        original_bucket="multifandom",
        corrected_bucket="action",
    ))
    append_correction(CorrectionEntry(
        forensic_id="b",
        original_bucket="action",
        corrected_bucket="action",  # no reclassification
    ))
    append_correction(CorrectionEntry(
        forensic_id="c",
        original_bucket="horror",
        corrected_bucket="sad",
    ))
    s = corrections_summary()
    assert s["total"] == 3
    assert s["per_bucket"]["action"] == 2
    assert s["per_bucket"]["sad"] == 1
    assert len(s["reclassifications"]) == 2


def test_iter_returns_empty_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "FF_CORRECTIONS_JOURNAL",
        str(tmp_path / "nonexistent.jsonl"),
    )
    assert list(iter_corrections()) == []
    assert corrections_summary()["total"] == 0


def test_corrupt_line_is_skipped(tmp_path, monkeypatch):
    path = tmp_path / "corrections.jsonl"
    monkeypatch.setenv("FF_CORRECTIONS_JOURNAL", str(path))
    append_correction(CorrectionEntry(
        forensic_id="good", corrected_bucket="action",
    ))
    with path.open("a") as f:
        f.write("not-json-at-all\n")
    append_correction(CorrectionEntry(
        forensic_id="also-good", corrected_bucket="sad",
    ))
    entries = list(iter_corrections())
    # The corrupt line is silently skipped; both valid rows survive.
    assert len(entries) == 2
