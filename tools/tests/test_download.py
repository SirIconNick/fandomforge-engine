"""Tests for the download layer — routing, error classification, pre-flight."""

from __future__ import annotations

import pytest

from fandomforge.sources.download import (
    DIRECT_MEDIA_EXTS,
    DownloadErrorKind,
    DownloadResult,
    RESOLUTION_CASCADE,
    SUPPORTED_BROWSERS,
    classify_url_route,
    classify_yt_dlp_error,
    download_source,
)


class TestClassifyUrlRoute:
    def test_direct_mp4_routes_to_direct(self) -> None:
        assert classify_url_route("https://example.com/clip.mp4") == "direct_media"

    def test_direct_mp3_routes_to_direct(self) -> None:
        assert classify_url_route("https://a.b/song.mp3") == "direct_media"

    def test_direct_mkv_routes_to_direct(self) -> None:
        assert classify_url_route("https://a.b/movie.mkv") == "direct_media"

    def test_all_direct_extensions_match(self) -> None:
        for ext in DIRECT_MEDIA_EXTS:
            assert classify_url_route(f"https://a.b/file{ext}") == "direct_media"

    def test_youtube_routes_to_yt_dlp(self) -> None:
        assert classify_url_route("https://www.youtube.com/watch?v=abc") == "yt_dlp"

    def test_vimeo_routes_to_yt_dlp(self) -> None:
        assert classify_url_route("https://vimeo.com/1234") == "yt_dlp"

    def test_empty_url_is_invalid(self) -> None:
        assert classify_url_route("") == "invalid"

    def test_non_http_scheme_is_invalid(self) -> None:
        assert classify_url_route("ftp://a.b/clip.mp4") == "invalid"
        assert classify_url_route("file:///local/clip.mp4") == "invalid"

    def test_junk_string_is_invalid(self) -> None:
        assert classify_url_route("not a url at all") == "invalid"

    def test_missing_host_is_invalid(self) -> None:
        assert classify_url_route("https:///path") == "invalid"

    def test_upper_case_extension_still_matches(self) -> None:
        assert classify_url_route("https://a.b/CLIP.MP4") == "direct_media"

    def test_query_string_does_not_confuse_extension(self) -> None:
        assert classify_url_route("https://a.b/song.mp3?token=xyz") == "direct_media"


class TestClassifyYtDlpError:
    def test_429_is_rate_limited(self) -> None:
        assert classify_yt_dlp_error("ERROR: HTTP Error 429: Too Many Requests") == DownloadErrorKind.RATE_LIMITED

    def test_too_many_requests_text_is_rate_limited(self) -> None:
        assert classify_yt_dlp_error("too many requests, please wait") == DownloadErrorKind.RATE_LIMITED

    def test_age_restricted_is_detected(self) -> None:
        assert classify_yt_dlp_error("Sign in to confirm your age") == DownloadErrorKind.AGE_RESTRICTED
        assert classify_yt_dlp_error("this video is age-restricted") == DownloadErrorKind.AGE_RESTRICTED

    def test_private_video_detected(self) -> None:
        assert classify_yt_dlp_error("ERROR: Private video. Sign in if you've been granted access") == DownloadErrorKind.PRIVATE

    def test_deleted_video_detected(self) -> None:
        assert classify_yt_dlp_error("This video is no longer available") == DownloadErrorKind.DELETED
        assert classify_yt_dlp_error("Video unavailable") == DownloadErrorKind.DELETED
        assert classify_yt_dlp_error("HTTP Error 404: Not Found") == DownloadErrorKind.DELETED

    def test_geo_block_detected(self) -> None:
        assert classify_yt_dlp_error("The uploader has not made this video available in your country") == DownloadErrorKind.GEO_BLOCKED
        assert classify_yt_dlp_error("geo-restricted") == DownloadErrorKind.GEO_BLOCKED

    def test_unsupported_site(self) -> None:
        assert classify_yt_dlp_error("ERROR: Unsupported URL: https://example.org") == DownloadErrorKind.UNSUPPORTED

    def test_format_unavailable(self) -> None:
        assert classify_yt_dlp_error("ERROR: Requested format is not available") == DownloadErrorKind.FORMAT_UNAVAILABLE
        assert classify_yt_dlp_error("No video formats found") == DownloadErrorKind.FORMAT_UNAVAILABLE

    def test_network_errors(self) -> None:
        assert classify_yt_dlp_error("No such host is known") == DownloadErrorKind.NETWORK
        assert classify_yt_dlp_error("Connection timed out") == DownloadErrorKind.NETWORK
        assert classify_yt_dlp_error("Connection reset by peer") == DownloadErrorKind.NETWORK
        assert classify_yt_dlp_error("Network is unreachable") == DownloadErrorKind.NETWORK
        assert classify_yt_dlp_error("Unable to connect to proxy") == DownloadErrorKind.NETWORK
        assert classify_yt_dlp_error("Tunnel connection failed: 502 Bad Gateway") == DownloadErrorKind.NETWORK
        assert classify_yt_dlp_error("HTTP Error 503: Service Unavailable") == DownloadErrorKind.NETWORK
        assert classify_yt_dlp_error("Unable to download webpage") == DownloadErrorKind.NETWORK
        assert classify_yt_dlp_error("Temporary failure in name resolution") == DownloadErrorKind.NETWORK

    def test_disk_full(self) -> None:
        assert classify_yt_dlp_error("OSError: [Errno 28] No space left on device") == DownloadErrorKind.DISK_FULL

    def test_permission_denied(self) -> None:
        assert classify_yt_dlp_error("PermissionError: Permission denied: '/root/x'") == DownloadErrorKind.PERMISSION

    def test_unknown_falls_through(self) -> None:
        assert classify_yt_dlp_error("something weird happened") == DownloadErrorKind.UNKNOWN

    def test_empty_stderr_is_unknown(self) -> None:
        assert classify_yt_dlp_error("") == DownloadErrorKind.UNKNOWN


