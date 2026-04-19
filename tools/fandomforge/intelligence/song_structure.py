"""Song structure analyzer for FandomForge.

Performs deep audio analysis beyond basic BPM detection, producing a complete
structural breakdown suitable for beat-accurate video editing. Outputs tempo,
bar-accurate downbeats, labeled sections (intro/verse/chorus/bridge/outro),
per-beat spectral classification, and key editorial moments (drops and breaths).

Segmentation uses a multi-pass approach:
  1. Foote novelty via self-similarity matrix with a checkerboard kernel to find
     structural boundaries driven by chroma + MFCC change.
  2. RMS energy analysis per section to refine labels and detect drops/breaths.
  3. Section labeling heuristics based on energy rank, position, and duration.

No madmom dependency -- 100% librosa + scipy + numpy.
"""

from __future__ import annotations

import json
import logging
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import librosa
import numpy as np
from scipy import signal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

SectionLabel = Literal["intro", "verse", "pre-chorus", "chorus", "bridge", "breakdown", "outro", "unknown"]
MoodTag = Literal["quiet", "building", "peak", "breakdown"]
EnergyLevel = Literal["low", "mid", "high"]
BeatType = Literal["kick", "snare", "vocal_onset", "sustained", "unknown"]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Beat:
    """Attributes for a single beat position.

    Attributes:
        time: Beat onset time in seconds.
        bar_position: 1-based position within the bar (1-4 for 4/4).
        is_downbeat: True if this beat is beat 1 of a bar.
        beat_type: Spectral classification of what hit on this beat.
        energy: Normalised RMS energy at this beat (0.0 to 1.0).
    """

    time: float
    bar_position: int
    is_downbeat: bool
    beat_type: BeatType
    energy: float


@dataclass
class Section:
    """A labeled structural section of a song.

    Attributes:
        label: Section type tag.
        start_time: Section start in seconds.
        end_time: Section end in seconds.
        duration: Section duration in seconds.
        energy_level: Broad energy category (low/mid/high).
        mood: Editorial mood tag for visual pacing.
        is_drop: True when this section begins a high-energy surge.
        mean_rms: Mean RMS energy (linear) across the section.
        spectral_centroid_mean: Mean spectral centroid in Hz -- proxy for brightness.
        chroma_stability: 0.0 to 1.0; high = tonally stable (often chorus/verse).
    """

    label: SectionLabel
    start_time: float
    end_time: float
    duration: float
    energy_level: EnergyLevel
    mood: MoodTag
    is_drop: bool
    mean_rms: float
    spectral_centroid_mean: float
    chroma_stability: float


@dataclass
class Transition:
    """A notable transition point between two sections.

    Attributes:
        time: Transition timestamp in seconds.
        kind: 'drop' for energy surge, 'breath' for energy collapse.
        energy_delta: Signed change in normalised RMS across the transition window.
        from_section: Label of the section before the transition.
        to_section: Label of the section after the transition.
    """

    time: float
    kind: Literal["drop", "breath"]
    energy_delta: float
    from_section: SectionLabel
    to_section: SectionLabel


@dataclass
class SongStructure:
    """Complete structural analysis of a song.

    Attributes:
        audio_path: Absolute path to the source audio file.
        duration: Total song duration in seconds.
        tempo: Estimated BPM (global median).
        tempo_confidence: 0.0 to 1.0 confidence from the tempogram analysis.
        time_signature: Detected meter (almost always 4).
        beats: All detected beats with per-beat attributes.
        downbeats: Subset of beats that are bar downbeats (beat 1 of each bar).
        sections: Labeled structural sections.
        transitions: Drop and breath transition points.
        drop_moments: Convenience list of timestamps for energy surges.
        breath_moments: Convenience list of timestamps for energy collapses.
    """

    audio_path: str
    duration: float
    tempo: float
    tempo_confidence: float
    time_signature: int
    beats: list[Beat]
    downbeats: list[float]
    sections: list[Section]
    transitions: list[Transition]
    drop_moments: list[float]
    breath_moments: list[float]

    def to_json(self, path: str | Path) -> None:
        """Serialise the full analysis to a JSON file.

        Args:
            path: Destination file path. Parent directories must exist.
        """
        data = asdict(self)
        Path(path).write_text(json.dumps(data, indent=2))
        logger.info("Saved SongStructure to %s", path)

    @classmethod
    def from_json(cls, path: str | Path) -> SongStructure:
        """Load a previously saved SongStructure from JSON.

        Args:
            path: Path to the JSON file produced by ``to_json``.

        Returns:
            Reconstructed SongStructure instance.

        Raises:
            FileNotFoundError: If the JSON file does not exist.
            KeyError: If required fields are missing from the JSON.
        """
        raw = json.loads(Path(path).read_text())
        raw["beats"] = [Beat(**b) for b in raw["beats"]]
        raw["sections"] = [Section(**s) for s in raw["sections"]]
        raw["transitions"] = [Transition(**t) for t in raw["transitions"]]
        return cls(**raw)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Hop length for all feature extraction -- 23ms at 44100Hz, 11ms at 22050Hz
