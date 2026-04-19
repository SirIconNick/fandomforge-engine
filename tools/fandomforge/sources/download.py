"""Robust download layer for FandomForge.

Supports three modes:
    - both (default): best video + best audio, merged to mp4
    - video-only (no_audio=True): video stream only, mp4 container, no audio track
    - audio-only (audio_only=True): extract audio to chosen format

Routing:
    - Direct media URLs (ending in .mp4/.mkv/.webm/.mp3/.wav/.m4a/.flac/.ogg/.opus)
      go through urllib with Range resume + size validation
    - Everything else goes through yt-dlp

No domain gating. Any URL is fair game.

Resilience:
    - yt-dlp retries with linear backoff for transient errors and 429s
    - Subtitle fetch failures fall back to media-only grab
    - Format cascade: if requested resolution is unavailable, drop to next tier
    - Typed error classification turns yt-dlp stderr into actionable messages
    - Disk-space + writability pre-flight before every download
    - Resumable direct downloads via HTTP Range

Everything external-facing returns a DownloadResult with a typed error_kind
when it fails, so callers (CLI, API) can show the right user-facing message.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal


RouteKind = Literal["direct_media", "yt_dlp", "invalid"]

DIRECT_MEDIA_EXTS: tuple[str, ...] = (
    ".mp4", ".mkv", ".webm", ".mov", ".avi",
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac",
)

RESOLUTION_CASCADE: dict[str, list[str]] = {
    "1080": ["1080", "720", "480", "best"],
    "720":  ["720", "480", "best"],
    "480":  ["480", "best"],
    "best": ["best"],
}


class DownloadErrorKind(str, Enum):
    YT_DLP_MISSING = "yt_dlp_missing"
    INVALID_URL = "invalid_url"
    NETWORK = "network"
    RATE_LIMITED = "rate_limited"
    GEO_BLOCKED = "geo_blocked"
    AGE_RESTRICTED = "age_restricted"
    PRIVATE = "private"
    DELETED = "deleted"
    UNSUPPORTED = "unsupported_site"
    FORMAT_UNAVAILABLE = "format_unavailable"
    DISK_FULL = "disk_full"
    PERMISSION = "permission_denied"
    UNKNOWN = "unknown"


ERROR_HINTS: dict[DownloadErrorKind, str] = {
    DownloadErrorKind.YT_DLP_MISSING: "Install yt-dlp: pip install yt-dlp  (or: brew install yt-dlp)",
    DownloadErrorKind.INVALID_URL: "URL is not parseable. Check for typos and include scheme (https://).",
    DownloadErrorKind.NETWORK: "Network problem. Check connection and retry.",
    DownloadErrorKind.RATE_LIMITED: "Rate-limited by the platform (429). Wait a minute and retry — yt-dlp already retried.",
    DownloadErrorKind.GEO_BLOCKED: "Content is region-locked. Try again via VPN or use a different source.",
    DownloadErrorKind.AGE_RESTRICTED: "Age-restricted. Pass --cookies-from-browser chrome (or firefox/safari/edge) to authenticate.",
    DownloadErrorKind.PRIVATE: "Content is private or requires login. Pass --cookies-from-browser to authenticate.",
    DownloadErrorKind.DELETED: "Content no longer exists at that URL (404 / removed).",
    DownloadErrorKind.UNSUPPORTED: "yt-dlp doesn't recognize this site. Update yt-dlp: pip install -U yt-dlp",
    DownloadErrorKind.FORMAT_UNAVAILABLE: "Requested format/resolution isn't available — tried every fallback.",
    DownloadErrorKind.DISK_FULL: "Not enough disk space for the download. Free up space and retry.",
    DownloadErrorKind.PERMISSION: "Can't write to the output directory. Check permissions.",
    DownloadErrorKind.UNKNOWN: "Something went wrong — see stderr for details.",
}


@dataclass
class DownloadResult:
    """Result of a single download attempt.

    Attributes:
        success: True if a file was produced
        path: Path to the produced file (None on failure)
        stderr: Raw stderr from yt-dlp (or error message for direct downloads)
        subtitles_dropped: True if subs failed but media succeeded
        error_kind: Typed error classification (None on success)
        error_message: Human-readable actionable message (empty on success)
        format_fallback_used: True if we had to drop to a lower resolution
        final_resolution: The resolution that actually produced the file
        route: Which backend handled it (direct_media / yt_dlp)
    """

    success: bool
    path: Path | None = None
    stderr: str = ""
    subtitles_dropped: bool = False
    error_kind: DownloadErrorKind | None = None
    error_message: str = ""
    format_fallback_used: bool = False
    final_resolution: str | None = None
    route: RouteKind | None = None
    attempts: list[str] = field(default_factory=list)


# ---------- URL routing ----------

def classify_url_route(url: str) -> RouteKind:
    """Decide which backend handles this URL."""
    if not url or not isinstance(url, str):
        return "invalid"
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return "invalid"
    if parsed.scheme not in ("http", "https"):
        return "invalid"
    if not parsed.netloc:
        return "invalid"
    path_lower = (parsed.path or "").lower()
    if any(path_lower.endswith(ext) for ext in DIRECT_MEDIA_EXTS):
        return "direct_media"
    return "yt_dlp"


# ---------- yt-dlp error classification ----------

_ERROR_PATTERNS: list[tuple[re.Pattern[str], DownloadErrorKind]] = [
    (re.compile(r"HTTP Error 429", re.I),                             DownloadErrorKind.RATE_LIMITED),
    (re.compile(r"too many requests", re.I),                          DownloadErrorKind.RATE_LIMITED),
    (re.compile(r"sign in to confirm your age", re.I),                DownloadErrorKind.AGE_RESTRICTED),
    (re.compile(r"age[- ]restricted", re.I),                          DownloadErrorKind.AGE_RESTRICTED),
    (re.compile(r"confirm your age", re.I),                           DownloadErrorKind.AGE_RESTRICTED),
    (re.compile(r"private video", re.I),                              DownloadErrorKind.PRIVATE),
    (re.compile(r"members[- ]only", re.I),                            DownloadErrorKind.PRIVATE),
    (re.compile(r"login required", re.I),                             DownloadErrorKind.PRIVATE),
    (re.compile(r"this video is no longer available", re.I),          DownloadErrorKind.DELETED),
    (re.compile(r"video unavailable", re.I),                          DownloadErrorKind.DELETED),
    (re.compile(r"HTTP Error 404", re.I),                             DownloadErrorKind.DELETED),
    (re.compile(r"(not available|available).*in your country", re.I), DownloadErrorKind.GEO_BLOCKED),
    (re.compile(r"geo[- ]?restrict", re.I),                            DownloadErrorKind.GEO_BLOCKED),
    (re.compile(r"blocked.*in your country", re.I),                    DownloadErrorKind.GEO_BLOCKED),
    (re.compile(r"unsupported url", re.I),                            DownloadErrorKind.UNSUPPORTED),
    (re.compile(r"no video formats found", re.I),                     DownloadErrorKind.FORMAT_UNAVAILABLE),
    (re.compile(r"requested format is not available", re.I),          DownloadErrorKind.FORMAT_UNAVAILABLE),
    (re.compile(r"no such host|name or service not known|getaddrinfo", re.I), DownloadErrorKind.NETWORK),
    (re.compile(r"connection (timed out|reset|refused)", re.I),       DownloadErrorKind.NETWORK),
    (re.compile(r"network is unreachable", re.I),                     DownloadErrorKind.NETWORK),
    (re.compile(r"unable to connect to proxy", re.I),                 DownloadErrorKind.NETWORK),
    (re.compile(r"HTTP Error 5\d\d", re.I),                           DownloadErrorKind.NETWORK),
    (re.compile(r"tunnel connection failed", re.I),                   DownloadErrorKind.NETWORK),
    (re.compile(r"bad gateway", re.I),                                DownloadErrorKind.NETWORK),
    (re.compile(r"unable to download webpage", re.I),                 DownloadErrorKind.NETWORK),
    (re.compile(r"SSL(V3)?[: _].*certificate", re.I),                 DownloadErrorKind.NETWORK),
    (re.compile(r"temporary failure in name resolution", re.I),       DownloadErrorKind.NETWORK),
    (re.compile(r"no space left on device", re.I),                    DownloadErrorKind.DISK_FULL),
    (re.compile(r"permission denied", re.I),                          DownloadErrorKind.PERMISSION),
]


def classify_yt_dlp_error(stderr: str) -> DownloadErrorKind:
    if not stderr:
        return DownloadErrorKind.UNKNOWN
    for pat, kind in _ERROR_PATTERNS:
        if pat.search(stderr):
            return kind
    return DownloadErrorKind.UNKNOWN


_SUBTITLE_FAILURE_PATTERNS = (
    re.compile(r"Unable to download.*subtitle", re.IGNORECASE),
    re.compile(r"HTTP Error 429.*subtitle", re.IGNORECASE),
    re.compile(r"no subtitles? for the requested languages", re.IGNORECASE),
)


def _stderr_looks_like_subtitle_failure(stderr: str) -> bool:
    if not stderr:
        return False
    return any(pat.search(stderr) for pat in _SUBTITLE_FAILURE_PATTERNS)


# ---------- Pre-flight ----------

def _preflight(output_dir: Path, estimated_bytes: int | None = None) -> DownloadResult | None:
    """Check writability + (optionally) disk space. Returns error result or None if OK."""
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        return DownloadResult(
            success=False,
            error_kind=DownloadErrorKind.PERMISSION,
            error_message=f"Cannot create output dir {output_dir}: {exc}",
            stderr=str(exc),
        )
    except OSError as exc:
        if exc.errno == 28:  # ENOSPC
            return DownloadResult(
                success=False,
                error_kind=DownloadErrorKind.DISK_FULL,
                error_message=f"No disk space for {output_dir}",
                stderr=str(exc),
            )
        return DownloadResult(
            success=False,
            error_kind=DownloadErrorKind.UNKNOWN,
            error_message=f"Cannot prepare {output_dir}: {exc}",
            stderr=str(exc),
        )

    if not os.access(output_dir, os.W_OK):
        return DownloadResult(
            success=False,
            error_kind=DownloadErrorKind.PERMISSION,
            error_message=f"{output_dir} is not writable",
        )

    try:
        free = shutil.disk_usage(output_dir).free
    except OSError:
        return None

    required = estimated_bytes if estimated_bytes is not None else (100 * 1024 * 1024)
    if free < required:
        return DownloadResult(
            success=False,
            error_kind=DownloadErrorKind.DISK_FULL,
            error_message=(
                f"Only {free // (1024*1024)} MiB free in {output_dir}; "
                f"need at least {required // (1024*1024)} MiB."
            ),
        )
    return None


def _check_yt_dlp() -> DownloadResult | None:
    if shutil.which("yt-dlp") is None:
        return DownloadResult(
            success=False,
            error_kind=DownloadErrorKind.YT_DLP_MISSING,
            error_message=ERROR_HINTS[DownloadErrorKind.YT_DLP_MISSING],
        )
    return None


# ---------- Direct-file downloader ----------

def _download_direct(
    url: str,
    output_dir: Path,
    filename: str | None,
) -> DownloadResult:
    """Download a direct media URL with resume support."""
    parsed = urllib.parse.urlparse(url)
    ext = Path(parsed.path).suffix.lower()
    if not ext:
        ext = ".bin"
    out_name = f"{filename}{ext}" if filename else Path(parsed.path).name
    target = output_dir / out_name

    existing = target.stat().st_size if target.exists() else 0

    req = urllib.request.Request(url)
    req.add_header("User-Agent", "FandomForge/1.0")
    if existing > 0:
        req.add_header("Range", f"bytes={existing}-")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            total = resp.headers.get("Content-Length")
            expected = (int(total) + existing) if (total and resp.status == 206) else (int(total) if total else None)
            mode = "ab" if resp.status == 206 and existing > 0 else "wb"
            try:
                with open(target, mode) as f:
                    shutil.copyfileobj(resp, f, length=1024 * 1024)
            except OSError as exc:
                if exc.errno == 28:
                    return DownloadResult(
                        success=False,
                        error_kind=DownloadErrorKind.DISK_FULL,
                        error_message=ERROR_HINTS[DownloadErrorKind.DISK_FULL],
                        stderr=str(exc),
                        route="direct_media",
                    )
                raise
    except urllib.error.HTTPError as exc:
        status = getattr(exc, "code", 0)
        if status == 429:
            kind = DownloadErrorKind.RATE_LIMITED
        elif status in (401, 403):
            kind = DownloadErrorKind.PRIVATE
        elif status == 404:
            kind = DownloadErrorKind.DELETED
        else:
            kind = DownloadErrorKind.NETWORK
        return DownloadResult(
            success=False,
            error_kind=kind,
            error_message=f"{ERROR_HINTS[kind]} (HTTP {status})",
            stderr=str(exc),
            route="direct_media",
        )
    except urllib.error.URLError as exc:
        return DownloadResult(
            success=False,
            error_kind=DownloadErrorKind.NETWORK,
            error_message=f"{ERROR_HINTS[DownloadErrorKind.NETWORK]} ({exc.reason})",
            stderr=str(exc),
            route="direct_media",
        )
    except TimeoutError as exc:
        return DownloadResult(
            success=False,
            error_kind=DownloadErrorKind.NETWORK,
            error_message="Request timed out.",
            stderr=str(exc),
            route="direct_media",
        )

    if expected is not None and target.stat().st_size < expected:
        return DownloadResult(
            success=False,
            error_kind=DownloadErrorKind.NETWORK,
            error_message=(
                f"Incomplete download: got {target.stat().st_size} of {expected} bytes. "
                f"Re-run to resume."
            ),
            stderr="short read",
            path=target,
            route="direct_media",
        )

    return DownloadResult(
        success=True,
        path=target,
        route="direct_media",
        attempts=["direct"],
    )


# ---------- yt-dlp plumbing ----------

RETRY_ARGS: list[str] = [
    "--retries", "5",
    "--fragment-retries", "10",
    "--retry-sleep", "linear=1:10:2",
    "--sleep-requests", "1",
]
SUBTITLE_SLEEP_ARGS: list[str] = ["--sleep-subtitles", "3"]


def _build_format_selector(resolution: str, no_audio: bool) -> str:
    if no_audio:
        return "bestvideo" if resolution == "best" else f"bestvideo[height<={resolution}]"
    if resolution == "best":
        return "bestvideo+bestaudio/best"
    return f"bestvideo[height<={resolution}]+bestaudio/best[height<={resolution}]"


def _run_ytdlp(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=True)


def _parse_final_path(stdout: str) -> Path | None:
    final_path: Path | None = None
    for line in stdout.splitlines():
        if "[Merger] Merging formats into" in line:
            part = line.split("into", 1)[1].strip().strip('"')
            final_path = Path(part)
        elif "[ExtractAudio] Destination:" in line:
            part = line.split("Destination:", 1)[1].strip()
            final_path = Path(part)
        elif line.startswith("[download] Destination:"):
            part = line.split(":", 1)[1].strip()
            final_path = Path(part)
    return final_path


def _fallback_latest(output_dir: Path, audio_only: bool, audio_format: str) -> Path | None:
    exts = (f"*.{audio_format}",) if audio_only else ("*.mp4", "*.mkv", "*.webm")
    latest: Path | None = None
    for pat in exts:
        for p in output_dir.glob(pat):
            if latest is None or p.stat().st_mtime > latest.stat().st_mtime:
                latest = p
    return latest


SUPPORTED_BROWSERS: tuple[str, ...] = (
    "chrome", "chromium", "brave", "edge", "firefox",
    "safari", "opera", "vivaldi", "whale",
)


def _download_via_ytdlp(
    url: str,
    output_dir: Path,
    *,
    filename: str | None,
    resolution: str,
    write_subs: bool,
    auto_subs: bool,
    subtitle_langs: str,
    format_selector: str | None,
    audio_only: bool,
    no_audio: bool,
    audio_format: str,
    cookies_from_browser: str | None,
    cookies_file: Path | None,
) -> DownloadResult:
    output_template = str(
        output_dir / (f"{filename}.%(ext)s" if filename else "%(title)s.%(ext)s")
    )

    base_cmd: list[str] = [
        "yt-dlp",
        "-o", output_template,
        "--no-playlist",
        "--restrict-filenames",
        *RETRY_ARGS,
    ]
    if cookies_from_browser:
        base_cmd += ["--cookies-from-browser", cookies_from_browser]
    if cookies_file:
        base_cmd += ["--cookies", str(cookies_file)]

    cascade = RESOLUTION_CASCADE.get(resolution, [resolution, "best"]) if not audio_only else ["best"]

    if format_selector is not None:
        cascade = ["custom"]

    want_subs = bool((write_subs or auto_subs) and not audio_only)
    attempts: list[str] = []
    stderr_tail = ""

    for idx, res_try in enumerate(cascade):
        if audio_only:
            cmd = list(base_cmd) + ["-x", "--audio-format", audio_format]
            active_res = None
        else:
            if res_try == "custom" and format_selector is not None:
                fmt = format_selector
            else:
                fmt = _build_format_selector(res_try, no_audio)
            cmd = list(base_cmd) + ["-f", fmt, "--merge-output-format", "mp4"]
            active_res = res_try

        def with_subs_args(cmd: list[str], enable: bool) -> list[str]:
            cmd = list(cmd)
            if enable:
                if write_subs:
                    cmd.append("--write-subs")
                if auto_subs:
                    cmd.append("--write-auto-subs")
                cmd += [
                    "--sub-langs", subtitle_langs,
                    "--convert-subs", "srt",
                    *SUBTITLE_SLEEP_ARGS,
                ]
            cmd.append(url)
            return cmd

        subtitles_dropped = False
        try:
            result = _run_ytdlp(with_subs_args(cmd, want_subs))
            attempts.append(f"{active_res or 'audio'}+subs" if want_subs else (active_res or "audio"))
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr or ""
            stderr_tail = stderr[-1500:]
            if want_subs and _stderr_looks_like_subtitle_failure(stderr):
                try:
                    result = _run_ytdlp(with_subs_args(cmd, False))
                    subtitles_dropped = True
                    attempts.append(f"{active_res or 'audio'} no-subs")
                except subprocess.CalledProcessError as exc2:
                    stderr_tail = (exc2.stderr or str(exc2))[-1500:]
                    kind = classify_yt_dlp_error(stderr_tail)
                    if kind == DownloadErrorKind.FORMAT_UNAVAILABLE and idx + 1 < len(cascade):
                        continue
                    return DownloadResult(
                        success=False,
                        error_kind=kind,
                        error_message=ERROR_HINTS[kind],
                        stderr=stderr_tail,
                        attempts=attempts,
                        route="yt_dlp",
                    )
            else:
                kind = classify_yt_dlp_error(stderr)
                if kind == DownloadErrorKind.FORMAT_UNAVAILABLE and idx + 1 < len(cascade):
                    attempts.append(f"{active_res or 'audio'} → format-unavailable, cascading")
                    continue
                return DownloadResult(
                    success=False,
                    error_kind=kind,
                    error_message=ERROR_HINTS[kind],
                    stderr=stderr_tail,
                    attempts=attempts,
                    route="yt_dlp",
                )

        final_path = _parse_final_path(result.stdout)
        if final_path is None:
            final_path = _fallback_latest(output_dir, audio_only, audio_format)

        return DownloadResult(
            success=True,
            path=final_path,
            stderr=result.stderr,
            subtitles_dropped=subtitles_dropped,
            format_fallback_used=(idx > 0),
            final_resolution=None if audio_only else active_res,
            attempts=attempts,
            route="yt_dlp",
        )

    kind = DownloadErrorKind.FORMAT_UNAVAILABLE
    return DownloadResult(
        success=False,
        error_kind=kind,
        error_message=ERROR_HINTS[kind],
        stderr=stderr_tail,
        attempts=attempts,
        route="yt_dlp",
    )


# ---------- Public entry point ----------

def download_source(
    url: str,
    output_dir: Path | str,
    *,
    filename: str | None = None,
    resolution: str = "1080",
    write_subs: bool = True,
    auto_subs: bool = True,
    subtitle_langs: str = "en,en-US",
    format_selector: str | None = None,
    audio_only: bool = False,
    no_audio: bool = False,
    audio_format: str = "mp3",
    cookies_from_browser: str | None = None,
    cookies_file: Path | str | None = None,
) -> DownloadResult:
    """Download a single source via the appropriate backend.

    Direct media URLs (ending in common media extensions) are fetched with
    urllib + Range resume. Everything else goes through yt-dlp with a format
    cascade, retry/backoff, and subtitle-failure fallback.

    Every failure returns a DownloadResult with a DownloadErrorKind and an
    actionable error_message. Never raises for expected failure modes.
    """
    if audio_only and no_audio:
        raise ValueError("audio_only and no_audio are mutually exclusive")

    if cookies_from_browser and cookies_from_browser not in SUPPORTED_BROWSERS:
        return DownloadResult(
            success=False,
            error_kind=DownloadErrorKind.INVALID_URL,
            error_message=(
                f"Unsupported browser '{cookies_from_browser}'. "
                f"Choose one of: {', '.join(SUPPORTED_BROWSERS)}."
            ),
        )

    cookies_path: Path | None = None
    if cookies_file is not None:
        cookies_path = Path(cookies_file).expanduser()
        if not cookies_path.exists():
            return DownloadResult(
                success=False,
                error_kind=DownloadErrorKind.INVALID_URL,
                error_message=f"cookies file not found: {cookies_path}",
            )

    route = classify_url_route(url)
    if route == "invalid":
        return DownloadResult(
            success=False,
            error_kind=DownloadErrorKind.INVALID_URL,
            error_message=ERROR_HINTS[DownloadErrorKind.INVALID_URL],
            route=None,
        )

    output_dir = Path(output_dir)
    pre = _preflight(output_dir)
    if pre is not None:
        pre.route = route
        return pre

    if route == "direct_media" and not audio_only and not no_audio:
        result = _download_direct(url, output_dir, filename)
        if result.success or result.error_kind is not DownloadErrorKind.NETWORK:
            return result

    yt_dlp_check = _check_yt_dlp()
    if yt_dlp_check is not None:
        yt_dlp_check.route = "yt_dlp"
        return yt_dlp_check

    return _download_via_ytdlp(
        url,
        output_dir,
        filename=filename,
        resolution=resolution,
        write_subs=write_subs,
        auto_subs=auto_subs,
        subtitle_langs=subtitle_langs,
        format_selector=format_selector,
        audio_only=audio_only,
        no_audio=no_audio,
        audio_format=audio_format,
        cookies_from_browser=cookies_from_browser,
        cookies_file=cookies_path,
    )


def export_cookies_from_browser(browser: str, output: Path) -> DownloadResult:
    """Export a reusable Netscape-format cookies.txt from the given browser.

    Uses yt-dlp's internal mechanism (same one --cookies-from-browser reads).
    The resulting file can be copied to another machine and used with
    --cookies <file> — useful for offline/headless environments.
    """
    if browser not in SUPPORTED_BROWSERS:
        return DownloadResult(
            success=False,
            error_kind=DownloadErrorKind.INVALID_URL,
            error_message=(
                f"Unsupported browser '{browser}'. "
                f"Choose one of: {', '.join(SUPPORTED_BROWSERS)}."
            ),
        )

    check = _check_yt_dlp()
    if check is not None:
        return check

    output = Path(output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "yt-dlp",
        "--cookies-from-browser", browser,
        "--cookies", str(output),
        "--skip-download",
        "--no-playlist",
        "https://www.youtube.com",
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        return DownloadResult(
            success=False,
            error_kind=DownloadErrorKind.UNKNOWN,
            error_message=(exc.stderr or str(exc))[-800:],
            stderr=(exc.stderr or str(exc))[-1500:],
        )

    if not output.exists() or output.stat().st_size == 0:
        return DownloadResult(
            success=False,
            error_kind=DownloadErrorKind.UNKNOWN,
            error_message=(
                f"yt-dlp ran but produced no cookies at {output}. "
                f"Browser may not be installed or has no cookies."
            ),
        )
    return DownloadResult(success=True, path=output)


__all__ = [
    "DIRECT_MEDIA_EXTS",
    "DownloadErrorKind",
    "DownloadResult",
    "ERROR_HINTS",
    "RESOLUTION_CASCADE",
    "SUPPORTED_BROWSERS",
    "classify_url_route",
    "classify_yt_dlp_error",
    "download_source",
    "export_cookies_from_browser",
]
