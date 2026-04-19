"""Unit tests for the song-source quality ranker.

All tests are pure — no yt-dlp calls. We feed synthetic metadata dicts
directly into `rank_metadata` and assert kind + score.
"""

from __future__ import annotations

import pytest

from fandomforge.sources.song_search import (
    SongKind,
    rank_metadata,
    search_song,
    rank_song_source,
)


def _meta(title: str, uploader: str = "Artist", duration: int = 200, id_: str = "abc123") -> dict:
    return {
        "id": id_,
        "title": title,
        "uploader": uploader,
        "duration": duration,
        "webpage_url": f"https://www.youtube.com/watch?v={id_}",
        "view_count": 1000,
    }


class TestKindClassification:
    def test_official_audio(self) -> None:
        q = rank_metadata(_meta("Fall Out Boy — Centuries (Official Audio)"))
        assert q.kind == SongKind.OFFICIAL_AUDIO
        assert q.score >= 95

    def test_official_audio_full_song_variant(self) -> None:
        q = rank_metadata(_meta("Song Name — Full Song"))
        assert q.kind == SongKind.OFFICIAL_AUDIO
        assert q.score >= 95

    def test_visualizer(self) -> None:
        q = rank_metadata(_meta("NF - HOPE (Visualizer)"))
        assert q.kind == SongKind.VISUALIZER
        assert 80 <= q.score <= 100

    def test_lyric_video(self) -> None:
        q = rank_metadata(_meta("Imagine Dragons — Believer (Lyric Video)"))
        assert q.kind == SongKind.LYRIC_VIDEO
        assert 70 <= q.score <= 90

    def test_lyric_video_lyrics_variant(self) -> None:
        q = rank_metadata(_meta("Some Song (Lyrics)"))
        assert q.kind == SongKind.LYRIC_VIDEO

    def test_music_video_official_music_video(self) -> None:
        q = rank_metadata(_meta("Fall Out Boy — Centuries (Official Music Video)"))
        assert q.kind == SongKind.MUSIC_VIDEO
        assert q.score < 50, "music video should score below 50 so warning fires"

    def test_music_video_official_video_variant(self) -> None:
        q = rank_metadata(_meta("Band — Track (Official Video)"))
        assert q.kind == SongKind.MUSIC_VIDEO

    def test_live_demoted(self) -> None:
        q = rank_metadata(_meta("Track (Live at Wembley)"))
        assert q.kind == SongKind.LIVE
        assert q.score < 30

    def test_acoustic_mapped_to_live(self) -> None:
        q = rank_metadata(_meta("Track (Acoustic Session)"))
        assert q.kind == SongKind.LIVE

    def test_remix(self) -> None:
        q = rank_metadata(_meta("Track — Shep Remix"))
        assert q.kind == SongKind.REMIX

    def test_cover(self) -> None:
        q = rank_metadata(_meta("Track (Cover by Someone)"))
        assert q.kind == SongKind.COVER

    def test_unknown_falls_through(self) -> None:
        q = rank_metadata(_meta("Just A Plain Title"))
        assert q.kind == SongKind.UNKNOWN


class TestUploaderBonus:
    def test_vevo_bumps_score(self) -> None:
        # Use a plain title so we're below the 100-score ceiling — then we can
        # see the VEVO delta.
        without = rank_metadata(_meta("Just A Plain Title", uploader="SomeChannel"))
        with_vevo = rank_metadata(_meta("Just A Plain Title", uploader="ArtistVEVO"))
        assert with_vevo.score > without.score
        assert "VEVO" in with_vevo.reason

    def test_official_channel_bumps(self) -> None:
        q = rank_metadata(_meta("Just A Plain Title", uploader="Artist Official"))
        assert q.score >= _KIND_BASE(SongKind.UNKNOWN) + 5


def _KIND_BASE(kind: SongKind) -> int:
    from fandomforge.sources.song_search import _KIND_SCORES
    return _KIND_SCORES[kind]


