"""Caption generator -- produce SRT and WebVTT caption files for finished edits.

Two modes
---------
1. edit_plan mode  -- generate captions from the DialogueEntry / DialogueCue
   objects already in the edit plan.  Timecodes come directly from the cue
   metadata so they are guaranteed to match the assembled timeline.

2. Whisper mode -- pass the rendered video or audio to OpenAI Whisper and get a
   full auto-generated transcript.  Catches ambient dialogue the editor didn't
   explicitly plan (radio chatter, background lines, song lyrics, etc.).

Speaker labels
--------------
When include_speaker=True, each caption line is prefixed with the character
name in upper-case followed by a colon, e.g. ``LEON: These things always...``

Output formats
--------------
Both .srt (standard) and .vtt (WebVTT for YouTube and web players) are written.
If you only need one format call the helpers _write_srt / _write_vtt directly.

Public API
----------
    from fandomforge.intelligence.caption_generator import (
        generate_captions,
        generate_captions_from_audio,
        CaptionEntry,
    )

    # From edit plan
    generate_captions(edit_plan, "exports/my_edit.srt", include_speaker=True)

    # From Whisper
    generate_captions_from_audio("exports/my_edit.mp4", "exports/my_edit.srt")
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fandomforge.intelligence.shot_optimizer import EditPlan

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class CaptionEntry:
    """A single caption line with timing and optional speaker label.

    Attributes:
        index: One-based sequence number in the caption file.
        start_sec: Caption display start time in seconds.
        end_sec: Caption display end time in seconds.
        speaker: Character name.  Empty string when not applicable.
        text: Caption text.  Multi-line text uses \\n inside this string.
    """

    index: int
    start_sec: float
    end_sec: float
    speaker: str
    text: str


# ---------------------------------------------------------------------------
# Timecode formatters
# ---------------------------------------------------------------------------


def _sec_to_srt_tc(seconds: float) -> str:
    """Convert a float seconds value to SRT timecode ``HH:MM:SS,mmm``."""
    seconds = max(0.0, seconds)
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    s = (total_ms // 1000) % 60
    m = (total_ms // 60000) % 60
    h = total_ms // 3600000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _sec_to_vtt_tc(seconds: float) -> str:
    """Convert a float seconds value to WebVTT timecode ``HH:MM:SS.mmm``."""
    return _sec_to_srt_tc(seconds).replace(",", ".")


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def _write_srt(entries: list[CaptionEntry], out_path: Path, include_speaker: bool) -> None:
    """Write a list of CaptionEntry objects to an SRT file."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for entry in entries:
        lines.append(str(entry.index))
        lines.append(f"{_sec_to_srt_tc(entry.start_sec)} --> {_sec_to_srt_tc(entry.end_sec)}")
        display_text = entry.text.strip()
        if include_speaker and entry.speaker:
            display_text = f"{entry.speaker.upper()}: {display_text}"
        lines.append(display_text)
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def _write_vtt(entries: list[CaptionEntry], out_path: Path, include_speaker: bool) -> None:
    """Write a list of CaptionEntry objects to a WebVTT (.vtt) file."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = ["WEBVTT", ""]
    for entry in entries:
        lines.append(f"{_sec_to_vtt_tc(entry.start_sec)} --> {_sec_to_vtt_tc(entry.end_sec)}")
        display_text = entry.text.strip()
        if include_speaker and entry.speaker:
            display_text = f"<v {entry.speaker}>{display_text}</v>"
        lines.append(display_text)
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Edit-plan mode
# ---------------------------------------------------------------------------


def _entries_from_edit_plan(
    edit_plan: "EditPlan",
    include_speaker: bool,
) -> list[CaptionEntry]:
    """Build a CaptionEntry list from the VOPlacement objects in an EditPlan.

    Falls back to checking for a ``dialogue_entries`` attribute that holds
    raw DialogueEntry objects (from assembly.dialogue).
    """
    raw_placements: list[tuple[float, float, str, str]] = []  # (start, end, speaker, text)

    # Primary source: EditPlan.dialogue_placements (VOPlacement list)
    if hasattr(edit_plan, "dialogue_placements"):
        for vop in edit_plan.dialogue_placements:
            start = float(vop.start_time)
            end = start + float(vop.duration)
            speaker = ""
            if include_speaker:
                # VOPlacement.slot_name is editorial context, not speaker name.
                # Try to derive speaker from expected_line prefix "LEON: ..." pattern.
                raw_line = vop.expected_line or ""
                m = re.match(r"^([A-Z][A-Z ]{1,20}):\s*(.+)", raw_line)
                if m:
                    speaker = m.group(1).strip()
                    raw_line = m.group(2).strip()
            raw_placements.append((start, end, speaker, vop.expected_line or ""))

    # Secondary source: dialogue_entries attribute (DialogueEntry objects)
    elif hasattr(edit_plan, "dialogue_entries"):
        for entry in edit_plan.dialogue_entries:
            start = float(entry.start_sec)
            end = start + float(entry.duration_sec)
            speaker = entry.character if include_speaker else ""
            raw_placements.append((start, end, speaker, entry.line or ""))

    if not raw_placements:
        return []

    # Sort by start time
    raw_placements.sort(key=lambda x: x[0])

    entries: list[CaptionEntry] = []
    for idx, (start, end, speaker, text) in enumerate(raw_placements, 1):
        if not text.strip():
            continue
        # Clamp minimum display duration to 0.5 s, maximum to 7 s
        duration = max(0.5, min(7.0, end - start))
        entries.append(
            CaptionEntry(
                index=idx,
                start_sec=start,
                end_sec=start + duration,
                speaker=speaker,
                text=text.strip(),
            )
        )

    return entries


# ---------------------------------------------------------------------------
# SRT parser (for converting Whisper SRT output back to CaptionEntry)
# ---------------------------------------------------------------------------


def _parse_srt_text(srt_text: str) -> list[CaptionEntry]:
    """Parse raw SRT text into a list of CaptionEntry objects."""
    entries: list[CaptionEntry] = []
    blocks = re.split(r"\n\s*\n", srt_text.strip())
    for block in blocks:
        block_lines = block.strip().splitlines()
        if len(block_lines) < 3:
            continue
        try:
            idx = int(block_lines[0].strip())
        except ValueError:
            continue
        tc_line = block_lines[1]
        m = re.match(
            r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})",
            tc_line,
        )
        if not m:
            continue

        def _tc_to_sec(tc: str) -> float:
            tc = tc.replace(",", ".")
            parts = tc.split(":")
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])

        start_sec = _tc_to_sec(m.group(1))
        end_sec = _tc_to_sec(m.group(2))
        text = "\n".join(block_lines[2:]).strip()

        # Extract speaker from "LEON: ..." prefix if present
        speaker_match = re.match(r"^([A-Z][A-Z ]{1,20}):\s*(.+)", text, re.DOTALL)
        if speaker_match:
            speaker = speaker_match.group(1).strip()
            text = speaker_match.group(2).strip()
        else:
            speaker = ""

        entries.append(
            CaptionEntry(
                index=idx,
                start_sec=start_sec,
                end_sec=end_sec,
                speaker=speaker,
                text=text,
            )
        )
    return entries


# ---------------------------------------------------------------------------
# Whisper audio extraction helper
# ---------------------------------------------------------------------------


def _extract_audio_for_whisper(video_path: Path) -> Path | None:
    """Extract a compressed mono audio track from a video for Whisper transcription.

    Returns the path to a temporary .mp3 file, or None on failure.
    The caller is responsible for deleting the file when done.
    """
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp_path = Path(tmp.name)
        tmp.close()

        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-nostats",
            "-i", str(video_path),
            "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "libmp3lame", "-b:a", "48k",
            str(tmp_path),
        ]
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=180,
            check=False,
        )
        if result.returncode != 0 or not tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
            return None
        return tmp_path
    except Exception as exc:
        logger.debug("Audio extraction for Whisper failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_captions(
    edit_plan: "EditPlan",
    output_srt: str | Path,
    *,
    include_speaker: bool = True,
) -> None:
    """Generate SRT and VTT caption files from an EditPlan's dialogue placements.

    Timecodes are taken directly from the VOPlacement / DialogueEntry objects
    in the edit plan, so they match the assembled timeline exactly.

    Both ``<output_srt>`` and the corresponding ``.vtt`` file (same stem, .vtt
    extension) are written to disk.

    Args:
        edit_plan: A completed EditPlan from shot_optimizer or a compatible
            object with a ``dialogue_placements`` or ``dialogue_entries``
            attribute.
        output_srt: Destination path for the SRT file.
        include_speaker: If True, prefix each caption with the speaker name.

    Raises:
        ValueError: When the edit plan contains no dialogue placement data.
    """
    output_srt = Path(output_srt)
    entries = _entries_from_edit_plan(edit_plan, include_speaker)

    if not entries:
        raise ValueError(
            "edit_plan contains no dialogue_placements or dialogue_entries -- "
            "nothing to caption. Use generate_captions_from_audio() for Whisper."
        )

    _write_srt(entries, output_srt, include_speaker=False)  # speaker already embedded above

    vtt_path = output_srt.with_suffix(".vtt")
    _write_vtt(entries, vtt_path, include_speaker=False)

    logger.info(
        "Captions written: %d lines -> %s + %s",
        len(entries),
        output_srt,
        vtt_path,
    )


def generate_captions_from_audio(
    video_path: str | Path,
    output_srt: str | Path,
    *,
    language: str = "en",
    model: str = "whisper-1",
    project_root: str | Path = ".",
) -> None:
    """Transcribe a video's audio track via OpenAI Whisper and write SRT + VTT files.

    This catches any audio content in the final mix -- dialogue, ambient lines,
    song lyrics -- that was not part of the explicit edit plan.

    Requires OPENAI_API_KEY in the environment or a .env file at project_root.

    Args:
        video_path: Path to the rendered MP4/MKV/WebM.
        output_srt: Destination path for the SRT file.
        language: BCP-47 language code (default "en").
        model: Whisper model name.  "whisper-1" is OpenAI's hosted model.
        project_root: Directory to search for .env files containing API keys.

    Raises:
        RuntimeError: When transcription fails for any reason.
    """
    video_path = Path(video_path)
    output_srt = Path(output_srt)

    if not video_path.exists():
        raise RuntimeError(f"Video not found: {video_path}")

    # Load env key from openai_helper pattern
    import os

    if not os.environ.get("OPENAI_API_KEY"):
        for env_name in (".env", ".env.local"):
            env_file = Path(project_root) / env_name
            if env_file.exists():
                for raw_line in env_file.read_text().splitlines():
                    raw_line = raw_line.strip()
                    if raw_line.startswith("#") or "=" not in raw_line:
                        continue
                    k, v = raw_line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k == "OPENAI_API_KEY" and v:
                        os.environ["OPENAI_API_KEY"] = v
                        break

    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY not set. "
            "Set it in your environment or add it to a .env file."
        )

    try:
        from openai import OpenAI  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError("OpenAI SDK not installed. pip install openai") from exc

    # Extract audio if the file is large (Whisper API limit is 25 MB)
    size_mb = video_path.stat().st_size / (1024 * 1024)
    tmp_audio: Path | None = None
    upload_path: Path

    if size_mb > 24:
        tmp_audio = _extract_audio_for_whisper(video_path)
        if tmp_audio is None:
            raise RuntimeError(
                f"Audio extraction from {video_path} failed. "
                "Make sure ffmpeg is installed and the file is readable."
            )
        upload_path = tmp_audio
    else:
        upload_path = video_path

    try:
        client = OpenAI()
        with upload_path.open("rb") as f:
            response = client.audio.transcriptions.create(
                model=model,
                file=f,
                language=language,
                response_format="srt",
            )
        srt_text = response if isinstance(response, str) else str(response)
    except Exception as exc:
        raise RuntimeError(f"Whisper transcription failed: {exc}") from exc
    finally:
        if tmp_audio is not None:
            tmp_audio.unlink(missing_ok=True)

    # Write SRT
    output_srt.parent.mkdir(parents=True, exist_ok=True)
    output_srt.write_text(srt_text, encoding="utf-8")

    # Parse SRT and write VTT
    entries = _parse_srt_text(srt_text)
    vtt_path = output_srt.with_suffix(".vtt")
    _write_vtt(entries, vtt_path, include_speaker=False)

    logger.info(
        "Whisper captions written: %d segments -> %s + %s",
        len(entries),
        output_srt,
        vtt_path,
    )
