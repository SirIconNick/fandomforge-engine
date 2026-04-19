"""Fetch transcripts (subtitles / captions) from video URLs via yt-dlp."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TranscriptResult:
    success: bool
    srt_path: Path | None
    plain_text: str = ""
    stderr: str = ""


def _check_yt_dlp() -> None:
    if shutil.which("yt-dlp") is None:
        raise RuntimeError(
            "yt-dlp not found. Install with: pip install yt-dlp  "
            "or: brew install yt-dlp"
        )


def _srt_to_plain(srt_path: Path) -> str:
    """Strip SRT timestamps and numbering, leaving clean dialogue text."""
    text = srt_path.read_text(encoding="utf-8")
    # Remove index lines (solo numbers)
    text = re.sub(r"^\d+\s*$", "", text, flags=re.MULTILINE)
    # Remove timestamp lines
    text = re.sub(
        r"\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}.*$",
        "",
        text,
        flags=re.MULTILINE,
    )
    # Remove HTML tags like <c>, <i>, positioning cues
    text = re.sub(r"<[^>]+>", "", text)
    # Collapse whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_transcript(
    url: str,
    output_dir: Path | str,
    *,
    filename_base: str | None = None,
    langs: str = "en,en-US",
    prefer_manual: bool = True,
) -> TranscriptResult:
    """Download just the transcript / subtitles for a video, no video file.

    Args:
        url: Video URL
        output_dir: Where to save the SRT and plain-text files
        filename_base: Output file base name (without extension)
        langs: Comma-separated language codes
        prefer_manual: If True, try to fetch uploader-authored subs first, fall back to auto.
                       If False, only fetch auto-generated captions.

    Returns:
        TranscriptResult with the SRT path and a plain-text version of the transcript.
    """
    _check_yt_dlp()

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_template = str(
        output_dir / (f"{filename_base}.%(ext)s" if filename_base else "%(title)s.%(ext)s")
    )

    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-auto-subs",
        "--sub-langs", langs,
        "--convert-subs", "srt",
        "-o", output_template,
        "--no-playlist",
        "--restrict-filenames",
    ]

    if prefer_manual:
        cmd.insert(3, "--write-subs")  # also try authored subs

    cmd.append(url)

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        return TranscriptResult(
            success=False, srt_path=None, stderr=exc.stderr or str(exc)
        )

    # Find the produced .srt file (newest)
    srts = sorted(
        output_dir.glob("*.srt"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not srts:
        return TranscriptResult(
            success=False,
            srt_path=None,
            stderr="No SRT file produced. Video may have no captions available.",
        )

    srt_path = srts[0]
    plain = _srt_to_plain(srt_path)

    # Write plain text alongside the SRT
    plain_path = srt_path.with_suffix(".txt")
    plain_path.write_text(plain, encoding="utf-8")

    return TranscriptResult(success=True, srt_path=srt_path, plain_text=plain)