_HOP = 512
_N_FFT = 2048
_SR = 22050  # librosa default resample target


def _suppress_joblib_warnings() -> None:
    """Suppress the joblib physical-core detection warning on Apple Silicon."""
    warnings.filterwarnings(
        "ignore",
        message="Could not find the number of physical cores",
        module="joblib",
    )


def _load_audio(audio_path: str) -> tuple[np.ndarray, int]:
    """Load and resample audio to mono at _SR.

    Args:
        audio_path: Path to any audio format supported by soundfile/audioread.

    Returns:
        Tuple of (waveform array, sample rate).

    Raises:
        FileNotFoundError: If the audio file does not exist.
        RuntimeError: If librosa cannot decode the file.
    """
    p = Path(audio_path)
    if not p.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    logger.info("Loading audio: %s", audio_path)
    y, sr = librosa.load(str(p), sr=_SR, mono=True)
    logger.info("Loaded %.1f seconds at %d Hz", len(y) / sr, sr)
    return y, sr


def _extract_features(
    y: np.ndarray, sr: int
) -> dict[str, np.ndarray]:
    """Compute all feature matrices used downstream.

    Args:
        y: Mono waveform.
        sr: Sample rate.

    Returns:
        Dictionary with keys: chroma, mfcc, rms, spectral_centroid,
        onset_envelope, spectral_contrast.
    """
    logger.debug("Extracting features ...")
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=_HOP, bins_per_octave=36)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13, hop_length=_HOP)
    rms = librosa.feature.rms(y=y, hop_length=_HOP)[0]
    spectral_centroid = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=_HOP)[0]
    spectral_contrast = librosa.feature.spectral_contrast(y=y, sr=sr, hop_length=_HOP)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=_HOP)
    return {
        "chroma": chroma,
        "mfcc": mfcc,
        "rms": rms,
        "spectral_centroid": spectral_centroid,
        "spectral_contrast": spectral_contrast,
        "onset_envelope": onset_env,
    }


def _estimate_tempo(
    y: np.ndarray, sr: int, onset_env: np.ndarray
) -> tuple[float, float, int]:
    """Estimate global tempo and meter from beat tracking + tempogram.

    Uses librosa's dynamic programming beat tracker as the primary estimator,
    then validates the result against the tempogram autocorrelation peak to
    produce a confidence score.

    Args:
        y: Mono waveform.
        sr: Sample rate.
        onset_env: Pre-computed onset strength envelope.

    Returns:
        Tuple of (bpm, confidence 0-1, time_signature).
    """
    tempo_raw, _ = librosa.beat.beat_track(
        onset_envelope=onset_env, sr=sr, hop_length=_HOP, trim=False
    )
    bpm = float(np.atleast_1d(tempo_raw)[0])

    # Confidence: check how strong the tempogram peak is relative to noise
    tempogram = librosa.feature.tempogram(
        onset_envelope=onset_env, sr=sr, hop_length=_HOP, win_length=384
    )
    tempo_freqs = librosa.tempo_frequencies(tempogram.shape[0], sr=sr, hop_length=_HOP)
    mean_tgram = tempogram.mean(axis=1)
    peak_idx = np.argmin(np.abs(tempo_freqs - bpm))
    peak_val = float(mean_tgram[peak_idx])
    noise_val = float(np.percentile(mean_tgram, 90))
    confidence = float(np.clip(peak_val / (noise_val + 1e-8), 0.0, 1.0))

    # Time signature: almost always 4/4; detect 3/4 via autocorrelation spacing
    time_sig = 4
    ac = librosa.autocorrelate(onset_env, max_size=int(sr * 4 / _HOP))
    beat_period = int(round(60.0 * sr / (bpm * _HOP)))
    if beat_period > 0:
        # Compare energy at 3x vs 4x beat period
        idx3 = beat_period * 3
        idx4 = beat_period * 4
        if idx3 < len(ac) and idx4 < len(ac):
            if ac[idx3] > ac[idx4] * 1.15:
                time_sig = 3

    return bpm, confidence, time_sig


