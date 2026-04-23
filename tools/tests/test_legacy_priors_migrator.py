"""Tests for the legacy per-playlist → per-bucket priors migrator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fandomforge.intelligence.legacy_priors_migrator import (
    BUCKET_MAPPING,
    migrate_all,
    migrate_bucket,
)


def _write_old_priors(
    refs_dir: Path,
    playlist_name: str,
    *,
    cuts_on_beat: float,
    cpm: float,
    video_count: int,
    video_ids: list[str] | None = None,
) -> None:
    d = refs_dir / playlist_name
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "tag": playlist_name,
        "source_playlists": [f"https://example.com/pl/{playlist_name}"],
        "video_count": video_count,
        "videos": [{"id": vid, "title": vid} for vid in (video_ids or [])],
        "priors": {
            "cuts_on_beat_pct_mean": cuts_on_beat,
            "cuts_per_minute": cpm,
            "median_shot_duration_sec": round(60.0 / cpm, 3) if cpm else 0.0,
            "shot_duration_range_sec": [0.5, 2.0],
            "avg_luma_mean": 0.3,
            "dark_shot_pct_mean": 15.0,
            "bright_shot_pct_mean": 20.0,
            "saturation_mean_mean": 0.4,
        },
        "generated_at": "2026-04-01T00:00:00Z",
    }
    (d / "reference-priors.json").write_text(json.dumps(payload))


def test_migrate_bucket_produces_schema_fields(tmp_path):
    _write_old_priors(
        tmp_path, "tribute-pl1",
        cuts_on_beat=62.0, cpm=50.0, video_count=15,
        video_ids=["abc", "def", "ghi"],
    )
    _write_old_priors(
        tmp_path, "tribute-pl2",
        cuts_on_beat=58.0, cpm=45.0, video_count=10,
        video_ids=["jkl"],
    )
    out = migrate_bucket(
        "tribute", ["tribute-pl1", "tribute-pl2"],
        references_dir=tmp_path, force=True,
    )
    assert out is not None
    assert out.exists()

    data = json.loads(out.read_text())
    assert data["bucket"] == "tribute"
    assert data["sample_count"] == 25  # 15 + 10
    assert data["consensus_edit_type"] == "tribute"
    assert data["mined_priors"]["cuts_on_beat_pct_mean"] == pytest.approx(0.60, abs=1e-2)
    assert data["consensus_target_cpm_range"] == [45.0, 50.0]
    assert set(data["video_ids"]) == {"abc", "def", "ghi", "jkl"}
    assert "consensus_craft_weights" in data
    assert data["sample_source"] == "legacy-migration"


def test_migrate_bucket_idempotent_skip(tmp_path):
    _write_old_priors(tmp_path, "dance-singles", cuts_on_beat=55, cpm=34, video_count=5)
    first = migrate_bucket("dance", ["dance-singles"], references_dir=tmp_path)
    # Overwrite the generated file with a sentinel; re-running without
    # force should leave the sentinel intact.
    first.write_text('{"sentinel": true}')
    migrate_bucket("dance", ["dance-singles"], references_dir=tmp_path, force=False)
    data = json.loads(first.read_text())
    assert data == {"sentinel": True}


def test_migrate_bucket_force_overwrites(tmp_path):
    _write_old_priors(tmp_path, "dance-singles", cuts_on_beat=55, cpm=34, video_count=5)
    out = migrate_bucket("dance", ["dance-singles"], references_dir=tmp_path, force=True)
    out.write_text('{"sentinel": true}')
    migrate_bucket("dance", ["dance-singles"], references_dir=tmp_path, force=True)
    data = json.loads(out.read_text())
    assert "bucket" in data
    assert data["bucket"] == "dance"


def test_missing_playlists_returns_none(tmp_path):
    out = migrate_bucket("nowhere", ["does-not-exist"], references_dir=tmp_path)
    assert out is None


def test_percentage_normalized_to_0_1(tmp_path):
    """Old priors store cuts_on_beat as 0-100; new format uses 0-1."""
    _write_old_priors(tmp_path, "sad-pl1", cuts_on_beat=80.0, cpm=20, video_count=12)
    out = migrate_bucket("sad", ["sad-pl1"], references_dir=tmp_path, force=True)
    data = json.loads(out.read_text())
    assert data["mined_priors"]["cuts_on_beat_pct_mean"] == pytest.approx(0.80, abs=1e-2)


def test_migrate_all_runs_every_bucket(tmp_path):
    # Seed at least one playlist for every bucket in BUCKET_MAPPING so
    # the bulk migration touches each.
    for bucket, playlists in BUCKET_MAPPING.items():
        for pl in playlists[:1]:  # one per bucket is enough
            _write_old_priors(tmp_path, pl, cuts_on_beat=50, cpm=30, video_count=5)

    results = migrate_all(references_dir=tmp_path, force=True)
    assert set(results.keys()) == set(BUCKET_MAPPING.keys())
    for bucket, path in results.items():
        assert path is not None, f"bucket {bucket} produced no report"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["bucket"] == bucket


def test_emotional_bucket_seeded_from_sad_plus_tribute(tmp_path):
    """The emotional bucket has no dedicated playlist — it aggregates
    from sad + tribute. Both must contribute."""
    _write_old_priors(tmp_path, "sad-pl1", cuts_on_beat=70, cpm=25, video_count=10)
    _write_old_priors(tmp_path, "sad-singles", cuts_on_beat=65, cpm=28, video_count=5)
    _write_old_priors(tmp_path, "tribute-pl1", cuts_on_beat=60, cpm=40, video_count=15)
    _write_old_priors(tmp_path, "tribute-pl2", cuts_on_beat=55, cpm=45, video_count=12)
    _write_old_priors(tmp_path, "tribute-pl3", cuts_on_beat=62, cpm=42, video_count=13)

    out = migrate_bucket(
        "emotional", BUCKET_MAPPING["emotional"],
        references_dir=tmp_path, force=True,
    )
    data = json.loads(out.read_text())
    # Sum of all 5 playlists
    assert data["sample_count"] == 10 + 5 + 15 + 12 + 13
