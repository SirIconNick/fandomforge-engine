"""Tests for energy zone classification.

Uses synthetic audio so the assertions are deterministic without depending
on a particular song fixture. Each test crafts a signal where the expected
zone label is obvious by construction.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from fandomforge.audio.energy_zones import (
    EnergyZone,
    EnergyZonesResult,
    analyze_energy_zones,
    band_at,
    write_energy_zones,
    zone_at,
    LOW_THRESHOLD,
    HIGH_THRESHOLD,
)
from fandomforge.validation import validate


def _write_wav(path: Path, samples: np.ndarray, sr: int = 22050) -> None:
    """Write a minimal mono WAV via stdlib `wave`."""
    import wave
    samples = np.clip(samples, -1.0, 1.0)
    pcm = (samples * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def _quiet_then_loud(sr: int = 22050, dur: float = 8.0) -> np.ndarray:
    """First half: near-silence (low zone). Second half: loud (high zone)."""
    n = int(sr * dur)
    t = np.linspace(0, dur, n)
    half = n // 2
    out = np.zeros(n)
    out[:half] = 0.02 * np.sin(2 * np.pi * 440 * t[:half])  # quiet
    out[half:] = 0.9 * np.sin(2 * np.pi * 440 * t[half:])   # loud
    return out


def _bass_heavy(sr: int = 22050, dur: float = 4.0) -> np.ndarray:
    n = int(sr * dur)
    t = np.linspace(0, dur, n)
    return 0.6 * np.sin(2 * np.pi * 80 * t)  # 80 Hz, in bass band


def _treble_heavy(sr: int = 22050, dur: float = 4.0) -> np.ndarray:
    n = int(sr * dur)
    t = np.linspace(0, dur, n)
    return 0.6 * np.sin(2 * np.pi * 8000 * t)  # 8 kHz, in treble


class TestZoneClassification:
    def test_quiet_then_loud_produces_low_then_high(self, tmp_path: Path) -> None:
        wav = tmp_path / "quiet_loud.wav"
        _write_wav(wav, _quiet_then_loud())
        r = analyze_energy_zones(wav, resolution_sec=0.25)
        assert r.duration_sec == pytest.approx(8.0, abs=0.1)
        labels = [z.label for z in r.zones]
        # Should land at least one "low" zone before any "high" zone
        assert "low" in labels or "mid" in labels
        assert "high" in labels
        first_high = next(i for i, lbl in enumerate(labels) if lbl == "high")
        for prior_label in labels[:first_high]:
            assert prior_label in {"low", "mid"}

    def test_zones_cover_full_duration(self, tmp_path: Path) -> None:
        wav = tmp_path / "loud.wav"
        _write_wav(wav, _quiet_then_loud())
        r = analyze_energy_zones(wav, resolution_sec=0.25)
        if r.zones:
            assert r.zones[0].start_sec <= 0.3
            assert r.zones[-1].end_sec >= r.duration_sec - 0.5

    def test_silent_input_returns_empty_or_low(self, tmp_path: Path) -> None:
        wav = tmp_path / "silent.wav"
        _write_wav(wav, np.zeros(22050 * 3))
        r = analyze_energy_zones(wav, resolution_sec=0.25)
        # All zones should be low (or no zones if normalize collapses)
        for z in r.zones:
            assert z.label == "low"


class TestSpectralBands:
    def test_bass_signal_dominates_bass_band(self, tmp_path: Path) -> None:
        wav = tmp_path / "bass.wav"
        _write_wav(wav, _bass_heavy())
        r = analyze_energy_zones(wav, resolution_sec=0.5)
        for b in r.bands:
            # bass should be larger than treble (treble band shouldn't have
            # significant content from an 80 Hz tone)
            assert b.bass >= b.treble

    def test_treble_signal_dominates_treble_band(self, tmp_path: Path) -> None:
        wav = tmp_path / "treble.wav"
        _write_wav(wav, _treble_heavy())
        r = analyze_energy_zones(wav, resolution_sec=0.5)
        for b in r.bands:
            assert b.treble >= b.bass

    def test_band_normalization(self, tmp_path: Path) -> None:
        """Each band is normalized to its own peak so the maximum of each
        band across the curve should be 1.0."""
        wav = tmp_path / "mixed.wav"
        sr = 22050
        n = sr * 4
        t = np.linspace(0, 4, n)
        sig = 0.4 * np.sin(2 * np.pi * 80 * t) + 0.4 * np.sin(2 * np.pi * 8000 * t)
        _write_wav(wav, sig)
        r = analyze_energy_zones(wav, resolution_sec=0.5)
        if r.bands:
            assert max(b.bass for b in r.bands) == pytest.approx(1.0, abs=0.001)
            assert max(b.treble for b in r.bands) == pytest.approx(1.0, abs=0.001)


class TestTransients:
    def test_percussive_clicks_classified_percussive(self, tmp_path: Path) -> None:
        sr = 22050
        n = sr * 4
        out = np.zeros(n)
        # Insert sharp clicks every 1 second
        for sec in (1.0, 2.0, 3.0):
            i = int(sec * sr)
            out[i : i + 220] = np.linspace(1.0, 0.0, 220) * np.random.RandomState(0).randn(220)
        wav = tmp_path / "clicks.wav"
        _write_wav(wav, np.clip(out, -1, 1))
        r = analyze_energy_zones(wav)
        # Should detect at least one transient near each click
        click_times = {1.0, 2.0, 3.0}
        detected = [t.time_sec for t in r.transients]
        for ct in click_times:
            assert any(abs(d - ct) < 0.2 for d in detected), (
                f"no transient detected near {ct}s; got {detected}"
            )


class TestBeatMapOverlay:
    def test_drop_promotes_zone_label(self, tmp_path: Path) -> None:
        wav = tmp_path / "song.wav"
        _write_wav(wav, _quiet_then_loud())
        beat_map = {"drops": [{"time": 5.0, "intensity": 0.95, "type": "main_drop"}]}
        r = analyze_energy_zones(wav, resolution_sec=0.5, beat_map=beat_map)
        # Zone covering 5.0s should now be labeled "drop"
        z = zone_at(r.zones, 5.0)
        assert z is not None
        assert z.label == "drop"

    def test_buildup_section_labeled(self, tmp_path: Path) -> None:
        wav = tmp_path / "song.wav"
        _write_wav(wav, _quiet_then_loud())
        # Buildup spanning the second half — every fully-contained zone gets
        # promoted. Use end>=duration so at least one zone is fully inside.
        beat_map = {
            "drops": [],
            "buildups": [{"start": 4.0, "end": 7.5, "curve": "linear"}],
            "breakdowns": [],
        }
        r = analyze_energy_zones(wav, resolution_sec=0.5, beat_map=beat_map)
        # At least one zone within the buildup window should be labeled "buildup"
        buildup_zones = [z for z in r.zones if z.label == "buildup"]
        assert buildup_zones, f"expected buildup label, got {[z.label for z in r.zones]}"


class TestPersistence:
    def test_write_round_trip_validates(self, tmp_path: Path) -> None:
        wav = tmp_path / "song.wav"
        _write_wav(wav, _quiet_then_loud())
        r = analyze_energy_zones(wav, resolution_sec=0.25)
        out = tmp_path / "energy-zones.json"
        write_energy_zones(r, out)
        payload = json.loads(out.read_text())
        # Schema-valid?
        validate(payload, "energy-zones")


class TestQueryHelpers:
    def test_zone_at_finds_covering_zone(self) -> None:
        zones = [
            EnergyZone(0.0, 2.0, "low", 0.8, 0.2),
            EnergyZone(2.0, 5.0, "high", 0.9, 0.85),
        ]
        z = zone_at(zones, 1.0)
        assert z is not None and z.label == "low"
        z = zone_at(zones, 3.5)
        assert z is not None and z.label == "high"
        assert zone_at(zones, 100.0) is None

    def test_band_at_returns_nearest(self) -> None:
        from fandomforge.audio.energy_zones import BandSample
        bands = [
            BandSample(0.0, 0.5, 0.3, 0.1),
            BandSample(0.25, 0.4, 0.4, 0.2),
            BandSample(0.5, 0.3, 0.5, 0.3),
        ]
        b = band_at(bands, 0.27)
        assert b is not None and b.time_sec == 0.25
