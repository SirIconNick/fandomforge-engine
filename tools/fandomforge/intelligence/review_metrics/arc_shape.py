"""Arc shape scorer (Phase 4.4) — does the rendered tension curve build to
climax and resolve as the arc-architect intended?

Reads `data/tension-curve.json`. Scores three sub-metrics 0-100:
  builds_to_climax — actual tension monotonically trends up into climax act
  resolves         — actual tension drops or peaks-and-holds in release act
  intent_match     — RMS delta between target and actual curves (low = good)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ArcShapeReport:
    builds_to_climax: float = 0.0
    resolves: float = 0.0
    intent_match: float = 0.0
    composite: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "builds_to_climax": round(self.builds_to_climax, 1),
            "resolves": round(self.resolves, 1),
            "intent_match": round(self.intent_match, 1),
            "composite": round(self.composite, 1),
            "notes": list(self.notes),
        }


def _build_score(samples: list[dict[str, Any]]) -> float:
    """Compare avg actual tension across setup vs climax. Score 0 when
    climax avg <= setup avg; 100 when climax avg is +0.6 above setup."""
    setup = [float(s.get("actual_tension", 0)) for s in samples if s.get("arc_role") == "setup"]
    climax = [float(s.get("actual_tension", 0)) for s in samples if s.get("arc_role") == "climax"]
    if not setup or not climax:
        return 0.0
    delta = (sum(climax) / len(climax)) - (sum(setup) / len(setup))
    return max(0.0, min(100.0, (delta / 0.6) * 100.0))


def _resolve_score(samples: list[dict[str, Any]]) -> float:
    """Release should drop below climax peak OR hold near peak (peak-and-hold).
    Score 100 when release_max ≤ climax_max × 0.85; 50 when held at peak;
    0 when release exceeds climax."""
    release = [float(s.get("actual_tension", 0)) for s in samples if s.get("arc_role") == "release"]
    climax = [float(s.get("actual_tension", 0)) for s in samples if s.get("arc_role") == "climax"]
    if not release:
        # No release act = treat as resolved (compressed arc OK)
        return 100.0
    if not climax:
        return 50.0
    rmax, cmax = max(release), max(climax)
    if rmax <= cmax * 0.85:
        return 100.0
    if rmax > cmax:
        # Tension grew past climax — bad release
        excess = rmax - cmax
        return max(0.0, 50.0 - (excess * 100))
    # Held at peak, slight overflow
    return 70.0


def _intent_match_score(rms_delta: float) -> float:
    """rms_delta of 0 → 100; 0.5 → 50; ≥1.0 → 0. Linear inverse."""
    return max(0.0, min(100.0, 100.0 - (rms_delta * 100.0)))


def score_arc_shape(tension_curve: dict[str, Any] | None) -> ArcShapeReport:
    if not tension_curve:
        return ArcShapeReport(
            notes=["no tension-curve.json — skipped (run autopilot to build it)"],
        )
    samples = tension_curve.get("samples") or []
    summary = tension_curve.get("summary") or {}

    builds = _build_score(samples)
    resolves = _resolve_score(samples)
    intent_match = _intent_match_score(float(summary.get("rms_delta", 0)))
    composite = (builds + resolves + intent_match) / 3.0

    notes: list[str] = []
    if builds < 30:
        notes.append("does not build to climax — verify arc-architect set rising tension targets")
    if resolves < 30:
        notes.append("does not resolve — release act is at or above climax peak")
    if intent_match < 50:
        notes.append(f"actual tension diverges from target (rms_delta={summary.get('rms_delta', 0):.2f})")

    return ArcShapeReport(
        builds_to_climax=builds,
        resolves=resolves,
        intent_match=intent_match,
        composite=composite,
        notes=notes,
    )


__all__ = ["ArcShapeReport", "score_arc_shape"]
