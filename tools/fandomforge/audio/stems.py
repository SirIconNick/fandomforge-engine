"""Audio stem separation via demucs.

Given a mixed track (dialogue + music + sfx), separate into clean stems:
    vocals.wav, drums.wav, bass.wav, other.wav

Primary use cases:
- Clean dialogue rips where the source has game music bleeding through
- Isolate percussion for tighter impact alignment on drops
- Get a clean instrumental for background layering
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = ["StemResult", "separate_stems", "demucs_available"]


@dataclass
class StemResult:
    source_path: Path
    stems_dir: Path
    vocals: Path | None
    drums: Path | None
    bass: Path | None
    other: Path | None
    model: str


def demucs_available() -> bool:
    try:
        import demucs.separate  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


def separate_stems(
    audio_or_video_path: str | Path,
    output_dir: str | Path,
    *,
    model: str = "htdemucs",
    two_stems: str | None = None,
) -> StemResult | None:
    """Separate the given audio or video file into stems.

    Args:
        audio_or_video_path: Source file (anything ffmpeg can read)
        output_dir: Where to write stems_dir/<model>/<track>/...wav
        model: demucs model name. 'htdemucs' (default) is the recommended
            high-quality model. 'htdemucs_ft' is slower but even better.
            'mdx_extra' for a different trade-off.
        two_stems: If set to 'vocals', run fast 2-stem mode producing just
            vocals.wav + no_vocals.wav (fastest path for dialogue cleaning).

    Returns:
        StemResult with paths, or None if demucs is unavailable.
    """
    if not demucs_available():
        return None
    if shutil.which("ffmpeg") is None:
        return None

    src = Path(audio_or_video_path).resolve()
    if not src.exists():
        raise FileNotFoundError(src)

    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    cmd = [
        "python", "-m", "demucs.separate",
        "-n", model,
        "-o", str(out),
        "--mp3",
    ]
    if two_stems:
        cmd += ["--two-stems", two_stems]
    cmd.append(str(src))

    try:
        subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            check=True,
            timeout=1800,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"demucs failed: {e}") from e

    # demucs writes to out/<model>/<track_name>/<stem>.mp3
    track_dir = out / model / src.stem
    return StemResult(
        source_path=src,
        stems_dir=track_dir,
        vocals=track_dir / "vocals.mp3" if (track_dir / "vocals.mp3").exists() else None,
        drums=track_dir / "drums.mp3" if (track_dir / "drums.mp3").exists() else None,
        bass=track_dir / "bass.mp3" if (track_dir / "bass.mp3").exists() else None,
        other=track_dir / "other.mp3" if (track_dir / "other.mp3").exists() else None,
        model=model,
    )