def _track_beats(
    y: np.ndarray, sr: int, onset_env: np.ndarray, bpm: float, time_sig: int
) -> tuple[np.ndarray, np.ndarray]:
    """Track beat positions and derive bar-level downbeats.

    Downbeats are estimated by grouping beats into bars of ``time_sig`` beats.
    The phase of the bar grid is determined by finding the grouped beat index
    that maximises cumulative onset energy (so bar-1 tends to land on strong
    structural accents rather than upbeats).

    Args:
        y: Mono waveform.
        sr: Sample rate.
        onset_env: Pre-computed onset strength envelope.
        bpm: Global tempo estimate.
        time_sig: Bar length in beats.

    Returns:
        Tuple of (beat_frames array, downbeat_frames array).
    """
    _, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_env, sr=sr, hop_length=_HOP, bpm=bpm, trim=False
    )

    if len(beat_frames) == 0:
        return beat_frames, np.array([], dtype=int)

    # Find bar phase that maximises downbeat strength
    n_beats = len(beat_frames)
    best_phase = 0
    best_score = -np.inf
    for phase in range(time_sig):
        idxs = list(range(phase, n_beats, time_sig))
        frames = beat_frames[idxs]
        # Clamp to valid onset envelope indices
        valid = frames[frames < len(onset_env)]
        score = float(onset_env[valid].sum()) if len(valid) > 0 else 0.0
        if score > best_score:
            best_score = score
            best_phase = phase

    downbeat_idxs = list(range(best_phase, n_beats, time_sig))
    downbeat_frames = beat_frames[downbeat_idxs]

    return beat_frames, downbeat_frames


def _foote_novelty(
    chroma: np.ndarray,
    mfcc: np.ndarray,
    kernel_size: int = 48,
) -> np.ndarray:
    """Compute a Foote novelty curve using a checkerboard kernel on the SSM.

    Combines chroma and MFCC into a single normalised feature matrix, builds a
    cosine self-similarity matrix, then convolves a 2-D checkerboard kernel
    along the diagonal to highlight structural boundaries.

    Args:
        chroma: Chroma feature matrix (12 x T).
        mfcc: MFCC matrix (n_mfcc x T).
        kernel_size: Side length of the checkerboard kernel in frames. Larger
            values detect coarser structure (song sections); smaller values
            detect finer structure (phrase boundaries).

    Returns:
        1-D novelty curve of length T, values in [0, 1].
    """
    feat = np.vstack([
        librosa.util.normalize(chroma, axis=0),
        librosa.util.normalize(mfcc, axis=0),
    ])
    norms = np.linalg.norm(feat, axis=0, keepdims=True) + 1e-8
    feat_n = feat / norms
    S = feat_n.T @ feat_n  # T x T cosine SSM

    half = kernel_size // 2
    checker = np.ones((kernel_size, kernel_size))
    checker[:half, half:] = -1.0
    checker[half:, :half] = -1.0

    conv = signal.fftconvolve(S, checker, mode="same")
    novelty = np.diag(conv).copy()

    # Normalise to [0, 1]
    novelty -= novelty.min()
    denom = novelty.max()
    if denom > 1e-8:
        novelty /= denom
    return novelty


