"""Text-to-speech for narration / voiceover.

Uses OpenAI TTS API (cloud, high quality) when key is set.
Falls back to `say` on macOS or nothing if neither available.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from fandomforge.intelligence.openai_helper import _load_env


@dataclass
class TTSResult:
    success: bool
    output_path: Path | None
    backend: str = ""  # "openai" or "macos_say"
    error: str = ""


OPENAI_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]


def synthesize_speech(
    text: str,
    output_path: str | Path,
    *,
    voice: str = "onyx",  # Masculine, serious — fits a narration role
    model: str = "tts-1-hd",  # higher quality (tts-1 is faster/cheaper)
    project_root: Path | str = ".",
) -> TTSResult:
    """Generate speech audio from text.

    Tries OpenAI TTS first (requires OPENAI_API_KEY), falls back to macOS `say`.
    """
    _load_env(project_root)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Try OpenAI first
    if os.environ.get("OPENAI_API_KEY"):
        try:
            from openai import OpenAI
            client = OpenAI()
            # Use streaming to write directly to file
            if voice not in OPENAI_VOICES:
                voice = "onyx"
            response = client.audio.speech.create(
                model=model,
                voice=voice,
                input=text,
                response_format="mp3",
            )
            # Write the returned bytes
            response.write_to_file(str(out))
            if out.exists() and out.stat().st_size > 0:
                return TTSResult(success=True, output_path=out, backend="openai")
        except Exception as exc:
            # Fall through to macOS say
            last_error = str(exc)
    else:
        last_error = "OPENAI_API_KEY not set"

    # macOS fallback
    if shutil.which("say"):
        try:
            aiff_path = out.with_suffix(".aiff")
            subprocess.run(
                ["say", "-v", "Alex", "-o", str(aiff_path), text],
                check=True,
                timeout=60,
            )
            # Convert AIFF to MP3 via ffmpeg
            if shutil.which("ffmpeg"):
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-loglevel", "error",
                        "-i", str(aiff_path),
                        "-c:a", "libmp3lame", "-b:a", "192k",
                        str(out),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                aiff_path.unlink(missing_ok=True)
            else:
                # No ffmpeg — just return the AIFF
                out = aiff_path
            return TTSResult(success=True, output_path=out, backend="macos_say")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)

    return TTSResult(
        success=False,
        output_path=None,
        error=f"No TTS backend worked. Last error: {last_error}",
    )
