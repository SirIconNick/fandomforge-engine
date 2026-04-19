"""Wrapper around yt-dlp for downloading source videos."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


# Domains we refuse to download from. Per project CLAUDE.md: never rip from
# streaming services. If the user wants content from these, they must acquire
# it via owned media (Blu-ray rip, official download) and drop the file into
# the project's raw/ directory manually.
DISALLOWED_DOMAINS = {
    "netflix.com", "www.netflix.com",
    "hulu.com", "www.hulu.com",
    "disneyplus.com", "www.disneyplus.com", "disney.com",
    "hbomax.com", "max.com", "www.max.com",
    "primevideo.com", "amazon.com/video", "amazon.com/gp/video",
    "appletv.com", "tv.apple.com",
    "paramountplus.com", "www.paramountplus.com",
    "peacocktv.com", "www.peacocktv.com",
    "crunchyroll.com", "beta.crunchyroll.com",
}


class DisallowedDomainError(RuntimeError):
    """Raised when the user tries to scrape from a streaming service."""


@dataclass
class DownloadResult:
    """Result of a single download attempt."""

    success: bool
    path: Path | None
    stderr: str = ""


def _check_yt_dlp() -> None:
    if shutil.which("yt-dlp") is None:
        raise RuntimeError(
            "yt-dlp not found. Install with: pip install yt-dlp  "
            "or: brew install yt-dlp"
        )


def _assert_allowed(url: str) -> None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return
    host = (parsed.netloc or "").lower()
    if not host:
        return
    if host in DISALLOWED_DOMAINS or any(host.endswith("." + d) for d in DISALLOWED_DOMAINS):
        raise DisallowedDomainError(
            f"Refusing to download from '{host}'. Streaming services are not a "
            f"legitimate source for FandomForge edits. Acquire the content via "
            f"owned media (physical disc, official purchase) and drop the file "
            f"into projects/<slug>/raw/ directly."
        )


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
) -> DownloadResult:
    """Download a single source video via yt-dlp.

    Args:
        url: Video URL (YouTube, Vimeo, etc. — anything yt-dlp supports)
        output_dir: Directory to save the downloaded video
        filename: Optional explicit filename (without extension). Defaults to video title.
        resolution: Max vertical resolution. "1080" = up to 1080p, "720" = up to 720p, "best" = no cap.
        write_subs: Also download manually-authored subtitles when available
        auto_subs: Also download auto-generated captions
        subtitle_langs: Subtitle languages (comma-separated, yt-dlp format)
        format_selector: Override the default format selector

    Returns:
        DownloadResult with path to the downloaded file on success.

    Raises:
        DisallowedDomainError: if the URL is from a streaming service we
            refuse to scrape (Netflix, Disney+, HBO Max, etc). The user must
            acquire content from owned media and drop it into raw/ manually.
    """
    _assert_allowed(url)
    _check_yt_dlp()

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Default format selector: best video + best audio at or below requested resolution
    if format_selector is None:
        if resolution == "best":
            format_selector = "bestvideo+bestaudio/best"
        else:
            format_selector = (
                f"bestvideo[height<={resolution}]+bestaudio/"
                f"best[height<={resolution}]"
            )

    output_template = str(
        output_dir / (f"{filename}.%(ext)s" if filename else "%(title)s.%(ext)s")
    )

    cmd = [
        "yt-dlp",
        "-f", format_selector,
        "-o", output_template,
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--restrict-filenames",
    ]

    if write_subs:
        cmd += ["--write-subs"]
    if auto_subs:
        cmd += ["--write-auto-subs"]
    if write_subs or auto_subs:
        cmd += ["--sub-langs", subtitle_langs, "--convert-subs", "srt"]

    cmd.append(url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        return DownloadResult(success=False, path=None, stderr=exc.stderr or str(exc))

    # Parse yt-dlp's "[download] Destination:" or "[Merger] Merging formats into"
    # lines to find the output path. Fall back to scanning the output dir.
    final_path: Path | None = None
    for line in result.stdout.splitlines():
        if "[Merger] Merging formats into" in line:
            # Example: [Merger] Merging formats into "out/filename.mp4"
            part = line.split("into", 1)[1].strip().strip('"')
            final_path = Path(part)
        elif line.startswith("[download] Destination:"):
            part = line.split(":", 1)[1].strip()
            final_path = Path(part)

    if final_path is None:
        # Fallback: latest mp4 in the output dir
        mp4s = sorted(
            output_dir.glob("*.mp4"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if mp4s:
            final_path = mp4s[0]

    return DownloadResult(success=True, path=final_path, stderr=result.stderr)
