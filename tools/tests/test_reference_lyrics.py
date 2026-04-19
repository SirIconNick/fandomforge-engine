"""Lyric-alignment scoring tests — the whisper runner is mocked."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fandomforge.intelligence import reference_lyrics


FAKE_TRANSCRIPT = {
    "segments": [
        {
            "start": 5.0, "end": 10.0, "text": "hello world fine day",
            "words": [
                {"word": "hello", "start": 5.0, "end": 5.4},
                {"word": "world", "start": 5.5, "end": 6.0},
                {"word": "fine", "start": 6.1, "end": 6.5},
                {"word": "day", "start": 6.6, "end": 7.0},
                # Gap of 2s → phrase boundary
                {"word": "next", "start": 9.0, "end": 9.4},
                {"word": "line", "start": 9.5, "end": 9.9},
            ],
        }
    ]
}


class TestScoreLyricAlignment:
    def test_returns_available_false_when_whisper_missing(self, tmp_path: Path) -> None:
        video = tmp_path / "v.mp4"
        video.write_bytes(b"x")
        with patch.object(reference_lyrics, "_load_or_transcribe", return_value=None):
            result = reference_lyrics.score_lyric_alignment(video, cut_times=[1.0])
        assert result["available"] is False

    def test_word_and_phrase_alignment(self, tmp_path: Path) -> None:
        video = tmp_path / "v.mp4"
        video.write_bytes(b"x")
        # cut at 5.03 → within 0.1s of word "hello" (start 5.0)
        # cut at 9.02 → within 0.25s of phrase boundary (start 9.0 after gap)
        # cut at 15.0 → no nearby word or phrase
        with patch.object(reference_lyrics, "_load_or_transcribe", return_value=FAKE_TRANSCRIPT):
            result = reference_lyrics.score_lyric_alignment(
                video, cut_times=[5.03, 9.02, 15.0],
            )
        assert result["available"] is True
        assert result["cuts_checked"] == 3
        # At least one cut landed on a word boundary
        assert result["cuts_on_word_boundary_pct"] > 0
        # At least one landed on a phrase boundary
        assert result["cuts_on_phrase_boundary_pct"] > 0

    def test_no_cuts_returns_available_false(self, tmp_path: Path) -> None:
        video = tmp_path / "v.mp4"
        video.write_bytes(b"x")
        result = reference_lyrics.score_lyric_alignment(video, cut_times=[])
        assert result["available"] is False


class TestCachePath:
    def test_cache_path_includes_content_hash(self, tmp_path: Path) -> None:
        video = tmp_path / "alpha.mp4"
        video.write_bytes(b"aaaaa" * 200)
        p1 = reference_lyrics._cache_path(video)

        video2 = tmp_path / "alpha.mp4"
        video2.write_bytes(b"bbbbb" * 200)
        p2 = reference_lyrics._cache_path(video2)

        # Different content → different hash → different cache path
        assert p1 != p2
        assert ".transcripts" in str(p1)