def _find_section_boundaries(
    novelty: np.ndarray,
    rms: np.ndarray,
    sr: int,
    duration: float,
    min_section_sec: float = 8.0,
    max_sections: int = 14,
) -> np.ndarray:
    """Find section boundary times by peaking the Foote novelty curve.

    A secondary RMS-change guard is applied: candidate peaks near a flat
    energy region are downweighted, reducing false positives in long sustained
    sections.

    Args:
        novelty: Normalised Foote novelty curve (length T).
        rms: RMS energy curve (length T).
        sr: Sample rate.
        duration: Total audio duration in seconds.
        min_section_sec: Minimum gap between consecutive boundaries.
        max_sections: Upper cap on the number of sections returned.

    Returns:
        Sorted array of boundary times in seconds (includes 0.0 and duration).
    """
    min_dist = max(1, int(sr / _HOP * min_section_sec))

    # Blend novelty with a smoothed RMS derivative so flat regions get less weight
    rms_smooth = np.convolve(rms, np.ones(8) / 8, mode="same")
    rms_change = np.abs(np.gradient(rms_smooth))
    rms_change_n = rms_change / (rms_change.max() + 1e-8)
    blended = 0.7 * novelty + 0.3 * rms_change_n

    peaks, props = signal.find_peaks(
        blended,
        distance=min_dist,
        height=np.percentile(blended, 55),
        prominence=0.05,
    )

    # Sort by prominence and keep top N
    if len(peaks) > max_sections:
        proms = props["prominences"]
        sorted_idxs = np.argsort(proms)[::-1][:max_sections]
        peaks = np.sort(peaks[sorted_idxs])

    peak_times = librosa.frames_to_time(peaks, sr=sr, hop_length=_HOP)

    # Always include song start and end
    boundary_times = np.unique(np.concatenate([[0.0], peak_times, [duration]]))
    return boundary_times


def _section_features(
    y: np.ndarray,
    sr: int,
    start_time: float,
    end_time: float,
    features: dict[str, np.ndarray],
) -> dict[str, float]:
    """Compute aggregate features for a section time range.

    Args:
        y: Full mono waveform.
        sr: Sample rate.
        start_time: Section start in seconds.
        end_time: Section end in seconds.
        features: Pre-computed feature dict from ``_extract_features``.

    Returns:
        Dict with keys: mean_rms, spectral_centroid_mean, chroma_stability.
    """
    start_frame = librosa.time_to_frames(start_time, sr=sr, hop_length=_HOP)
    end_frame = librosa.time_to_frames(end_time, sr=sr, hop_length=_HOP)

    rms_seg = features["rms"][start_frame:end_frame]
    cent_seg = features["spectral_centroid"][start_frame:end_frame]
    chroma_seg = features["chroma"][:, start_frame:end_frame]

    mean_rms = float(rms_seg.mean()) if len(rms_seg) > 0 else 0.0
    centroid_mean = float(cent_seg.mean()) if len(cent_seg) > 0 else 0.0

    # Chroma stability: mean cosine similarity between adjacent frames
    if chroma_seg.shape[1] > 1:
        norms = np.linalg.norm(chroma_seg, axis=0, keepdims=True) + 1e-8
        chroma_n = chroma_seg / norms
        dot = (chroma_n[:, :-1] * chroma_n[:, 1:]).sum(axis=0)
        stability = float(dot.mean())
    else:
        stability = 0.0

    return {
        "mean_rms": mean_rms,
        "spectral_centroid_mean": centroid_mean,
        "chroma_stability": float(np.clip(stability, 0.0, 1.0)),
    }


