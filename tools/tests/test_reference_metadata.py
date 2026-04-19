"""YouTube metadata fetch tests — yt-dlp is mocked."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from fandomforge.intelligence import reference_library


def _fake_proc(payload: dict) -> MagicMock:
    proc = MagicMock()
    proc.stdout = json.dumps(payload)
    proc.returncode = 0
    return proc


class TestFetchYoutubeMetadata:
    def test_returns_none_when_ytdlp_missing(self) -> None:
        with patch.object(reference_library, "_yt_dlp_available", return_value=False):
            assert reference_library.fetch_youtube_metadata("x") is None

    def test_extracts_core_fields(self) -> None:
        payload = {
            "view_count": 1_000_000,
            "like_count": 30_000,
            "duration": 180.0,
            "channel": "VCreations",
            "upload_date": "20240301",
            "title": "Some Edit",
        }
        with patch.object(reference_library, "_yt_dlp_available", return_value=True), \
             patch("subprocess.run", return_value=_fake_proc(payload)):
            m = reference_library.fetch_youtube_metadata("x")
        assert m["view_count"] == 1_000_000
        assert m["like_count"] == 30_000
        assert m["like_ratio"] == round(30_000 / 1_000_000, 6)
        assert m["duration_sec"] == 180.0
        assert m["channel"] == "VCreations"
        assert m["title"] == "Some Edit"

    def test_missing_like_count_leaves_ratio_none(self) -> None:
        payload = {"view_count": 5_000_000, "duration": 200}
        with patch.object(reference_library, "_yt_dlp_available", return_value=True), \
             patch("subprocess.run", return_value=_fake_proc(payload)):
            m = reference_library.fetch_youtube_metadata("x")
        assert m["view_count"] == 5_000_000
        assert m["like_count"] is None
        assert m["like_ratio"] is None

    def test_invalid_json_returns_none(self) -> None:
        proc = MagicMock(stdout="not json", returncode=0)
        with patch.object(reference_library, "_yt_dlp_available", return_value=True), \
             patch("subprocess.run", return_value=proc):
            assert reference_library.fetch_youtube_metadata("x") is None
