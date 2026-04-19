"""Energy curve computation — per-second normalized energy for visualizing and matching."""

from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np


def compute_energy_curve(
    audio_path: str | Path,
    *,
    sr: int = 22050,
    resolution_sec: float = 1.0,
) -> list[tuple[float, float]]:
    """Compute a downsampled energy curve for the song.

    Args:
        audio_path: path to audio file
        sr: sample rate
        resolution_sec: how coarse the curve is. 1.0 = one sample per second.

    Returns:
        List of (time_sec, energy_0_to_1) tuples.
    """
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")

    y, actual_sr = librosa.load(str(path), sr=sr, mono=True)

    # RMS energy
    frame_length = 2048
    hop_length = 512
    rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop_length)[0]

    # Time per frame
    hop_sec = hop_length / actual_sr

    # Downsample to target resolution
    frames_per_sample = max(1, int(resolution_sec / hop_sec))
    samples = []
    for i in range(0, len(rms), frames_per_sample):
        chunk = rms[i : i + frames_per_sample]
        if chunk.size == 0:
            continue
        samples.append(float(chunk.mean()))

    if not samples:
        return []

    max_energy = max(samples)
    if max_energy <= 0:
        return [(i * resolution_sec, 0.0) for i in range(len(samples))]

    # Normalize to [0, 1]
    curve = [
        (round(i * resolution_sec, 3), round(s / max_energy, 3))
        for i, s in enumerate(samples)
    ]
    return curve