class TestDemotions:
    def test_radio_edit_demoted(self) -> None:
        clean = rank_metadata(_meta("Song (Official Audio)"))
        dirty = rank_metadata(_meta("Song (Official Audio) (Clean Version)"))
        assert dirty.score < clean.score
        assert "clean/radio" in dirty.reason

    def test_reaction_video_demoted_hard(self) -> None:
        q = rank_metadata(_meta("Track — Reaction"))
        assert q.score < 20

    def test_bass_boosted_demoted(self) -> None:
        q = rank_metadata(_meta("Track (Bass Boosted)"))
        assert q.score < 50

    def test_sped_up_demoted(self) -> None:
        q = rank_metadata(_meta("Track (Sped Up)"))
        assert q.score < 50

    def test_snippet_demoted(self) -> None:
        q = rank_metadata(_meta("Track snippet"))
        assert q.score < 50


class TestScoreBands:
    """Ensure the score bands we claim in the plan actually hold."""

    def test_official_audio_above_threshold(self) -> None:
        assert rank_metadata(_meta("Centuries (Official Audio)")).score >= 90

    def test_music_video_below_warning_line(self) -> None:
        """We claimed <50 triggers a warning. Make sure MVs land below it."""
        assert rank_metadata(_meta("Centuries (Official Music Video)")).score < 50

    def test_visualizer_above_warning_line(self) -> None:
        assert rank_metadata(_meta("Track (Visualizer)")).score >= 50

    def test_lyric_video_above_warning_line(self) -> None:
        assert rank_metadata(_meta("Track (Lyric Video)")).score >= 50


class TestRankSongSourceFallback:
    """When yt-dlp is absent or fails, return a score-0 UNKNOWN — never crash."""

    def test_missing_yt_dlp_returns_unknown(self, monkeypatch) -> None:
        from fandomforge.sources import song_search

        monkeypatch.setattr(song_search.shutil, "which", lambda _: None)
        q = rank_song_source("https://www.youtube.com/watch?v=abc")
        assert q.kind == SongKind.UNKNOWN
        assert q.score == 0
        assert q.url == "https://www.youtube.com/watch?v=abc"

    def test_search_missing_yt_dlp_returns_empty(self, monkeypatch) -> None:
        from fandomforge.sources import song_search

        monkeypatch.setattr(song_search.shutil, "which", lambda _: None)
        assert search_song("whatever") == []


class TestSearchRanking:
    """If we feed synthetic search results, best scores sort first."""

    def test_results_sorted_best_first(self, monkeypatch) -> None:
        from fandomforge.sources import song_search

        fake_results = [
            _meta("Song — Official Music Video", id_="mv"),
            _meta("Song — Official Audio", id_="oa"),
            _meta("Song (Lyric Video)", id_="lv"),
            _meta("Song — Cover", id_="cv"),
        ]
        monkeypatch.setattr(
            song_search, "_search_metadata",
            lambda q, n, timeout=60: fake_results,
        )
        out = search_song("Song")
        # best-first: official audio, then lyric, then music video, then cover
        ids = [c.id for c in out]
        assert ids[0] == "oa"
        assert ids[1] == "lv"
        assert ids[2] == "mv"
        assert ids[3] == "cv"

    def test_duplicate_ids_deduped(self, monkeypatch) -> None:
        from fandomforge.sources import song_search

        fake_results = [
            _meta("Track (Official Audio)", id_="x"),
            _meta("Track (Official Audio) mirror", id_="x"),  # same id
        ]
        monkeypatch.setattr(
            song_search, "_search_metadata",
            lambda q, n, timeout=60: fake_results,
        )
        out = search_song("Track")
        assert len(out) == 1

    def test_min_score_filters(self, monkeypatch) -> None:
        from fandomforge.sources import song_search

        fake_results = [
            _meta("Track (Official Music Video)", id_="mv"),  # score ~40
            _meta("Track (Official Audio)", id_="oa"),        # score ~100
        ]
        monkeypatch.setattr(
            song_search, "_search_metadata",
            lambda q, n, timeout=60: fake_results,
        )
        out = search_song("Track", min_score=70)
        assert len(out) == 1
        assert out[0].id == "oa"
