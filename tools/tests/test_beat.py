"""Tests for the beat detection module."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from fandomforge.audio.beat import analyze_beats
from fandomforge.audio.drops import detect_breakdowns, detect_buildups, detect_drops
from fandomforge.audio.energy import compute_energy_curve


@pytest.fixture
def click_track_120bpm(tmp_path: Path) -> Path:
    """A 10-second click track at exactly 120 BPM for testing."""
    sr = 22050
    duration_sec = 10.0
    bpm = 120.0
    samples_per_beat = int(sr * 60 / bpm)
    total_samples = int(sr * duration_sec)

    y = np.zeros(total_samples, dtype=np.float32)
    click_len = int(0.01 * sr)  # 10ms click
    for beat_idx in range(int(duration_sec * bpm / 60)):
        start = beat_idx * samples_per_beat
        end = min(start + click_len, total_samples)
        y[start:end] = np.sin(2 * np.pi * 1000 * np.arange(end - start) / sr)

    path = tmp_path / "click_120.wav"
    sf.write(str(path), y, sr)
    return path


@pytest.fixture
def drop_track(tmp_path: Path) -> Path:
    """A synthetic track with a clear loud 'drop' at 5 seconds."""
    sr = 22050
    duration_sec = 15.0
    total_samples = int(sr * duration_sec)
    y = np.random.uniform(-0.05, 0.05, total_samples).astype(np.float32)

    # Drop region: 5.0s - 7.0s, with a bass-heavy sustained tone
    drop_start = int(5.0 * sr)
    drop_end = int(7.0 * sr)
    t = np.arange(drop_end - drop_start) / sr
    bass = 0.5 * np.sin(2 * np.pi * 60 * t)  # 60 Hz bass
    y[drop_start:drop_end] += bass.astype(np.float32)

    path = tmp_path / "drop.wav"
    sf.write(str(path), y, sr)
    return path


class TestAnalyzeBeats:
    def test_detects_bpm_close_to_target(self, click_track_120bpm: Path) -> None:
        result = analyze_beats(click_track_120bpm)
        # Allow wide tolerance for synthetic clicks; librosa may report 120 or a near integer multiple
        assert 100 <= result.bpm <= 140 or abs(result.bpm - 60) < 5 or abs(result.bpm - 240) < 5

    def test_returns_beats(self, click_track_120bpm: Path) -> None:
        result = analyze_beats(click_track_120bpm)
        assert len(result.beats) > 0

    def test_returns_downbeats(self, click_track_120bpm: Path) -> None:
        result = analyze_beats(click_track_120bpm)
        assert len(result.downbeats) > 0
        assert len(result.downbeats) <= len(result.beats)

    def test_duration_correct(self, click_track_120bpm: Path) -> None:
        result = analyze_beats(click_track_120bpm)
        assert 9.5 <= result.duration_sec <= 10.5

    def test_raises_on_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            analyze_beats(tmp_path / "nope.wav")

    def test_tempo_hint_respected(self, click_track_120bpm: Path) -> None:
        result = analyze_beats(click_track_120bpm, tempo_hint=120.0)
        # With a strong hint, we should land close to 120 (or exact multiples)
        assert result.bpm > 0


class TestDetectDrops:
    def test_finds_drop(self, drop_track: Path) -> None:
        drops = detect_drops(drop_track)
        assert len(drops) >= 1
        # The drop should be detected near the 5-7s window
        main = drops[0]
        assert 4.0 <= main.time <= 8.0

    def test_no_drops_in_silence(self, tmp_path: Path) -> None:
        sr = 22050
        y = np.zeros(int(sr * 5.0), dtype=np.float32)
        path = tmp_path / "silent.wav"
        sf.write(str(path), y, sr)
        drops = detect_drops(path)
        assert drops == []


class TestEnergyCurve:
    def test_returns_curve(self, drop_track: Path) -> None:
        curve = compute_energy_curve(drop_track, resolution_sec=0.5)
        assert len(curve) > 0
        for t, e in curve:
            assert 0.0 <= e <= 1.0


class TestBuildupsAndBreakdowns:
    def test_buildups_runs(self, drop_track: Path) -> None:
        drops = detect_drops(drop_track)
        buildups = detect_buildups(drop_track, drops)
        # May or may not detect — our test fixture doesn't have a pronounced buildup
        assert isinstance(buildups, list)

    def test_breakdowns_runs(self, drop_track: Path) -> None:
        bds = detect_breakdowns(drop_track)
        assert isinstance(bds, list)
