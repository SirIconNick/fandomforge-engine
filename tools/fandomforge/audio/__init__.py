"""Audio analysis for beat detection, drop detection, and energy curves."""

from fandomforge.audio.beat import analyze_beats, BeatMap
from fandomforge.audio.drops import detect_drops, Drop
from fandomforge.audio.energy import compute_energy_curve

__all__ = [
    "analyze_beats",
    "BeatMap",
    "detect_drops",
    "Drop",
    "compute_energy_curve",
]
