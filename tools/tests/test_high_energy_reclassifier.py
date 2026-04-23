"""Tests for the high_energy reclassifier."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fandomforge.intelligence.high_energy_reclassifier import (
    HIGH_ENERGY_THRESHOLDS,
    reclassify_high_energy,
    _forensic_score_for_high_energy,
)


def _fake_forensic(
    *,
    bpm: float,
    cpm_values: list[float],
    shot_durations: list[float],
    duration_sec: float = 120.0,
) -> dict:
    shots = [
        {"start_sec": sum(shot_durations[:i]),
         "end_sec": sum(shot_durations[:i + 1]),
         "duration_sec": d}
        for i, d in enumerate(shot_durations)
    ]
    return {
        "schema_version": 1,
        "source": {"video_id": "test", "duration_sec": duration_sec},
        "bucket": "action",
        "shots": shots,
        "music": {"bpm": bpm},
        "cut_timing": {"cpm_curve": cpm_values},
    }


def test_qualifies_with_hot_bpm_and_short_shots():
    f = _fake_forensic(bpm=140, cpm_values=[40, 40], shot_durations=[0.3, 0.3, 0.3, 0.3])
    ok, info = _forensic_score_for_high_energy(f, HIGH_ENERGY_THRESHOLDS)
    assert ok is True
    assert info["bpm"] == 140
    assert info["median_shot_sec"] == 0.3


def test_qualifies_with_high_cpm_even_if_bpm_low():
    f = _fake_forensic(bpm=100, cpm_values=[80, 90, 100], shot_durations=[0.3, 0.4, 0.3])
    ok, _ = _forensic_score_for_high_energy(f, HIGH_ENERGY_THRESHOLDS)
    assert ok is True


def test_rejected_if_shot_duration_too_long():
    f = _fake_forensic(bpm=160, cpm_values=[100], shot_durations=[1.5, 2.0])
    ok, info = _forensic_score_for_high_energy(f, HIGH_ENERGY_THRESHOLDS)
    assert ok is False
    assert info["median_shot_sec"] > HIGH_ENERGY_THRESHOLDS.max_median_shot_sec


def test_rejected_if_neither_bpm_nor_cpm_hot():
    f = _fake_forensic(bpm=90, cpm_values=[30, 35], shot_durations=[0.3, 0.3])
    ok, _ = _forensic_score_for_high_energy(f, HIGH_ENERGY_THRESHOLDS)
    assert ok is False


def test_reclassify_copies_qualifiers(tmp_path):
    action_dir = tmp_path / "action" / "forensic"
    action_dir.mkdir(parents=True)
    (tmp_path / "high_energy" / "forensic").mkdir(parents=True)

    hot = _fake_forensic(bpm=150, cpm_values=[90, 110], shot_durations=[0.3, 0.3, 0.3])
    cold = _fake_forensic(bpm=90, cpm_values=[30], shot_durations=[2.0, 1.5])

    (action_dir / "hot-vid.forensic.json").write_text(json.dumps(hot))
    (action_dir / "cold-vid.forensic.json").write_text(json.dumps(cold))

    res = reclassify_high_energy(references_dir=tmp_path)
    assert "hot-vid" in res["promoted"]
    assert "cold-vid" in res["skipped"]

    # Original stays in action
    assert (action_dir / "hot-vid.forensic.json").exists()
    # Copy lands in high_energy with bucket label updated
    he_copy = tmp_path / "high_energy" / "forensic" / "hot-vid.forensic.json"
    assert he_copy.exists()
    copied = json.loads(he_copy.read_text())
    assert copied["bucket"] == "high_energy"


def test_reclassify_idempotent(tmp_path):
    action_dir = tmp_path / "action" / "forensic"
    action_dir.mkdir(parents=True)
    (tmp_path / "high_energy" / "forensic").mkdir(parents=True)
    hot = _fake_forensic(bpm=150, cpm_values=[100], shot_durations=[0.3, 0.3])
    (action_dir / "hot.forensic.json").write_text(json.dumps(hot))

    res1 = reclassify_high_energy(references_dir=tmp_path)
    assert res1["promoted"] == ["hot"]

    # Second pass sees it as already_present
    res2 = reclassify_high_energy(references_dir=tmp_path)
    assert res2["promoted"] == []
    assert "hot" in res2["already_present"]


def test_reclassify_dry_run_does_not_write(tmp_path):
    action_dir = tmp_path / "action" / "forensic"
    action_dir.mkdir(parents=True)
    he_dir = tmp_path / "high_energy" / "forensic"
    he_dir.mkdir(parents=True)
    hot = _fake_forensic(bpm=150, cpm_values=[100], shot_durations=[0.3])
    (action_dir / "hot.forensic.json").write_text(json.dumps(hot))

    res = reclassify_high_energy(references_dir=tmp_path, dry_run=True)
    assert "hot" in res["promoted"]
    # Did not write to high_energy
    assert not (he_dir / "hot.forensic.json").exists()


def test_reclassify_missing_source_bucket_safe(tmp_path):
    res = reclassify_high_energy(references_dir=tmp_path)
    assert res == {"promoted": [], "skipped": [], "already_present": []}
