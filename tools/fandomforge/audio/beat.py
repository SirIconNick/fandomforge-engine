"""Beat and tempo detection using librosa."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import librosa
import numpy as np


@dataclass
class BeatMap:
    """Structured beat analysis result for a single audio file."""

    song: str
    artist: str
    duration_sec: float
    bpm: float
    bpm_confidence: float
    time_signature: str
    beats: list[float] = field(default_factory=list)
    downbeats: list[float] = field(default_factory=list)
    onsets: list[float] = field(default_factory=list)
    downbeat_source: str = "librosa-heuristic"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _estimate_downbeats_heuristic(
    beats: np.ndarray,
    beats_per_bar: int = 4,
) -> list[float]:
    """Pick every Nth beat as a downbeat, starting from the first.

    Fallback used when madmom is unavailable. True downbeat detection (beat 1
    of each bar) requires madmom's DBN processor, which we try first.
    """
    if len(beats) == 0:
        return []
    return [float(b) for b in beats[::beats_per_bar]]


def _patch_for_madmom() -> None:
    """madmom 0.16.x targets numpy < 1.20 and Python < 3.10. Alias removed
    names back so its imports don't explode.
    """
    import collections
    import collections.abc
    for name in (
        "MutableSequence",
        "MutableMapping",
        "Iterable",
        "Mapping",
        "Sequence",
        "Callable",
    ):
        if not hasattr(collections, name):
            setattr(collections, name, getattr(collections.abc, name))
    # numpy 1.20+ removed these aliases.
    for np_name, py_type in (("float", float), ("int", int), ("bool", bool), ("object", object)):
        if not hasattr(np, np_name):
            setattr(np, np_name, py_type)


def _madmom_downbeats(
    audio_path: Path,
    *,
    beats_per_bar: int = 4,
) -> tuple[list[float], list[float]] | None:
    """Extract beats and downbeats using madmom's RNN downbeat activations.

    madmom's DBNDownBeatTrackingProcessor crashes under numpy 2.x (inhomogeneous
    array issue). We use the RNN activations directly and pick peaks ourselves —
    same neural net, simpler post-processor. Returns (beats_sec, downbeats_sec)
    or None if madmom is unavailable or the analysis produced no output.

    The RNN emits shape (num_frames, 2) at 100 fps, where col 0 is P(beat) and
    col 1 is P(downbeat). Downbeats are a subset of beats; we enforce that by
    snapping every downbeat time to its nearest detected beat.
    """
    _patch_for_madmom()
    try:
        from madmom.features.downbeats import RNNDownBeatProcessor  # type: ignore
    except (ImportError, AttributeError):
        return None

    try:
        act = RNNDownBeatProcessor()(str(audio_path))
    except Exception:
        return None

    if act is None or getattr(act, "size", 0) == 0:
        return None

    import numpy as _np

    arr = _np.asarray(act, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 2:
        return None

    fps = 100.0
    beat_probs = arr[:, 0]
    db_probs = arr[:, 1]

    def _pick_peaks(probs: _np.ndarray, threshold: float, min_gap_frames: int) -> list[int]:
        picks: list[int] = []
        last = -min_gap_frames
        for i in range(1, len(probs) - 1):
            if (
                probs[i] >= threshold
                and probs[i] >= probs[i - 1]
                and probs[i] >= probs[i + 1]
                and i - last >= min_gap_frames
            ):
                picks.append(i)
                last = i
        return picks

    # Typical beat gap at 60-200 BPM is 30-100 frames at 100 fps. Use a floor
    # of 25 frames to protect against triplet/sixteenth doubling.
    beat_idx = _pick_peaks(beat_probs, threshold=0.4, min_gap_frames=25)
    if not beat_idx:
        return None

    beats = [float(i) / fps for i in beat_idx]

    # Downbeats: pick peaks in db_probs but snap each to the nearest beat so
    # they're always a proper subset.
    db_candidates = _pick_peaks(
        db_probs,
        threshold=0.3,
        min_gap_frames=60,  # downbeats ~1/bar => >= ~1 sec apart
    )
    downbeats: list[float] = []
    for cand in db_candidates:
        cand_sec = cand / fps
        nearest = min(beats, key=lambda b: abs(b - cand_sec))
        if abs(nearest - cand_sec) < 0.12 and nearest not in downbeats:
            downbeats.append(nearest)

    # Fallback: if we found beats but no confident downbeats, take every Nth.
    if not downbeats and beats:
        downbeats = beats[::beats_per_bar]

    downbeats.sort()
    return beats, downbeats


def _confidence_from_onset(onset_env: np.ndarray, tempo: float) -> float:
    """Rough confidence score for the detected tempo.

    We autocorrelate the onset envelope and check how strong the peak at the
    expected period is vs. the surrounding signal. Returns a value in [0, 1].
    """
    if len(onset_env) < 10:
        return 0.0
    # Autocorrelate
    ac = librosa.autocorrelate(onset_env, max_size=len(onset_env) // 2)
    if ac.size == 0 or ac.max() <= 0:
        return 0.0
    # Normalize
    ac_norm = ac / ac.max()
    # Find peak strength relative to median
    median = float(np.median(np.abs(ac_norm)))
    peak = float(ac_norm.max())
    if median <= 0:
        return min(1.0, peak)
    ratio = (peak - median) / max(peak, 1e-6)
    return float(np.clip(ratio, 0.0, 1.0))


def analyze_beats(
    audio_path: str | Path,
    *,
    song_name: str | None = None,
    artist: str | None = None,
    tempo_hint: float | None = None,
    tightness: int = 100,
    beats_per_bar: int = 4,
    sr: int = 22050,
    prefer_madmom: bool = True,
) -> BeatMap:
    """Run full beat analysis on an audio file.

    Args:
        audio_path: Path to audio file (any format ffmpeg can read)
        song_name: Optional display name; defaults to file stem
        artist: Optional artist name
        tempo_hint: Optional BPM hint to constrain the search
        tightness: Tightness parameter for librosa beat tracking
        beats_per_bar: Beats per bar (4 for 4/4)
        sr: Sample rate to load at (22050 is fine for beat detection)
        prefer_madmom: Use madmom's DBN downbeat tracker when available
            (tighter downbeats). Falls back to librosa + every-Nth-beat
            heuristic if madmom is missing or fails.

    Returns:
        BeatMap with tempo, beats, downbeats, onsets, and downbeat_source.
    """
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")

    # Load audio for librosa-based tempo + confidence signals.
    y, actual_sr = librosa.load(str(path), sr=sr, mono=True)
    duration = librosa.get_duration(y=y, sr=actual_sr)

    onset_env = librosa.onset.onset_strength(y=y, sr=actual_sr)
    start_bpm = tempo_hint if tempo_hint is not None else 120.0
    tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_env,
        sr=actual_sr,
        start_bpm=start_bpm,
        tightness=tightness,
    )
    tempo_val: float = float(np.atleast_1d(tempo)[0])
    librosa_beats = librosa.frames_to_time(beat_frames, sr=actual_sr)

    onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=actual_sr)
    onsets = [float(t) for t in librosa.frames_to_time(onset_frames, sr=actual_sr)]
    confidence = _confidence_from_onset(onset_env, tempo_val)

    # Downbeats: try madmom DBN first; fall back to the every-Nth heuristic.
    downbeats: list[float]
    beats: list[float]
    downbeat_source: str
    madmom_result = _madmom_downbeats(path, beats_per_bar=beats_per_bar) if prefer_madmom else None
    if madmom_result is not None:
        mm_beats, mm_downbeats = madmom_result
        beats = mm_beats
        downbeats = mm_downbeats
        downbeat_source = "madmom-rnn"
    else:
        beats = [float(t) for t in librosa_beats]
        downbeats = _estimate_downbeats_heuristic(librosa_beats, beats_per_bar=beats_per_bar)
        downbeat_source = "librosa-heuristic"

    return BeatMap(
        song=song_name or path.stem,
        artist=artist or "Unknown",
        duration_sec=float(duration),
        bpm=round(tempo_val, 2),
        bpm_confidence=round(confidence, 3),
        time_signature=f"{beats_per_bar}/4",
        beats=beats,
        downbeats=downbeats,
        onsets=onsets,
        downbeat_source=downbeat_source,
    )