def _classify_sections(
    sections_raw: list[dict],
    duration: float,
    time_sig: int,
) -> list[Section]:
    """Assign label, energy_level, mood, and is_drop to each section.

    Labeling strategy:
    - Rank sections by mean_rms. Top 25% are 'high', bottom 25% are 'low'.
    - First section below 15% of duration and low energy = intro.
    - Last section below 15% of duration and low-mid energy = outro.
    - Highest-energy section(s) = chorus.
    - Very short mid-energy section following chorus = bridge or breakdown.
    - Remaining mid/low sections alternating from top = verse.
    - Pre-chorus is inferred between a verse and chorus when the section energy
      is between them and duration is short.

    Args:
        sections_raw: List of dicts with keys start_time, end_time, duration,
            mean_rms, spectral_centroid_mean, chroma_stability.
        duration: Total song duration in seconds.
        time_sig: Meter (for future use in beat-aligned label snapping).

    Returns:
        List of Section dataclass instances.
    """
    if not sections_raw:
        return []

    rms_values = np.array([s["mean_rms"] for s in sections_raw])
    rms_min = rms_values.min()
    rms_max = rms_values.max()
    rms_range = rms_max - rms_min + 1e-8

    def normalise_rms(v: float) -> float:
        return (v - rms_min) / rms_range

    # Energy thresholds (relative to the song's own dynamic range)
    high_thresh = 0.65
    low_thresh = 0.30

    # First pass: assign energy_level
    for s in sections_raw:
        nr = normalise_rms(s["mean_rms"])
        if nr >= high_thresh:
            s["energy_level"] = "high"
        elif nr <= low_thresh:
            s["energy_level"] = "low"
        else:
            s["energy_level"] = "mid"
        s["normalised_rms"] = nr

    n = len(sections_raw)
    intro_max_time = 0.15 * duration
    outro_min_time = 0.82 * duration

    # Assign provisional labels
    labels: list[SectionLabel] = ["unknown"] * n
    chorus_count = 0

    for i, s in enumerate(sections_raw):
        t_start = s["start_time"]
        t_end = s["end_time"]
        nr = s["normalised_rms"]
        el = s["energy_level"]

        if i == 0 and t_end <= intro_max_time and el in ("low", "mid"):
            labels[i] = "intro"
            continue

        if i == n - 1 and t_start >= outro_min_time and el in ("low", "mid"):
            labels[i] = "outro"
            continue

        if el == "high":
            labels[i] = "chorus"
            chorus_count += 1
            continue

        if el == "low":
            if s["duration"] < 12.0:
                labels[i] = "breakdown"
            else:
                labels[i] = "verse"
            continue

        # mid energy: decide verse, pre-chorus, or bridge based on position
        # relative to neighbouring sections
        prev_label = labels[i - 1] if i > 0 else "unknown"
        next_energy = sections_raw[i + 1]["energy_level"] if i < n - 1 else "low"

        if next_energy == "high" and s["duration"] < 20.0:
            labels[i] = "pre-chorus"
        elif prev_label == "chorus" and s["duration"] < 20.0:
            labels[i] = "bridge"
        else:
            labels[i] = "verse"

    # Detect is_drop: section whose energy is significantly higher than previous
    is_drop_flags = [False] * n
    for i in range(1, n):
        prev_nr = sections_raw[i - 1]["normalised_rms"]
        curr_nr = sections_raw[i]["normalised_rms"]
        if curr_nr - prev_nr >= 0.25 and sections_raw[i]["energy_level"] == "high":
            is_drop_flags[i] = True

    # Build mood tags
    def mood_from(label: SectionLabel, el: EnergyLevel) -> MoodTag:
        if label in ("chorus",) or el == "high":
            return "peak"
        if label in ("pre-chorus",) or el == "mid":
            return "building"
        if label in ("breakdown",):
            return "breakdown"
        return "quiet"

    results: list[Section] = []
    for i, s in enumerate(sections_raw):
        el = s["energy_level"]
        label = labels[i]
        results.append(Section(
            label=label,
            start_time=round(s["start_time"], 4),
            end_time=round(s["end_time"], 4),
            duration=round(s["duration"], 4),
            energy_level=el,
            mood=mood_from(label, el),
            is_drop=is_drop_flags[i],
            mean_rms=round(s["mean_rms"], 6),
            spectral_centroid_mean=round(s["spectral_centroid_mean"], 2),
            chroma_stability=round(s["chroma_stability"], 4),
        ))
    return results


def _classify_beat(
    y: np.ndarray,
    sr: int,
    beat_time: float,
) -> tuple[BeatType, float]:
    """Classify a beat as kick, snare, vocal_onset, or sustained.

    Uses a short FFT window starting at the beat onset to compare energy
    distribution across sub-bass, bass, and mid frequency bands combined with
    spectral centroid and zero-crossing rate.

    Args:
        y: Full mono waveform.
        sr: Sample rate.
        beat_time: Beat onset time in seconds.

    Returns:
        Tuple of (beat_type, normalised_energy).
    """
    start = int(beat_time * sr)
    end = min(start + _N_FFT, len(y))
    seg = y[start:end]

    if len(seg) < 128:
        return "unknown", 0.0

    spec = np.abs(np.fft.rfft(seg, n=_N_FFT))
    freqs = np.fft.rfftfreq(_N_FFT, d=1.0 / sr)
    total = spec.sum() + 1e-8

    sub_ratio = spec[freqs < 120].sum() / total
    low_mid_ratio = spec[(freqs >= 120) & (freqs < 2000)].sum() / total
    centroid = float((freqs * spec).sum() / total)

    # Zero-crossing rate as transient roughness proxy
    signs = np.sign(seg)
    zcr = float(np.mean(np.abs(np.diff(signs)) > 0))

    # RMS energy of this segment
    energy = float(np.sqrt(np.mean(seg ** 2)))

    if centroid < 250 and sub_ratio > 0.25:
        beat_type: BeatType = "kick"
    elif 200 <= centroid < 1500 and low_mid_ratio > 0.45 and zcr < 0.18:
        beat_type = "snare"
    elif centroid > 500 and zcr > 0.12:
        beat_type = "vocal_onset"
    else:
        beat_type = "sustained"

    return beat_type, energy


