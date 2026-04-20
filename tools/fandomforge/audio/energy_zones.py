"""Energy zone classification — split a song into labeled structural regions
with spectral-band breakdown and transient typing.

Foundation for downstream pacing logic. The clip-assembly engine queries this
module to answer:
  - "Which 250 ms slice am I about to place a shot in — low/mid/high energy?"
  - "Is this a drop, a buildup, a breakdown, or a held passage?"
  - "Where are the bass-heavy moments vs the treble-bright moments?"
  - "Are these onsets percussive (cut here) or sustained (don't cut)?"

Outputs `energy-zones.json` parallel to `beat-map.json`. Beat-map stays
untouched — energy zones is an additive layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import librosa
import numpy as np


# Frequency band edges (Hz). Standard split for music analysis:
# bass = sub-bass + bass (kick, low toms, sub synths)
# mid  = vocals, snares, most melodic content
# treble = hi-hats, cymbals, brightness
BAND_EDGES_HZ = {
    "bass": (20.0, 250.0),
    "mid": (250.0, 4000.0),
    "treble": (4000.0, 20000.0),
}

# Zone classification thresholds (normalized 0-1 RMS).
LOW_THRESHOLD = 0.33
HIGH_THRESHOLD = 0.66

# Min duration for a labeled zone (sec). Shorter contiguous runs get merged
# into the surrounding zone — avoids fragmentary 1-frame labels.
MIN_ZONE_DURATION_SEC = 0.75

# Transient-typing thresholds. Onsets with spectral_flatness < this are
# treated as percussive (sharp transients suitable for cuts). Higher = more
# tonal / sustained content (don't cut here).
PERCUSSIVE_FLATNESS_MAX = 0.35


@dataclass
class EnergyZone:
    """A contiguous region of similar energy character."""
    start_sec: float
    end_sec: float
    label: str  # low | mid | high | drop | buildup | breakdown
    confidence: float  # 0-1, how strongly the data supports this label
    avg_energy: float  # 0-1 normalized RMS across the region

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BandSample:
    """Per-band energy at a single time point."""
    time_sec: float
    bass: float
    mid: float
    treble: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Transient:
    """A detected onset with type label."""
    time_sec: float
    amplitude: float  # 0-1
    kind: str  # percussive | sustained
    flatness: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EnergyZonesResult:
    """Full energy-zone analysis for a single audio file."""
    schema_version: int
    duration_sec: float
    sample_rate_hz: int
    resolution_sec: float
    zones: list[EnergyZone] = field(default_factory=list)
    bands: list[BandSample] = field(default_factory=list)
    transients: list[Transient] = field(default_factory=list)
    generator: str = "ff energy zones"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Round floats for compact JSON
        return d


def _band_energies(
    stft_mag: np.ndarray,
    freqs: np.ndarray,
) -> dict[str, np.ndarray]:
    """Sum STFT magnitude across each frequency band per frame."""
    out: dict[str, np.ndarray] = {}
    for name, (lo, hi) in BAND_EDGES_HZ.items():
        mask = (freqs >= lo) & (freqs < hi)
        if not mask.any():
            out[name] = np.zeros(stft_mag.shape[1])
        else:
            out[name] = stft_mag[mask, :].sum(axis=0)
    return out


def _normalize_curve(curve: np.ndarray) -> np.ndarray:
    """Min-max normalize to [0, 1]. Guards against silent input."""
    if curve.size == 0:
        return curve
    peak = float(curve.max())
    if peak <= 0:
        return np.zeros_like(curve)
    return curve / peak


def _label_for_energy(e: float) -> str:
    if e < LOW_THRESHOLD:
        return "low"
    if e < HIGH_THRESHOLD:
        return "mid"
    return "high"


def _merge_runs(
    times: np.ndarray,
    energies: np.ndarray,
    min_zone_sec: float,
) -> list[EnergyZone]:
    """Walk the energy curve, group contiguous same-label samples into zones,
    and absorb short runs into neighbors so we don't emit 1-sample zones."""
    if times.size == 0 or energies.size == 0:
        return []

    labels = [_label_for_energy(float(e)) for e in energies]

    raw_zones: list[tuple[int, int, str]] = []
    cur_label = labels[0]
    start_idx = 0
    for i in range(1, len(labels)):
        if labels[i] != cur_label:
            raw_zones.append((start_idx, i - 1, cur_label))
            cur_label = labels[i]
            start_idx = i
    raw_zones.append((start_idx, len(labels) - 1, cur_label))

    # Merge short runs into the larger neighbor so we never emit fragments.
    if len(raw_zones) > 1 and len(times) > 1:
        sample_dt = float(times[1] - times[0])
        merged: list[tuple[int, int, str]] = []
        for s, e, lbl in raw_zones:
            dur = (e - s + 1) * sample_dt
            if dur < min_zone_sec and merged:
                # absorb into previous
                ps, pe, plbl = merged[-1]
                merged[-1] = (ps, e, plbl)
            else:
                merged.append((s, e, lbl))
        raw_zones = merged

    zones: list[EnergyZone] = []
    for s, e, lbl in raw_zones:
        seg_energies = energies[s : e + 1]
        avg = float(seg_energies.mean()) if seg_energies.size else 0.0
        # Confidence = distance from threshold edges, normalized
        if lbl == "low":
            conf = float(min(1.0, (LOW_THRESHOLD - avg) / LOW_THRESHOLD))
        elif lbl == "high":
            denom = max(1e-6, 1.0 - HIGH_THRESHOLD)
            conf = float(min(1.0, (avg - HIGH_THRESHOLD) / denom))
        else:
            mid_center = (LOW_THRESHOLD + HIGH_THRESHOLD) / 2
            half_band = (HIGH_THRESHOLD - LOW_THRESHOLD) / 2
            conf = float(max(0.0, 1.0 - abs(avg - mid_center) / max(1e-6, half_band)))

        zones.append(EnergyZone(
            start_sec=round(float(times[s]), 3),
            end_sec=round(float(times[e] + (times[1] - times[0]) if len(times) > 1 else times[e]), 3),
            label=lbl,
            confidence=round(max(0.0, min(1.0, conf)), 3),
            avg_energy=round(avg, 3),
        ))
    return zones


