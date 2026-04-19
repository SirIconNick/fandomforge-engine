"""Analyze a reference tribute/multifandom video to extract its editing style.

For each reference video we measure:
- Tempo, beat grid
- Scene boundaries (cuts)
- Cut-to-beat alignment (what fraction land on beats, downbeats, half-beats)
- Shot duration distribution per section (verse/chorus/bridge)
- Voice-over presence timeline (is there speech over music?)
- Color LAB means per section
- Opening style (black, cold, title, fade-in)
- Ending style (black, fade, hard cut)
- Overall energy curve (song RMS)

Output: one JSON style profile per video. Aggregate profiles become the
"template" our shot planner uses.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class CutAlignment:
    time_sec: float
    shot_duration_sec: float
    nearest_beat_sec: float
    offset_from_beat_sec: float
    aligned_to: str  # "beat", "downbeat", "half-beat", "off-grid"


@dataclass
class VOWindow:
    start_sec: float
    end_sec: float
    intensity: float  # 0-1, how strongly voice dominates


@dataclass
class StyleProfile:
    video_path: str
    duration_sec: float
    tempo_bpm: float
    beat_spacing_sec: float

    # Cuts
    num_cuts: int = 0
    cuts_per_second: float = 0.0
    cuts_aligned: dict[str, int] = field(default_factory=dict)
    shot_duration_stats: dict[str, float] = field(default_factory=dict)

    # VO
    vo_windows: list[dict] = field(default_factory=list)
    vo_coverage_pct: float = 0.0

    # Energy
    energy_curve_0_5s: list[float] = field(default_factory=list)
    peak_energy_sec: float = 0.0
    opening_first_3s_energy: float = 0.0

    # Color
    lab_means_by_section: list[dict] = field(default_factory=list)
    color_saturation_avg: float = 0.0

    # Opening/ending
    opening_black_sec: float = 0.0
    ending_black_sec: float = 0.0
    opens_on_beat: bool = False
    ends_on_beat: bool = False


def _probe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _extract_audio(video: Path, out_wav: Path) -> bool:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(video), "-ac", "1", "-ar", "22050", str(out_wav)],
        stderr=subprocess.DEVNULL, timeout=300,
    )
    return r.returncode == 0 and out_wav.exists()


def _detect_scenes(video: Path) -> list[tuple[float, float]]:
    """Return list of (start_sec, end_sec) for each scene."""
    from scenedetect import detect, ContentDetector
    scenes = detect(str(video), ContentDetector(threshold=30.0, min_scene_len=12))
    return [(s[0].get_seconds(), s[1].get_seconds()) for s in scenes]


def _beat_analysis(wav_path: Path) -> tuple[float, list[float]]:
    import librosa
    import numpy as np
    y, sr = librosa.load(str(wav_path), sr=22050)
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    tempo = float(np.atleast_1d(tempo)[0])
    return tempo, [float(b) for b in beats]


def _energy_curve(wav_path: Path, window_sec: float = 0.5) -> list[float]:
    import librosa
    import numpy as np
    y, sr = librosa.load(str(wav_path), sr=22050)
    hop = int(sr * window_sec)
    return [
        float(np.sqrt(np.mean(y[i:i + hop] ** 2)))
        for i in range(0, len(y) - hop, hop)
    ]


def _detect_vo_windows(wav_path: Path) -> list[VOWindow]:
    """Crude VO detection: sliding spectral ratio between voice band (200-4k Hz)
    and full band. When voice-band dominates by > threshold for >1s, count it.

    This is a heuristic, not perfect. Aim: measure what % of the edit has a
    speech component above music.
    """
    import librosa
    import numpy as np

    y, sr = librosa.load(str(wav_path), sr=22050, mono=True)
    frame_sec = 0.25
    hop = int(sr * frame_sec)

    voice_band = (200, 4000)
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    voice_mask = (freqs >= voice_band[0]) & (freqs <= voice_band[1])

    voice_energy = S[voice_mask].sum(axis=0)
    total_energy = S.sum(axis=0) + 1e-9
    ratio = voice_energy / total_energy

    # Smooth over ~1s
    smooth_win = max(1, int(1.0 / frame_sec))
    kernel = np.ones(smooth_win) / smooth_win
    ratio_s = np.convolve(ratio, kernel, mode="same")

    # Threshold at 70th percentile to pick the "vocal-dominant" sections
    threshold = float(np.percentile(ratio_s, 70))
    active = ratio_s > threshold

    windows: list[VOWindow] = []
    start = None
    for i, a in enumerate(active):
        t = i * frame_sec
        if a and start is None:
            start = t
        elif not a and start is not None:
            dur = t - start
            if dur >= 1.0:
                mid = (start + t) / 2
                mid_i = int(mid / frame_sec)
                intensity = float(ratio_s[mid_i]) if 0 <= mid_i < len(ratio_s) else threshold
                windows.append(VOWindow(start_sec=start, end_sec=t, intensity=intensity))
            start = None
    if start is not None:
        t = len(ratio_s) * frame_sec
        dur = t - start
        if dur >= 1.0:
            windows.append(VOWindow(start_sec=start, end_sec=t, intensity=threshold))
    return windows


def _detect_opening_ending_black(video: Path) -> tuple[float, float]:
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video),
         "-vf", "blackdetect=d=0.1:pix_th=0.10", "-f", "null", "-"],
        capture_output=True, text=True, timeout=300,
    )
    import re
    ranges = [
        (float(m.group(1)), float(m.group(2)))
        for m in re.finditer(
            r"black_start:(\d+\.?\d*).*?black_end:(\d+\.?\d*)", r.stderr
        )
    ]
    if not ranges:
        return 0.0, 0.0
    duration = _probe_duration(video)
    opening = 0.0
    if ranges[0][0] < 0.5:
        opening = ranges[0][1] - ranges[0][0]
    ending = 0.0
    if ranges[-1][1] > duration - 1.0:
        ending = ranges[-1][1] - ranges[-1][0]
    return opening, ending


def _color_lab_means(video: Path, section_bounds: list[tuple[float, float]]) -> list[dict]:
    """Sample LAB means for each section via ffmpeg signalstats."""
    out = []
    for start, end in section_bounds:
        mid = (start + end) / 2
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-ss", f"{mid:.2f}", "-i", str(video),
             "-vf", "signalstats,metadata=print:file=-",
             "-frames:v", "1", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
        stats = {}
        for line in r.stderr.splitlines() + r.stdout.splitlines():
            for key in ("YAVG", "UAVG", "VAVG", "SATAVG"):
                needle = f"lavfi.signalstats.{key}="
                if needle in line:
                    try:
                        stats[key] = float(line.split(needle, 1)[1].split()[0])
                    except (ValueError, IndexError):
                        pass
        stats["section_start"] = start
        stats["section_end"] = end
        out.append(stats)
    return out


def analyze(video_path: str | Path, work_dir: Path | None = None) -> StyleProfile:
    video = Path(video_path)
    work = Path(work_dir) if work_dir else video.parent / ".ref-analysis"
    work.mkdir(parents=True, exist_ok=True)
    wav = work / f"{video.stem}.wav"

    prof = StyleProfile(
        video_path=str(video), duration_sec=0.0,
        tempo_bpm=0.0, beat_spacing_sec=0.0,
    )

    prof.duration_sec = _probe_duration(video)
    if prof.duration_sec <= 0:
        return prof

    if not wav.exists():
        _extract_audio(video, wav)

    # Beats
    try:
        tempo, beats = _beat_analysis(wav)
        prof.tempo_bpm = tempo
        if len(beats) > 1:
            import statistics
            prof.beat_spacing_sec = statistics.median(
                [beats[i + 1] - beats[i] for i in range(len(beats) - 1)]
            )
    except Exception:  # noqa: BLE001
        beats = []

    # Scenes / cuts
    try:
        scenes = _detect_scenes(video)
    except Exception:  # noqa: BLE001
        scenes = []
    prof.num_cuts = len(scenes)
    prof.cuts_per_second = len(scenes) / prof.duration_sec

    # Cut-to-beat alignment
    aligned = {"downbeat": 0, "beat": 0, "half-beat": 0, "off-grid": 0}
    shot_durs = []
    if beats and scenes:
        beat_spacing = prof.beat_spacing_sec or (60 / tempo if tempo else 0.5)
        downbeat_spacing = beat_spacing * 4
        for s, e in scenes:
            dur = e - s
            shot_durs.append(dur)
            # Find nearest beat
            nearest = min(beats, key=lambda b: abs(b - s))
            offset = abs(nearest - s)
            if offset < 0.08:
                # Is it a downbeat? Check position in bar
                beat_idx = beats.index(nearest)
                if beat_idx % 4 == 0:
                    aligned["downbeat"] += 1
                else:
                    aligned["beat"] += 1
            elif offset < beat_spacing * 0.55 and offset > beat_spacing * 0.45:
                aligned["half-beat"] += 1
            else:
                aligned["off-grid"] += 1
    prof.cuts_aligned = aligned

    if shot_durs:
        import statistics
        prof.shot_duration_stats = {
            "mean": statistics.mean(shot_durs),
            "median": statistics.median(shot_durs),
            "min": min(shot_durs),
            "max": max(shot_durs),
            "p25": sorted(shot_durs)[len(shot_durs) // 4],
            "p75": sorted(shot_durs)[3 * len(shot_durs) // 4],
        }

    # VO
    try:
        vo = _detect_vo_windows(wav)
        prof.vo_windows = [asdict(w) for w in vo]
        vo_time = sum(w.end_sec - w.start_sec for w in vo)
        prof.vo_coverage_pct = 100 * vo_time / prof.duration_sec
    except Exception:  # noqa: BLE001
        pass

    # Energy
    try:
        prof.energy_curve_0_5s = _energy_curve(wav)
        if prof.energy_curve_0_5s:
            peak_idx = prof.energy_curve_0_5s.index(max(prof.energy_curve_0_5s))
            prof.peak_energy_sec = peak_idx * 0.5
            prof.opening_first_3s_energy = (
                sum(prof.energy_curve_0_5s[:6]) / 6
                if len(prof.energy_curve_0_5s) >= 6
                else 0.0
            )
    except Exception:  # noqa: BLE001
        pass

    # Opening/ending black
    prof.opening_black_sec, prof.ending_black_sec = _detect_opening_ending_black(video)

    # Did first/last cut land on a beat?
    if beats and scenes:
        first_cut = scenes[0][0]
        last_cut = scenes[-1][1]
        prof.opens_on_beat = any(abs(b - first_cut) < 0.1 for b in beats)
        prof.ends_on_beat = any(abs(b - last_cut) < 0.1 for b in beats)

    # Color LAB per approximate-section (divide duration into 4)
    q = prof.duration_sec / 4
    prof.lab_means_by_section = _color_lab_means(
        video, [(q * i, q * (i + 1)) for i in range(4)]
    )
    sats = [s.get("SATAVG") for s in prof.lab_means_by_section if "SATAVG" in s]
    if sats:
        prof.color_saturation_avg = sum(sats) / len(sats)

    return prof


def aggregate_profiles(profiles: list[StyleProfile]) -> dict:
    """Average a batch of style profiles into a meta-style template."""
    if not profiles:
        return {}
    import statistics
    agg = {
        "n_videos": len(profiles),
        "tempo_bpm": statistics.median([p.tempo_bpm for p in profiles if p.tempo_bpm > 0]),
        "cuts_per_second_median": statistics.median([p.cuts_per_second for p in profiles]),
        "shot_dur_median": statistics.median([
            p.shot_duration_stats.get("median", 2.0)
            for p in profiles
            if p.shot_duration_stats
        ]),
        "shot_dur_p25": statistics.median([
            p.shot_duration_stats.get("p25", 1.5)
            for p in profiles
            if p.shot_duration_stats
        ]),
        "shot_dur_p75": statistics.median([
            p.shot_duration_stats.get("p75", 3.0)
            for p in profiles
            if p.shot_duration_stats
        ]),
        "vo_coverage_pct_median": statistics.median(
            [p.vo_coverage_pct for p in profiles]
        ),
        "downbeat_alignment_pct": statistics.mean([
            100 * p.cuts_aligned.get("downbeat", 0) / max(p.num_cuts, 1)
            for p in profiles
        ]),
        "beat_alignment_pct": statistics.mean([
            100 * p.cuts_aligned.get("beat", 0) / max(p.num_cuts, 1)
            for p in profiles
        ]),
        "opening_black_sec_median": statistics.median(
            [p.opening_black_sec for p in profiles]
        ),
        "opening_cold_pct": 100 * sum(
            1 for p in profiles if p.opening_black_sec < 0.3
        ) / len(profiles),
        "color_saturation_avg_median": statistics.median(
            [p.color_saturation_avg for p in profiles if p.color_saturation_avg > 0]
        ) if any(p.color_saturation_avg > 0 for p in profiles) else 0,
    }
    return agg


def save_profile(profile: StyleProfile, out_path: Path | str) -> None:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(profile), indent=2))