def _build_beats(
    y: np.ndarray,
    sr: int,
    beat_frames: np.ndarray,
    downbeat_frames: np.ndarray,
    time_sig: int,
    features: dict[str, np.ndarray],
) -> list[Beat]:
    """Build the full Beat list with per-beat attributes.

    Args:
        y: Mono waveform.
        sr: Sample rate.
        beat_frames: All beat frame positions.
        downbeat_frames: Subset that are bar downbeats.
        time_sig: Beats per bar.
        features: Pre-computed feature dict.

    Returns:
        List of Beat instances.
    """
    downbeat_set = set(int(f) for f in downbeat_frames)
    beat_times_arr = librosa.frames_to_time(beat_frames, sr=sr, hop_length=_HOP)

    rms = features["rms"]
    rms_max = rms.max() + 1e-8

    beats: list[Beat] = []
    bar_pos = 1  # 1-based counter, resets every time_sig beats
    last_downbeat_idx = -1

    for idx, (frame, t) in enumerate(zip(beat_frames, beat_times_arr)):
        is_db = int(frame) in downbeat_set
        if is_db:
            bar_pos = 1
            last_downbeat_idx = idx
        else:
            bar_pos = ((idx - last_downbeat_idx) % time_sig) + 1

        beat_type, _ = _classify_beat(y, sr, float(t))
        rms_frame = min(int(frame), len(rms) - 1)
        energy = float(rms[rms_frame] / rms_max)

        beats.append(Beat(
            time=round(float(t), 4),
            bar_position=bar_pos,
            is_downbeat=is_db,
            beat_type=beat_type,
            energy=round(energy, 4),
        ))

    return beats