def _overlay_drops_buildups(
    zones: list[EnergyZone],
    beat_map: dict[str, Any] | None,
) -> list[EnergyZone]:
    """Promote labels where the beat-map identifies a drop / buildup / breakdown.
    The original RMS-derived label becomes a fallback; the beat-map call wins.
    """
    if not beat_map:
        return zones

    # Drops are point events with `time` field. Promote the zone covering each
    # drop to label="drop".
    for drop in beat_map.get("drops") or []:
        t = float(drop.get("time", -1))
        for z in zones:
            if z.start_sec <= t < z.end_sec:
                z.label = "drop"
                z.confidence = max(z.confidence, 0.85)
                break

    def _overlap_pct(zs: float, ze: float, ws: float, we: float) -> float:
        """Fraction of the zone covered by the [ws, we] window."""
        zone_dur = max(1e-6, ze - zs)
        ovl = max(0.0, min(ze, we) - max(zs, ws))
        return ovl / zone_dur

    # Buildup / breakdown windows promote any zone with >50% overlap with
    # the window. Drops are single-point events handled above.
    for bu in beat_map.get("buildups") or []:
        bs = float(bu.get("start", -1))
        be = float(bu.get("end", -1))
        if bs < 0 or be <= bs:
            continue
        for z in zones:
            if _overlap_pct(z.start_sec, z.end_sec, bs, be) > 0.5:
                z.label = "buildup"
                z.confidence = max(z.confidence, 0.80)

    for bd in beat_map.get("breakdowns") or []:
        bs = float(bd.get("start", -1))
        be = float(bd.get("end", -1))
        if bs < 0 or be <= bs:
            continue
        for z in zones:
            if _overlap_pct(z.start_sec, z.end_sec, bs, be) > 0.5:
                z.label = "breakdown"
                z.confidence = max(z.confidence, 0.80)

    return zones


def _classify_transients(
    onset_times: np.ndarray,
    onset_strengths: np.ndarray,
    flatness_curve: np.ndarray,
    flatness_times: np.ndarray,
) -> list[Transient]:
    """Tag each onset percussive vs sustained by sampling spectral flatness.

    Percussive transients have sharp, broadband attacks (low flatness ≈ noisy
    but spread, but in this codepath we use the simpler heuristic that low
    spectral flatness near the onset means tonal-spectrum sharp attack)."""
    if onset_times.size == 0:
        return []
    # Normalize amplitudes
    if onset_strengths.size:
        peak = float(onset_strengths.max()) or 1.0
        norm_amps = onset_strengths / peak
    else:
        norm_amps = np.zeros_like(onset_times)

    out: list[Transient] = []
    for ot, amp in zip(onset_times, norm_amps):
        # Find nearest flatness sample
        if flatness_times.size:
            idx = int(np.argmin(np.abs(flatness_times - ot)))
            f = float(flatness_curve[idx])
        else:
            f = 0.5
        kind = "percussive" if f < PERCUSSIVE_FLATNESS_MAX else "sustained"
        out.append(Transient(
            time_sec=round(float(ot), 3),
            amplitude=round(float(amp), 3),
            kind=kind,
            flatness=round(f, 3),
        ))
    return out