class TestResolutionCascade:
    def test_1080_cascade(self) -> None:
        assert RESOLUTION_CASCADE["1080"] == ["1080", "720", "480", "best"]

    def test_720_cascade(self) -> None:
        assert RESOLUTION_CASCADE["720"] == ["720", "480", "best"]

    def test_480_cascade(self) -> None:
        assert RESOLUTION_CASCADE["480"] == ["480", "best"]

    def test_best_cascade(self) -> None:
        assert RESOLUTION_CASCADE["best"] == ["best"]


class TestDownloadSourceValidation:
    def test_audio_only_and_no_audio_is_rejected(self, tmp_path) -> None:
        with pytest.raises(ValueError):
            download_source("https://a.b/clip.mp4", tmp_path, audio_only=True, no_audio=True)

    def test_invalid_url_returns_typed_error(self, tmp_path) -> None:
        result = download_source("not a url", tmp_path)
        assert result.success is False
        assert result.error_kind == DownloadErrorKind.INVALID_URL
        assert result.error_message

    def test_empty_url_returns_typed_error(self, tmp_path) -> None:
        result = download_source("", tmp_path)
        assert result.success is False
        assert result.error_kind == DownloadErrorKind.INVALID_URL

    def test_ftp_url_is_invalid(self, tmp_path) -> None:
        result = download_source("ftp://a.b/clip.mp4", tmp_path)
        assert result.error_kind == DownloadErrorKind.INVALID_URL

    def test_unsupported_browser_rejected(self, tmp_path) -> None:
        result = download_source(
            "https://www.youtube.com/watch?v=abc",
            tmp_path,
            cookies_from_browser="netscape",
        )
        assert result.success is False
        assert "netscape" in (result.error_message or "").lower()

    def test_all_supported_browsers_accepted(self) -> None:
        assert "chrome" in SUPPORTED_BROWSERS
        assert "firefox" in SUPPORTED_BROWSERS
        assert "safari" in SUPPORTED_BROWSERS
        assert "edge" in SUPPORTED_BROWSERS
        assert "brave" in SUPPORTED_BROWSERS

    def test_missing_cookies_file_returns_error(self, tmp_path) -> None:
        result = download_source(
            "https://www.youtube.com/watch?v=abc",
            tmp_path,
            cookies_file=tmp_path / "does-not-exist.txt",
        )
        assert result.success is False
        assert "cookies file not found" in (result.error_message or "").lower()


class TestPreflight:
    def test_invalid_url_short_circuits_before_dir_create(self, tmp_path) -> None:
        """Don't create directories for invalid URLs — cheap validation first."""
        nested = tmp_path / "a" / "b" / "c"
        assert not nested.exists()
        result = download_source("not a url", nested)
        assert result.error_kind == DownloadErrorKind.INVALID_URL
        assert not nested.exists()


class TestDownloadResultDefaults:
    def test_defaults_are_sensible(self) -> None:
        r = DownloadResult(success=False)
        assert r.path is None
        assert r.stderr == ""
        assert r.subtitles_dropped is False
        assert r.error_kind is None
        assert r.error_message == ""
        assert r.format_fallback_used is False
        assert r.final_resolution is None
        assert r.route is None
        assert r.attempts == []
        assert r.has_video_stream is None
        assert r.has_audio_stream is None
        assert r.audio_mean_dbfs is None


class TestStreamValidation:
    """The stream validator fills DownloadResult fields and classifies errors."""

    def test_new_error_kinds_exist(self) -> None:
        assert DownloadErrorKind.MISSING_VIDEO_STREAM.value == "missing_video_stream"
        assert DownloadErrorKind.MISSING_AUDIO_STREAM.value == "missing_audio_stream"
        assert DownloadErrorKind.SILENT_AUDIO.value == "silent_audio"

    def test_validator_graceful_when_ffprobe_absent(self, tmp_path, monkeypatch) -> None:
        """If ffprobe isn't installed, validator returns (True, True, None) — skip, not fail."""
        from fandomforge.sources import download as dl

        monkeypatch.setattr(dl.shutil, "which", lambda _: None)
        p = tmp_path / "fake.mp4"
        p.write_bytes(b"\x00")
        has_v, has_a, dbfs = dl._validate_streams(p, expect_video=True, expect_audio=True)
        assert has_v is True and has_a is True and dbfs is None

    def test_silent_threshold_is_reasonable(self) -> None:
        from fandomforge.sources.download import SILENT_THRESHOLD_DBFS
        # Typical YouTube audio is ~-20 dB, ambient noise floor is ~-60 dB,
        # truly silent tracks report -91 dB. Threshold of -70 sits between.
        assert -85.0 < SILENT_THRESHOLD_DBFS < -50.0
