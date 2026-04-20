"""Tension curve constructor (Phase 2.3).

Builds a per-second tension model from:
  - edit-plan acts[] (each act's tension_target + start/end from Phase 2.1)
  - beat-map energy_curve (the actual song energy)
  - emotion-arc (per-shot emotion + intensity if present)

Output: tension-curve.json with target vs actual per sample. Phase 4.4
arc_shape_realized reads this to score whether the render actually builds
to the climax.

Cross-type: both target and actual tension ranges -1..+1 regardless of
edit type. The TARGET differs by type via arc-architect; the ACTUAL is
computed the same way everywhere.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_RESOLUTION_SEC = 1.0


@dataclass
class TensionSample:
    time_sec: float
    target_tension: float
    actual_tension: float
    delta: float
    act_index: int
    arc_role: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_sec": round(self.time_sec, 2),
            "target_tension": round(self.target_tension, 3),
            "actual_tension": round(self.actual_tension, 3),
            "delta": round(self.delta, 3),
            "act_index": self.act_index,
            "arc_role": self.arc_role,
        }


def _sample_target(acts: list[dict[str, Any]], t: float) -> tuple[float, int, str]:
    """Linear ramp inside each act from its previous act's tension_target to
    its own, so transitions feel gradual rather than stepping."""
    if not acts:
        return 0.0, 0, "setup"
    # Find the act containing t
    for idx, a in enumerate(acts):
        start = float(a.get("start_sec", 0))
        end = float(a.get("end_sec", 0))
        if start <= t < end:
            cur = float(a.get("tension_target", 0))
            prev_target = float(acts[idx - 1].get("tension_target", cur)) if idx > 0 else cur
            # interpolate across first half of the act from prev→cur, hold at cur
            span = max(0.01, end - start)
            progress = (t - start) / span
            # ease: first 40% ramp, last 60% hold
            if progress < 0.4:
                ramp = progress / 0.4
                target = prev_target + (cur - prev_target) * ramp
            else:
                target = cur
            return target, int(a.get("number", idx + 1)), str(a.get("arc_role", "setup"))
    # t beyond last act
    last = acts[-1]
    return float(last.get("tension_target", 0)), int(last.get("number", len(acts))), str(last.get("arc_role", "setup"))


def _sample_actual(
    energy_curve: list[list[float]] | None,
    t: float,
    emotion_samples: list[dict[str, Any]] | None,
    act_tension_target: float,
) -> float:
    """Actual tension at t = weighted average of:
      - song energy (0-1) mapped to -1..+1 by act's expected range
      - emotion intensity × emotion sorrow/triumph axis
    Falls back to the act's target when no data is available (zero delta)."""
    if not energy_curve:
        return act_tension_target

    # Nearest energy sample
    energy = _nearest_sample(energy_curve, t)
    if energy is None:
        return act_tension_target

    # Map 0-1 energy → -1..+1 using the act's target as anchor
    # High-energy zones (>0.7) contribute positive tension; valleys (<0.3) negative
    if energy > 0.7:
        energy_tension = 0.6 + (energy - 0.7) * 1.33  # 0.7→0.6, 1.0→1.0
    elif energy < 0.3:
        energy_tension = -0.3 - (0.3 - energy) * 1.0  # 0.3→-0.3, 0.0→-0.6
    else:
        energy_tension = (energy - 0.5) * 0.6  # 0.5→0, 0.3→-0.12, 0.7→0.12
    energy_tension = max(-1.0, min(1.0, energy_tension))

    # Emotion contribution (if any)
    emotion_tension = 0.0
    if emotion_samples:
        # nearest sample by time
        best = None
        best_diff = 1e9
        for s in emotion_samples:
            sec = float(s.get("start_sec", 0))
            diff = abs(sec - t)
            if diff < best_diff:
                best_diff = diff
                best = s
        if best is not None and best_diff < 3.0:
            intensity = float(best.get("intensity", 0))
            vec = best.get("vector") or []
            # Treat the 8-dim vector's triumph+tension peaks as positive,
            # grief+sorrow as negative tension.
            # vector order matches intent.tone_vector
            if len(vec) >= 8:
                pos = float(vec[1]) + float(vec[4])  # triumph + tension
                neg = float(vec[0]) + float(vec[6])  # grief + sorrow
                emotion_tension = (pos - neg) * intensity * 0.5

    # Blend: 60% energy, 40% emotion
    actual = 0.6 * energy_tension + 0.4 * emotion_tension
    return round(max(-1.0, min(1.0, actual)), 3)