def analyze_energy_zones(
    audio_path: str | Path,
    *,
    sr: int = 22050,
    resolution_sec: float = 0.25,
    beat_map: dict[str, Any] | None = None,
) -> EnergyZonesResult:
    """Run the full energy-zone analysis pass.

    Args:
        audio_path: path to the song audio file.
        sr: sample rate to load at. 22050 is the standard librosa default and
            plenty of resolution for this analysis.
        resolution_sec: time resolution of the output bands + zone boundaries.
            Default 0.25s = 4 samples/sec, fine enough for accurate cut
            placement and dialogue-window detection.
        beat_map: optional pre-analyzed beat-map.json content. If provided,
            drops / buildups / breakdowns from beat-map promote the overlapping
            energy zones to those richer labels.

    Returns:
        EnergyZonesResult with zones, bands, and transients populated.
    """
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")

    y, actual_sr = librosa.load(str(path), sr=sr, mono=True)
    if y.size == 0:
        return EnergyZonesResult(
            schema_version=1,
            duration_sec=0.0,
            sample_rate_hz=actual_sr,
            resolution_sec=resolution_sec,
        )
    duration_sec = float(len(y) / actual_sr)

    # STFT for spectral bands. n_fft=2048 gives ~10 Hz resolution at 22050.
    n_fft = 2048
    hop_length = 512
    stft = librosa.stft(y, n_fft=n_fft, hop_length=hop_length)
    stft_mag = np.abs(stft)
    freqs = librosa.fft_frequencies(sr=actual_sr, n_fft=n_fft)
    band_curves = _band_energies(stft_mag, freqs)

    hop_sec = hop_length / actual_sr
    frame_times = np.arange(stft_mag.shape[1]) * hop_sec

    # Resample bands to target resolution
    target_times = np.arange(0, duration_sec, resolution_sec)
    bands: list[BandSample] = []
    for t in target_times:
        idx = int(min(len(frame_times) - 1, t / hop_sec))
        bands.append(BandSample(
            time_sec=round(float(t), 3),
            bass=round(float(band_curves["bass"][idx]), 4),
            mid=round(float(band_curves["mid"][idx]), 4),
            treble=round(float(band_curves["treble"][idx]), 4),
        ))

    # Normalize bands per band so each peaks at ~1
    if bands:
        for band_name in ("bass", "mid", "treble"):
            vals = [getattr(b, band_name) for b in bands]
            peak = max(vals) or 1.0
            for b in bands:
                setattr(b, band_name, round(float(getattr(b, band_name) / peak), 4))

    # Overall energy curve at the same target resolution
    rms = librosa.feature.rms(y=y, frame_length=n_fft, hop_length=hop_length)[0]
    rms_target: list[float] = []
    for t in target_times:
        idx = int(min(len(rms) - 1, t / hop_sec))
        rms_target.append(float(rms[idx]))
    rms_arr = np.array(rms_target)
    rms_norm = _normalize_curve(rms_arr)

    zones = _merge_runs(target_times, rms_norm, MIN_ZONE_DURATION_SEC)
    zones = _overlay_drops_buildups(zones, beat_map)

    # Transients: onset_detect + spectral_flatness
    onset_frames = librosa.onset.onset_detect(
        y=y, sr=actual_sr, hop_length=hop_length, units="frames"
    )
    onset_times = librosa.frames_to_time(onset_frames, sr=actual_sr, hop_length=hop_length)
    onset_strengths = librosa.onset.onset_strength(
        y=y, sr=actual_sr, hop_length=hop_length
    )
    # Sample onset-strength values at onset frames
    if onset_frames.size and onset_strengths.size:
        oss = onset_strengths[onset_frames]
    else:
        oss = np.array([])
    flatness = librosa.feature.spectral_flatness(y=y, n_fft=n_fft, hop_length=hop_length)[0]
    flatness_times = np.arange(flatness.size) * hop_sec
    transients = _classify_transients(onset_times, oss, flatness, flatness_times)

    return EnergyZonesResult(
        schema_version=1,
        duration_sec=round(duration_sec, 3),
        sample_rate_hz=actual_sr,
        resolution_sec=resolution_sec,
        zones=zones,
        bands=bands,
        transients=transients,
    )


def write_energy_zones(result: EnergyZonesResult, out_path: Path) -> Path:
    """Persist an EnergyZonesResult as energy-zones.json."""
    import json
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": result.schema_version,
        "duration_sec": result.duration_sec,
        "sample_rate_hz": result.sample_rate_hz,
        "resolution_sec": result.resolution_sec,
        "zones": [z.to_dict() for z in result.zones],
        "bands": [b.to_dict() for b in result.bands],
        "transients": [t.to_dict() for t in result.transients],
        "generator": result.generator,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    return out_path


def zone_at(zones: list[EnergyZone], time_sec: float) -> EnergyZone | None:
    """Convenience: find the zone covering a given timestamp."""
    for z in zones:
        if z.start_sec <= time_sec < z.end_sec:
            return z
    return None


def band_at(bands: list[BandSample], time_sec: float) -> BandSample | None:
    """Convenience: find the band sample nearest a given timestamp."""
    if not bands:
        return None
    # Bands are sorted by time_sec; binary-search style isn't needed for
    # typical 4-samples-per-second resolution.
    closest = min(bands, key=lambda b: abs(b.time_sec - time_sec))
    return closest