def _detect_transitions(
    y: np.ndarray,
    sr: int,
    sections: list[Section],
    features: dict[str, np.ndarray],
) -> tuple[list[Transition], list[float], list[float]]:
    """Identify drop and breath transitions at section boundaries.

    A 'drop' is a boundary where energy increases by >= 0.20 normalised RMS.
    A 'breath' is a boundary where energy decreases by >= 0.15 normalised RMS.

    Args:
        y: Mono waveform.
        sr: Sample rate.
        sections: Classified Section list.
        features: Pre-computed features.

    Returns:
        Tuple of (transitions list, drop_times list, breath_times list).
    """
    if len(sections) < 2:
        return [], [], []

    rms_values = np.array([s.mean_rms for s in sections])
    rms_min = rms_values.min()
    rms_max = rms_values.max()
    rms_range = rms_max - rms_min + 1e-8

    def nr(v: float) -> float:
        return (v - rms_min) / rms_range

    transitions: list[Transition] = []
    drop_times: list[float] = []
    breath_times: list[float] = []

    for i in range(1, len(sections)):
        prev = sections[i - 1]
        curr = sections[i]
        delta = nr(curr.mean_rms) - nr(prev.mean_rms)
        boundary_time = curr.start_time

        if delta >= 0.20:
            transitions.append(Transition(
                time=round(boundary_time, 4),
                kind="drop",
                energy_delta=round(float(delta), 4),
                from_section=prev.label,
                to_section=curr.label,
            ))
            drop_times.append(round(boundary_time, 4))
        elif delta <= -0.15:
            transitions.append(Transition(
                time=round(boundary_time, 4),
                kind="breath",
                energy_delta=round(float(delta), 4),
                from_section=prev.label,
                to_section=curr.label,
            ))
            breath_times.append(round(boundary_time, 4))

    return transitions, drop_times, breath_times


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze(audio_path: str) -> SongStructure:
    """Analyse a song and return its complete structural breakdown.

    This is the main entry point. Performs the following pipeline:

    1. Load audio at 22050 Hz mono.
    2. Extract chroma, MFCC, RMS, spectral centroid, and onset strength.
    3. Estimate tempo + time signature from the tempogram.
    4. Track beats and derive bar-level downbeats.
    5. Compute Foote novelty curve via checkerboard kernel on the SSM.
    6. Find section boundaries by peaking the novelty curve.
    7. Compute per-section aggregate features.
    8. Label sections (intro/verse/chorus/etc.) by energy rank and position.
    9. Classify each beat as kick/snare/vocal_onset/sustained.
    10. Identify drop and breath transitions at section boundaries.

    Args:
        audio_path: Path to any audio file format supported by librosa
            (mp3, wav, flac, ogg, aiff, etc.).

    Returns:
        SongStructure dataclass with all analysis results.

    Raises:
        FileNotFoundError: If the audio file does not exist.
        RuntimeError: If audio cannot be decoded.
    """
    _suppress_joblib_warnings()

    audio_path = str(Path(audio_path).resolve())
    logger.info("Starting song structure analysis: %s", audio_path)

    # 1. Load
    y, sr = _load_audio(audio_path)
    duration = float(len(y) / sr)

    # 2. Feature extraction
    features = _extract_features(y, sr)

    # 3. Tempo
    logger.info("Estimating tempo ...")
    tempo, confidence, time_sig = _estimate_tempo(y, sr, features["onset_envelope"])
    logger.info("Tempo=%.1f BPM  confidence=%.2f  meter=%d/4", tempo, confidence, time_sig)

    # 4. Beat tracking
    logger.info("Tracking beats ...")
    beat_frames, downbeat_frames = _track_beats(
        y, sr, features["onset_envelope"], tempo, time_sig
    )
    downbeat_times = librosa.frames_to_time(
        downbeat_frames, sr=sr, hop_length=_HOP
    ).tolist()
    logger.info("Found %d beats, %d downbeats", len(beat_frames), len(downbeat_frames))

    # 5. Foote novelty
    logger.info("Computing Foote novelty curve ...")
    # Use smaller kernel for shorter songs so we don't miss fine structure
    kernel_size = 64 if duration > 180 else 48
    novelty = _foote_novelty(features["chroma"], features["mfcc"], kernel_size=kernel_size)

    # 6. Section boundaries
    logger.info("Finding section boundaries ...")
    boundary_times = _find_section_boundaries(
        novelty, features["rms"], sr, duration,
        min_section_sec=8.0,
        max_sections=14,
    )
    logger.info("Found %d sections", len(boundary_times) - 1)

    # 7. Per-section features
    sections_raw: list[dict] = []
    for i in range(len(boundary_times) - 1):
        t0 = float(boundary_times[i])
        t1 = float(boundary_times[i + 1])
        sf = _section_features(y, sr, t0, t1, features)
        sections_raw.append({
            "start_time": t0,
            "end_time": t1,
            "duration": t1 - t0,
            **sf,
        })

    # 8. Section classification
    logger.info("Classifying sections ...")
    sections = _classify_sections(sections_raw, duration, time_sig)

    # 9. Per-beat classification
    logger.info("Classifying beats ...")
    beats = _build_beats(y, sr, beat_frames, downbeat_frames, time_sig, features)

    # 10. Transitions
    logger.info("Detecting transitions ...")
    transitions, drop_moments, breath_moments = _detect_transitions(
        y, sr, sections, features
    )

    logger.info(
        "Analysis complete: %d sections, %d drops, %d breaths",
        len(sections), len(drop_moments), len(breath_moments),
    )

    return SongStructure(
        audio_path=audio_path,
        duration=round(duration, 4),
        tempo=round(tempo, 4),
        tempo_confidence=round(confidence, 4),
        time_signature=time_sig,
        beats=beats,
        downbeats=[round(float(t), 4) for t in downbeat_times],
        sections=sections,
        transitions=transitions,
        drop_moments=drop_moments,
        breath_moments=breath_moments,
    )


# ---------------------------------------------------------------------------
# Human-readable printer
# ---------------------------------------------------------------------------

