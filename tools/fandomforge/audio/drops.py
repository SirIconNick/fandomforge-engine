"""Drop and buildup detection from the energy envelope."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import librosa
import numpy as np


@dataclass
class Drop:
    """A drop or peak-energy event in the song."""

    time: float
    intensity: float
    type: str  # "main_drop", "second_drop", "outro_drop", "peak"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Buildup:
    """A rising-energy section before a drop."""

    start: float
    end: float
    curve: str  # "linear", "exponential", "stepped"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Breakdown:
    """A low-energy valley section."""

    start: float
    end: float
    intensity: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _smooth(envelope: np.ndarray, window: int = 20) -> np.ndarray:
    """Moving-average smoothing."""
    if window < 2 or envelope.size < window:
        return envelope
    kernel = np.ones(window) / window
    return np.convolve(envelope, kernel, mode="same")


def _find_peaks(
    envelope: np.ndarray,
    *,
    min_separation: int,
    threshold_ratio: float = 0.7,
) -> list[int]:
    """Simple peak detection: local maxima above a threshold, separated."""
    if envelope.size == 0:
        return []
    threshold = float(envelope.max()) * threshold_ratio
    peaks: list[int] = []
    last_peak = -min_separation
    for i in range(1, len(envelope) - 1):
        if (
            envelope[i] >= threshold
            and envelope[i] > envelope[i - 1]
            and envelope[i] >= envelope[i + 1]
            and i - last_peak >= min_separation
        ):
            peaks.append(i)
            last_peak = i
    return peaks


def detect_drops(
    audio_path: str | Path,
    *,
    sr: int = 22050,
    low_freq_cutoff: int = 200,
    snare_bias: bool = False,
) -> list[Drop]:
    """Detect drops in a song.

    A drop is a moment where the song's energy — especially low-frequency
    energy — jumps dramatically compared to the preceding section.

    Args:
        audio_path: path to audio file
        sr: sample rate
        low_freq_cutoff: Hz below which we consider "bass" energy
        snare_bias: if True, weight high-frequency flux more heavily (for
                    snare-driven drops that don't use heavy bass)

    Returns:
        List of detected drops, ranked by intensity.
    """
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")

    y, actual_sr = librosa.load(str(path), sr=sr, mono=True)
    duration = librosa.get_duration(y=y, sr=actual_sr)

    # Spectral representation
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=512))
    freqs = librosa.fft_frequencies(sr=actual_sr, n_fft=2048)

    # Low-frequency energy envelope (bass content)
    low_mask = freqs < low_freq_cutoff
    low_energy = S[low_mask].sum(axis=0)

    # High-frequency energy (for snare detection)
    high_mask = (freqs > 2000) & (freqs < 8000)
    high_energy = S[high_mask].sum(axis=0)

    # Spectral flux — how much the spectrum is changing
    spec_flux = np.diff(S.sum(axis=0))
    spec_flux = np.concatenate([[0.0], spec_flux])
    spec_flux = np.maximum(spec_flux, 0)  # only positive changes

    # Combine signals. Low-energy + flux is the standard drop detector.
    if snare_bias:
        combined = 0.3 * low_energy + 0.4 * high_energy + 0.3 * spec_flux
    else:
        combined = 0.5 * low_energy + 0.5 * spec_flux

    # Smooth
    combined_smooth = _smooth(combined, window=40)

    # Peak detection — drops are at least ~15 seconds apart for typical songs
    hop_duration = 512 / actual_sr
    min_sep_frames = int(15.0 / hop_duration)
    peak_indices = _find_peaks(
        combined_smooth,
        min_separation=min_sep_frames,
        threshold_ratio=0.65,
    )

    if not peak_indices:
        return []

    # Convert to Drop objects, normalize intensity to [0, 1]
    max_val = float(combined_smooth[peak_indices].max())
    if max_val <= 0:
        return []

    # Rank by intensity
    drops_raw = [
        (i * hop_duration, float(combined_smooth[i]) / max_val)
        for i in peak_indices
    ]
    drops_raw.sort(key=lambda x: -x[1])  # highest intensity first

    # Classify: main = highest, second = next, then numbered
    drops: list[Drop] = []
    for rank, (t, intensity) in enumerate(drops_raw):
        if t >= duration - 1.0:
            # skip end-of-file artifacts
            continue
        if rank == 0:
            drop_type = "main_drop"
        elif rank == 1:
            drop_type = "second_drop"
        else:
            drop_type = f"drop_{rank + 1}"
        drops.append(Drop(time=round(t, 3), intensity=round(intensity, 3), type=drop_type))

    # Re-sort by time for output
    drops.sort(key=lambda d: d.time)
    return drops


def detect_buildups(
    audio_path: str | Path,
    drops: list[Drop],
    *,
    sr: int = 22050,
    lookback_sec: float = 8.0,
) -> list[Buildup]:
    """Detect buildups preceding each drop.

    A buildup is the rising-energy stretch right before a drop. We look back
    `lookback_sec` from each drop and check if energy was rising.
    """
    path = Path(audio_path)
    y, actual_sr = librosa.load(str(path), sr=sr, mono=True)

    rms = librosa.feature.rms(y=y)[0]
    hop = 512 / actual_sr

    buildups: list[Buildup] = []
    for drop in drops:
        end = drop.time
        start = max(0.0, end - lookback_sec)
        start_frame = int(start / hop)
        end_frame = min(len(rms) - 1, int(end / hop))
        if end_frame <= start_frame + 5:
            continue
        region = rms[start_frame:end_frame]
        if region.size < 3:
            continue

        # Check if energy rose significantly across the region
        first_half = float(region[: len(region) // 2].mean())
        second_half = float(region[len(region) // 2 :].mean())
        if second_half < first_half * 1.2:
            continue  # not a clear buildup

        # Classify curve shape
        slope = np.polyfit(np.arange(len(region)), region, 1)[0]
        curvature = (region[-1] - region[0]) / max(region.size, 1)
        if slope > 0 and curvature > (region.std() * 2.0):
            curve = "exponential"
        elif slope > 0:
            curve = "linear"
        else:
            curve = "stepped"

        buildups.append(Buildup(start=round(start, 3), end=round(end, 3), curve=curve))

    return buildups


def detect_breakdowns(
    audio_path: str | Path,
    *,
    sr: int = 22050,
    min_duration: float = 3.0,
    threshold_ratio: float = 0.3,
) -> list[Breakdown]:
    """Detect low-energy breakdown sections.

    A breakdown is a sustained stretch of low energy in the song — a rest
    section. Useful for identifying valleys in the energy curve.
    """
    path = Path(audio_path)
    y, actual_sr = librosa.load(str(path), sr=sr, mono=True)

    rms = librosa.feature.rms(y=y)[0]
    hop = 512 / actual_sr

    if rms.size == 0:
        return []

    # Normalize
    max_rms = float(rms.max())
    if max_rms <= 0:
        return []
    rms_norm = rms / max_rms

    min_frames = int(min_duration / hop)

    breakdowns: list[Breakdown] = []
    in_breakdown = False
    start_frame = 0
    for i, val in enumerate(rms_norm):
        if val < threshold_ratio and not in_breakdown:
            in_breakdown = True
            start_frame = i
        elif val >= threshold_ratio and in_breakdown:
            in_breakdown = False
            if i - start_frame >= min_frames:
                breakdowns.append(
                    Breakdown(
                        start=round(start_frame * hop, 3),
                        end=round(i * hop, 3),
                        intensity=round(
                            float(rms_norm[start_frame:i].mean()), 3
                        ),
                    )
                )
    # Handle trailing breakdown
    if in_breakdown and len(rms_norm) - start_frame >= min_frames:
        breakdowns.append(
            Breakdown(
                start=round(start_frame * hop, 3),
                end=round((len(rms_norm) - 1) * hop, 3),
                intensity=round(float(rms_norm[start_frame:].mean()), 3),
            )
        )

    return breakdowns
