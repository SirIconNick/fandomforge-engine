"""Tests for Phase 0.5.2 + 0.5.3 corpus expansion code:
edit_type_for_tag, list_playlist_metadata_only (mocked),
aggregate_priors_per_bucket, load_per_bucket_priors.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fandomforge.intelligence.reference_library import (
    TAG_PREFIX_TO_EDIT_TYPE,
    aggregate_priors_per_bucket,
    edit_type_for_tag,
    load_per_bucket_priors,
    list_playlist_metadata_only,
)


@pytest.fixture(autouse=True)
def isolate_refs(tmp_path: Path, monkeypatch):
    """Pin FF_REFERENCES_DIR to a temp dir for every test so we don't
    pollute the user's real corpus."""
    monkeypatch.setenv("FF_REFERENCES_DIR", str(tmp_path))
    yield


class TestEditTypeForTag:
    def test_action_tag(self):
        assert edit_type_for_tag("action-pl1") == "action"

    def test_dance_tag(self):
        assert edit_type_for_tag("dance-pl3") == "dance_movement"

    def test_sad_tag(self):
        assert edit_type_for_tag("sad-singles") == "sad_emotional"

    def test_dialogue_tag(self):
        assert edit_type_for_tag("dialogue-pl1") == "dialogue_narrative"

    def test_mixed_returns_none(self):
        assert edit_type_for_tag("mixed-pl5") is None

    def test_unknown_prefix_returns_none(self):
        assert edit_type_for_tag("nonsense-pl1") is None

    def test_empty_returns_none(self):
        assert edit_type_for_tag("") is None

    def test_every_known_prefix_maps_or_none(self):
        for prefix, expected in TAG_PREFIX_TO_EDIT_TYPE.items():
            assert edit_type_for_tag(f"{prefix}-pl1") == expected


class TestListPlaylistMetadataOnly:
    """list_playlist_metadata_only enumerates a playlist and fetches
    per-video metadata WITHOUT downloading. Mocked here so tests don't
    require network."""

    def test_top_n_capped(self):
        from fandomforge.intelligence import reference_library
        with patch.object(reference_library, "list_playlist_entries", return_value=[
            {"id": f"v{i}", "title": f"Video {i}",
             "url": f"https://www.youtube.com/watch?v=v{i}",
             "duration_sec": 180.0}
            for i in range(50)
        ]), patch.object(reference_library, "fetch_youtube_metadata",
                         side_effect=lambda url: {
                             "view_count": 1000 - int(url[-2:].replace("v", "")) * 10,
                             "like_count": 50, "like_ratio": 0.05,
                             "duration_sec": 180.0, "channel": "ch", "upload_date": "20240101",
                             "title": "Video",
                         }):
            results = list_playlist_metadata_only("https://example/playlist", top_n=10)
        assert len(results) == 10

    def test_sorted_by_view_count_desc(self):
        from fandomforge.intelligence import reference_library

        def _meta_for(url: str) -> dict:
            vc_table = {"low": 100, "high": 50000, "mid": 5000}
            vid = url.rsplit("=", 1)[-1]
            return {
                "view_count": vc_table.get(vid, 0),
                "like_count": 0, "like_ratio": None, "duration_sec": 180.0,
                "channel": "ch", "upload_date": "20240101", "title": "X",
            }

        with patch.object(reference_library, "list_playlist_entries", return_value=[
            {"id": "low", "title": "Low",
             "url": "https://www.youtube.com/watch?v=low", "duration_sec": 180.0},
            {"id": "high", "title": "High",
             "url": "https://www.youtube.com/watch?v=high", "duration_sec": 180.0},
            {"id": "mid", "title": "Mid",
             "url": "https://www.youtube.com/watch?v=mid", "duration_sec": 180.0},
        ]), patch.object(reference_library, "fetch_youtube_metadata", side_effect=_meta_for):
            results = list_playlist_metadata_only("https://example/playlist", top_n=3)
        assert [r["id"] for r in results] == ["high", "mid", "low"]

    def test_handles_unfetchable_metadata(self):
        from fandomforge.intelligence import reference_library
        with patch.object(reference_library, "list_playlist_entries", return_value=[
            {"id": "ok", "title": "OK",
             "url": "https://www.youtube.com/watch?v=ok", "duration_sec": 180.0},
            {"id": "bad", "title": "Bad",
             "url": "https://www.youtube.com/watch?v=bad", "duration_sec": 0.0},
        ]), patch.object(reference_library, "fetch_youtube_metadata", side_effect=[
            {"view_count": 1000, "like_count": 50, "like_ratio": 0.05,
             "duration_sec": 180.0, "channel": "c", "upload_date": "x", "title": "OK"},
            None,  # second video has no metadata
        ]):
            results = list_playlist_metadata_only("https://example/playlist", top_n=5)
        assert len(results) == 2
        assert results[0]["metadata_available"] is True
        assert results[1]["metadata_available"] is False

    def test_empty_playlist_returns_empty(self):
        from fandomforge.intelligence import reference_library
        with patch.object(reference_library, "list_playlist_entries", return_value=[]):
            results = list_playlist_metadata_only("https://example/empty")
        assert results == []