def _nearest_sample(energy_curve: list[list[float]], t: float) -> float | None:
    """Return energy value at t (linear nearest-neighbor)."""
    if not energy_curve:
        return None
    # energy_curve is list of [time_sec, energy_0_to_1]
    best_diff = 1e9
    best = None
    for pair in energy_curve:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        diff = abs(float(pair[0]) - t)
        if diff < best_diff:
            best_diff = diff
            best = float(pair[1])
    return best


def build_tension_curve(
    edit_plan: dict[str, Any],
    *,
    beat_map: dict[str, Any] | None = None,
    emotion_arc: dict[str, Any] | None = None,
    resolution_sec: float = DEFAULT_RESOLUTION_SEC,
) -> dict[str, Any]:
    """Produce a tension-curve dict for the given plan + music intel."""
    acts = edit_plan.get("acts") or []
    duration = 0.0
    for a in acts:
        duration = max(duration, float(a.get("end_sec", 0)))
    if duration <= 0:
        duration = float(edit_plan.get("length_seconds") or 60.0)

    energy_curve = (beat_map or {}).get("energy_curve") or []
    emotion_samples = (emotion_arc or {}).get("samples") or []

    samples: list[TensionSample] = []
    t = 0.0
    while t <= duration:
        target, act_index, arc_role = _sample_target(acts, t)
        actual = _sample_actual(energy_curve, t, emotion_samples, target)
        samples.append(TensionSample(
            time_sec=t,
            target_tension=target,
            actual_tension=actual,
            delta=actual - target,
            act_index=act_index,
            arc_role=arc_role,
        ))
        t += resolution_sec

    # Summary stats
    if samples:
        peak_target = max(s.target_tension for s in samples)
        peak_actual_sample = max(samples, key=lambda s: s.actual_tension)
        rms_delta = (sum((s.delta ** 2) for s in samples) / len(samples)) ** 0.5

        # builds_to_climax: last climax sample's actual > avg of setup actuals
        setup_actuals = [s.actual_tension for s in samples if s.arc_role == "setup"]
        climax_actuals = [s.actual_tension for s in samples if s.arc_role == "climax"]
        builds_to_climax = bool(
            climax_actuals and setup_actuals
            and max(climax_actuals) > (sum(setup_actuals) / len(setup_actuals))
        )

        # resolves: final release samples below the climax peak
        release_actuals = [s.actual_tension for s in samples if s.arc_role == "release"]
        if release_actuals and climax_actuals:
            resolves = bool(
                max(release_actuals) < max(climax_actuals)
                or max(release_actuals) >= max(climax_actuals) * 0.8
            )
        else:
            resolves = True
    else:
        peak_target = 0.0
        peak_actual_sample = None
        rms_delta = 0.0
        builds_to_climax = False
        resolves = True

    return {
        "schema_version": 1,
        "duration_sec": round(duration, 3),
        "resolution_sec": resolution_sec,
        "samples": [s.to_dict() for s in samples],
        "summary": {
            "peak_target": round(peak_target, 3),
            "peak_actual": round(peak_actual_sample.actual_tension if peak_actual_sample else 0.0, 3),
            "peak_actual_time_sec": round(peak_actual_sample.time_sec if peak_actual_sample else 0.0, 2),
            "rms_delta": round(rms_delta, 3),
            "builds_to_climax": builds_to_climax,
            "resolves": resolves,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "ff tension curve",
    }


def write_tension_curve(curve: dict[str, Any], out_path: Path) -> Path:
    from fandomforge.validation import validate_and_write
    out_path.parent.mkdir(parents=True, exist_ok=True)
    validate_and_write(curve, "tension-curve", out_path)
    return out_path


__all__ = [
    "build_tension_curve",
    "write_tension_curve",
]
