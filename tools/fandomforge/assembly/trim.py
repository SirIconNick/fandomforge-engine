"""Silence trimming — wrap auto-editor to remove dead space from audio/video clips."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TrimResult:
    success: bool
    output_path: Path | None
    stderr: str = ""


def trim_silence(
    input_path: Path | str,
    output_path: Path | str,
    *,
    silence_threshold: float = 0.04,
    margin_sec: float = 0.1,
) -> TrimResult:
    """Trim silence from an audio or video file using auto-editor.

    Args:
        input_path: input file
        output_path: output file (same format as input recommended)
        silence_threshold: audio level below this is considered silence (0-1)
        margin_sec: preserve this much audio around spoken sections
    """
    if shutil.which("auto-editor") is None:
        return TrimResult(
            success=False,
            output_path=None,
            stderr="auto-editor not installed. Run: pip install auto-editor",
        )

    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        return TrimResult(
            success=False,
            output_path=None,
            stderr=f"Input not found: {input_path}",
        )

    cmd = [
        "auto-editor",
        str(input_path),
        "--edit", f"audio:threshold={silence_threshold}",
        "--margin", f"{margin_sec}sec",
        "-o", str(output_path),
        "--no-open",
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=600)
    except subprocess.CalledProcessError as exc:
        return TrimResult(
            success=False,
            output_path=None,
            stderr=(exc.stderr or str(exc))[-1000:],
        )
    except subprocess.TimeoutExpired:
        return TrimResult(success=False, output_path=None, stderr="auto-editor timed out.")

    return TrimResult(success=True, output_path=output_path)