def print_structure(ss: SongStructure) -> None:
    """Print a readable song structure breakdown to stdout.

    Args:
        ss: SongStructure instance from ``analyze()``.
    """
    def fmt_time(t: float) -> str:
        m = int(t) // 60
        s = t - m * 60
        return f"{m}:{s:05.2f}"

    bar = "=" * 70
    thin = "-" * 70

    print(bar)
    print(f"  SONG STRUCTURE ANALYSIS")
    print(f"  {ss.audio_path}")
    print(bar)
    print(f"  Duration        : {fmt_time(ss.duration)}  ({ss.duration:.1f}s)")
    print(f"  Tempo           : {ss.tempo:.1f} BPM  (confidence {ss.tempo_confidence:.0%})")
    print(f"  Time signature  : {ss.time_signature}/4")
    print(f"  Beats detected  : {len(ss.beats)}")
    print(f"  Downbeats       : {len(ss.downbeats)}")
    print(f"  Sections        : {len(ss.sections)}")
    print(f"  Drop moments    : {len(ss.drop_moments)}")
    print(f"  Breath moments  : {len(ss.breath_moments)}")
    print()

    # Sections table
    print(thin)
    print(f"  {'#':<3} {'LABEL':<12} {'START':>6}  {'END':>6}  {'DUR':>5}  {'ENERGY':<6} {'MOOD':<10} {'DROP?'}")
    print(thin)
    for i, sec in enumerate(ss.sections):
        drop_flag = "  << DROP" if sec.is_drop else ""
        print(
            f"  {i+1:<3} {sec.label:<12} {fmt_time(sec.start_time):>6}  "
            f"{fmt_time(sec.end_time):>6}  {sec.duration:>5.1f}s  "
            f"{sec.energy_level:<6} {sec.mood:<10}{drop_flag}"
        )
    print()

    # Transitions
    if ss.transitions:
        print(thin)
        print("  TRANSITIONS")
        print(thin)
        for tr in ss.transitions:
            arrow = ">> DROP" if tr.kind == "drop" else "<< BREATH"
            print(
                f"  {fmt_time(tr.time):>6}  {arrow:<10}  "
                f"{tr.from_section} -> {tr.to_section}  "
                f"(delta {tr.energy_delta:+.2f})"
            )
        print()

    # Drop moments (editorial hit list)
    if ss.drop_moments:
        print(thin)
        print("  DROP MOMENTS  (land big visual hits here)")
        print(thin)
        for t in ss.drop_moments:
            print(f"  {fmt_time(t):>6}  ({t:.2f}s)")
        print()

    # Breath moments
    if ss.breath_moments:
        print(thin)
        print("  BREATH MOMENTS  (use for slow cuts / emotion beats)")
        print(thin)
        for t in ss.breath_moments:
            print(f"  {fmt_time(t):>6}  ({t:.2f}s)")
        print()

    # Downbeat sample (first 32)
    print(thin)
    print("  DOWNBEATS (first 32 bar-1 positions, seconds)")
    print(thin)
    sample = ss.downbeats[:32]
    row = "  " + "  ".join(f"{t:6.2f}" for t in sample)
    print(row)
    if len(ss.downbeats) > 32:
        print(f"  ... {len(ss.downbeats) - 32} more")
    print()

    # Beat type summary
    type_counts: dict[str, int] = {}
    for b in ss.beats:
        type_counts[b.beat_type] = type_counts.get(b.beat_type, 0) + 1
    print(thin)
    print("  BEAT TYPE BREAKDOWN")
    print(thin)
    for btype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        pct = count / len(ss.beats) * 100
        print(f"  {btype:<16} {count:4d}  ({pct:.1f}%)")
    print()
    print(bar)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    if len(sys.argv) < 2:
        # Default to the test track when run without arguments
        default_path = (
            "/Users/damato/Video Project/projects/leon-badass-monologue/raw/"
            "in-the-end-tommee.mp3"
        )
        audio_path = default_path
    else:
        audio_path = sys.argv[1]

    structure = analyze(audio_path)
    print_structure(structure)

    # Optionally save JSON alongside the audio
    audio_p = Path(audio_path)
    json_path = audio_p.with_suffix(".song_structure.json")
    structure.to_json(json_path)
    print(f"\nJSON saved to: {json_path}")