class TestAggregatePriorsPerBucket:
    def _write_tag(self, refs_root: Path, tag: str, video_count: int):
        d = refs_root / tag
        d.mkdir(parents=True)
        priors = {
            "schema_version": 1, "tag": tag, "video_count": video_count,
            "videos": [
                {"id": f"v{i}", "title": f"V{i}",
                 "metrics": {"shot_count": 50, "avg_shot_duration_sec": 1.0,
                             "median_shot_duration_sec": 0.95,
                             "duration_sec": 60, "cuts_per_minute": 50},
                 "youtube": {"view_count": 1000, "like_count": 50, "like_ratio": 0.05},
                 "quality": {"quality_score": 70, "quality_tier": "B"}}
                for i in range(video_count)
            ],
            "priors": {
                "median_shot_duration_sec": 0.95,
                "cuts_per_minute": 50,
                "shot_duration_range_sec": [0.5, 2.0],
            },
        }
        (d / "reference-priors.json").write_text(json.dumps(priors))

    def test_groups_by_edit_type(self, tmp_path):
        # 3 action tags, 1 dance, 1 mixed
        self._write_tag(tmp_path, "action-pl1", 6)
        self._write_tag(tmp_path, "action-pl2", 7)
        self._write_tag(tmp_path, "dance-pl1", 8)
        self._write_tag(tmp_path, "mixed-pl1", 5)

        summary = aggregate_priors_per_bucket(refs_root=tmp_path)
        # 3 buckets attempted (action, dance, mixed-skipped). Priors written: action + dance.
        written_types = {b["edit_type"] for b in summary["buckets_written"]}
        assert "action" in written_types
        assert "dance_movement" in written_types
        # mixed should be skipped (None edit_type)
        skipped_tags = [s.get("tag") for s in summary["buckets_skipped"] if "tag" in s]
        assert "mixed-pl1" in skipped_tags

    def test_skips_under_5_videos(self, tmp_path):
        self._write_tag(tmp_path, "tribute-pl1", 3)  # under threshold
        summary = aggregate_priors_per_bucket(refs_root=tmp_path)
        assert all(b["edit_type"] != "tribute" for b in summary["buckets_written"])
        # Should appear in skipped with the n<5 reason
        et_skipped = [s for s in summary["buckets_skipped"] if "edit_type" in s]
        assert any(s["edit_type"] == "tribute" for s in et_skipped)

    def test_writes_priors_files(self, tmp_path):
        self._write_tag(tmp_path, "sad-pl1", 6)
        summary = aggregate_priors_per_bucket(refs_root=tmp_path)
        bucket = next(b for b in summary["buckets_written"]
                      if b["edit_type"] == "sad_emotional")
        out_path = Path(bucket["path"])
        assert out_path.exists()
        data = json.loads(out_path.read_text())
        assert data["edit_type"] == "sad_emotional"
        assert data["fandom_family"] == "all"
        assert data["video_count"] == 6
        assert "priors" in data


class TestLoadPerBucketPriors:
    def test_returns_none_when_missing(self, tmp_path):
        result = load_per_bucket_priors("dance_movement", refs_root=tmp_path)
        assert result is None

    def test_loads_when_present(self, tmp_path):
        out = tmp_path / "priors" / "dance_movement" / "all.json"
        out.parent.mkdir(parents=True)
        out.write_text(json.dumps({
            "schema_version": 1, "edit_type": "dance_movement",
            "fandom_family": "all", "video_count": 30,
            "priors": {"median_shot_duration_sec": 0.6, "cuts_per_minute": 80},
        }))
        result = load_per_bucket_priors("dance_movement", refs_root=tmp_path)
        assert result is not None
        assert result["median_shot_duration_sec"] == 0.6
