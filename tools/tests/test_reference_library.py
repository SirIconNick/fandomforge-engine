"""Tests for the reference-edit library.

The download + scene-detect paths require network / scenedetect at runtime.
These tests mock both and exercise the aggregation logic on synthetic inputs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fandomforge.intelligence.reference_library import (
    aggregate_priors,
    load_priors,
    references_root,
)


class TestAggregatePriors:
    def test_median_shot_duration_from_multiple_videos(self) -> None:
        videos = [
            {"metrics": {"shot_count": 10, "avg_shot_duration_sec": 1.0,
                         "median_shot_duration_sec": 1.0, "cuts_per_minute": 60}},
            {"metrics": {"shot_count": 10, "avg_shot_duration_sec": 2.0,
                         "median_shot_duration_sec": 2.0, "cuts_per_minute": 30}},
            {"metrics": {"shot_count": 10, "avg_shot_duration_sec": 3.0,
                         "median_shot_duration_sec": 3.0, "cuts_per_minute": 20}},
        ]
        priors = aggregate_priors(videos)
        assert priors["median_shot_duration_sec"] == 2.0
        assert priors["cuts_per_minute"] == pytest.approx(36.67, abs=0.1)

    def test_empty_videos_yields_safe_defaults(self) -> None:
        priors = aggregate_priors([])
        assert priors["median_shot_duration_sec"] > 0
        assert priors["cuts_per_minute"] > 0
        assert priors["typical_act_pacing_pct"] == [25.0, 45.0, 30.0]

    def test_shot_duration_range_is_p10_p90(self) -> None:
        videos = [
            {"metrics": {
                "shot_count": 100, "avg_shot_duration_sec": 1.5,
                "median_shot_duration_sec": 1.5, "cuts_per_minute": 40,
            }},
        ]
        priors = aggregate_priors(videos)
        # all same value, so p10 == p90
        assert priors["shot_duration_range_sec"] == [1.5, 1.5]


class TestLoadPriors:
    def test_returns_none_when_root_missing(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("FF_REFERENCES_DIR", str(tmp_path / "nope"))
        assert load_priors() is None

    def test_loads_tagged_priors(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("FF_REFERENCES_DIR", str(tmp_path))
        (tmp_path / "test-tag").mkdir(parents=True)
        priors = {
            "schema_version": 1,
            "video_count": 1,
            "videos": [],
            "priors": {
                "median_shot_duration_sec": 1.8,
                "cuts_per_minute": 28.0,
                "typical_act_pacing_pct": [25, 45, 30],
            },
        }
        (tmp_path / "test-tag" / "reference-priors.json").write_text(json.dumps(priors))
        loaded = load_priors("test-tag")
        assert loaded is not None
        assert loaded["priors"]["median_shot_duration_sec"] == 1.8

    def test_returns_newest_when_tag_omitted(self, tmp_path: Path, monkeypatch) -> None:
        import os
        import time
        monkeypatch.setenv("FF_REFERENCES_DIR", str(tmp_path))

        old = tmp_path / "old"
        new = tmp_path / "new"
        for t in (old, new):
            t.mkdir()
        base = {"schema_version": 1, "video_count": 0, "videos": [],
                "priors": {"median_shot_duration_sec": 2.0, "cuts_per_minute": 20}}
        old_path = old / "reference-priors.json"
        new_path = new / "reference-priors.json"
        old_path.write_text(json.dumps({**base, "tag": "old"}))
        # ensure different mtimes
        os.utime(old_path, (time.time() - 100, time.time() - 100))
        new_path.write_text(json.dumps({**base, "tag": "new"}))

        loaded = load_priors()
        assert loaded is not None
        assert loaded["tag"] == "new"


class TestReferencesRoot:
    def test_respects_env_override(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("FF_REFERENCES_DIR", str(tmp_path / "refs"))
        assert references_root() == tmp_path / "refs"

    def test_default_under_home(self, monkeypatch) -> None:
        monkeypatch.delenv("FF_REFERENCES_DIR", raising=False)
        root = references_root()
        assert root.name == "references"
        assert root.parent.name == ".fandomforge"
