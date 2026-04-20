"""Engagement heuristic (Phase 4.6) — pacing curve match + visual variety
+ complement-pair usage.

Sub-metrics 0-100, composite is unweighted average:
  pacing_curve_match — actual cuts-per-minute curve vs reference-prior expectation
  visual_variety     — fandom + role + framing diversity over time
  complement_usage   — % of consecutive shot pairs that match cross-source
                        complement pairs (visual rhyming)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EngagementReport:
    pacing_curve_match: float = 0.0
    visual_variety: float = 0.0
    complement_usage: float = 0.0
    composite: float = 0.0
    notes: list[str] = field(default_factory=list)
    measurements: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pacing_curve_match": round(self.pacing_curve_match, 1),
            "visual_variety": round(self.visual_variety, 1),
            "complement_usage": round(self.complement_usage, 1),
            "composite": round(self.composite, 1),
            "notes": list(self.notes),
            "measurements": dict(self.measurements),
        }


def _cuts_per_minute_curve(shots: list[dict[str, Any]], fps: float, window_sec: float = 10.0) -> list[float]:
    """Sliding-window cuts-per-minute. Returns one value per `window_sec` slice."""
    if not shots:
        return []
    end = max(float(s.get("start_frame", 0)) / fps for s in shots) + 5
    starts = sorted(float(s.get("start_frame", 0)) / fps for s in shots)
    cpm: list[float] = []
    t = 0.0
    while t < end:
        lo, hi = t, t + window_sec
        cuts_in_window = sum(1 for s in starts if lo <= s < hi)
        cpm.append(cuts_in_window * (60.0 / window_sec))
        t += window_sec
    return cpm


def _pacing_curve_match(
    shots: list[dict[str, Any]],
    fps: float,
    target_cpm: float | None,
) -> tuple[float, dict[str, Any]]:
    if target_cpm is None or target_cpm <= 0:
        return 50.0, {"target_cpm": None}
    cpm = _cuts_per_minute_curve(shots, fps)
    if not cpm:
        return 0.0, {"actual_avg_cpm": 0.0, "target_cpm": target_cpm}
    actual_avg = sum(cpm) / len(cpm)
    deviation = abs(actual_avg - target_cpm) / target_cpm
    score = max(0.0, 100.0 - (deviation * 100.0))
    return score, {
        "actual_avg_cpm": round(actual_avg, 1),
        "target_cpm": target_cpm,
        "actual_curve": [round(x, 1) for x in cpm[:30]],
    }


def _shannon_diversity(counts: dict[str, int]) -> float:
    total = sum(counts.values())
    if total == 0 or len(counts) <= 1:
        return 0.0
    probs = [c / total for c in counts.values() if c > 0]
    entropy = -sum(p * math.log(p, 2) for p in probs)
    max_entropy = math.log(len(counts), 2)
    return entropy / max_entropy if max_entropy > 0 else 0.0


def _visual_variety(shots: list[dict[str, Any]]) -> tuple[float, dict[str, Any]]:
    if not shots:
        return 0.0, {}
    fandoms = {}
    roles = {}
    framings = {}
    for s in shots:
        f = s.get("fandom") or ""
        r = s.get("role") or ""
        fr = s.get("framing") or ""
        if f:
            fandoms[f] = fandoms.get(f, 0) + 1
        if r:
            roles[r] = roles.get(r, 0) + 1
        if fr:
            framings[fr] = framings.get(fr, 0) + 1
    fan_div = _shannon_diversity(fandoms)
    role_div = _shannon_diversity(roles)
    fram_div = _shannon_diversity(framings)
    composite = (fan_div + role_div + fram_div) / 3.0 * 100.0
    return composite, {
        "fandom_diversity": round(fan_div, 3),
        "role_diversity": round(role_div, 3),
        "framing_diversity": round(fram_div, 3),
    }


def _complement_usage(
    shots: list[dict[str, Any]],
    complement_plan: dict[str, Any] | None,
) -> tuple[float, dict[str, Any]]:
    pairs = (complement_plan or {}).get("pairs") or []
    if not pairs or not shots:
        return 0.0, {"complement_pair_count": len(pairs)}
    # Build set of (thrown_idx, received_idx) tuples
    pair_set = {
        (int(p["thrown_shot_idx"]), int(p["received_shot_idx"]))
        for p in pairs
        if "thrown_shot_idx" in p and "received_shot_idx" in p
    }
    if not pair_set:
        return 0.0, {"complement_pair_count": 0}
    # Count adjacent shot pairs that match a complement pair
    used = 0
    for i in range(len(shots) - 1):
        if (i, i + 1) in pair_set:
            used += 1
    pct = used / max(1, len(pair_set)) * 100.0
    return min(100.0, pct), {
        "complement_pair_count": len(pair_set),
        "complement_pairs_used": used,
    }


def score_engagement(
    shot_list: dict[str, Any],
    *,
    edit_type_priors: dict[str, Any] | None = None,
    complement_plan: dict[str, Any] | None = None,
) -> EngagementReport:
    shots = (shot_list or {}).get("shots") or []
    fps = float((shot_list or {}).get("fps") or 24.0)
    target_cpm = None
    if edit_type_priors:
        target_cpm = float(edit_type_priors.get("target_cuts_per_minute") or 0) or None

    pacing, pacing_meas = _pacing_curve_match(shots, fps, target_cpm)
    variety, variety_meas = _visual_variety(shots)
    complement, complement_meas = _complement_usage(shots, complement_plan)

    # Composite from POPULATED sub-metrics only. A structurally missing
    # sub-metric (no complement pairs in the plan, no target_cpm in priors)
    # shouldn't pull the composite to zero — that's a data gap not a quality
    # failure. Only count complement_usage when there's a real plan with pairs.
    populated: list[float] = []
    if target_cpm is not None:
        populated.append(pacing)
    elif pacing > 0:
        populated.append(pacing)
    if len(shots) > 0:
        populated.append(variety)
    if complement_meas.get("complement_pair_count", 0) > 0:
        populated.append(complement)
    composite = sum(populated) / len(populated) if populated else 0.0

    notes: list[str] = []
    if target_cpm is not None and abs(pacing_meas.get("actual_avg_cpm", 0) - target_cpm) / max(target_cpm, 1) > 0.4:
        notes.append(f"cuts/min off target by >40% (actual={pacing_meas.get('actual_avg_cpm')}, target={target_cpm})")
    if variety_meas.get("fandom_diversity", 0) < 0.3 and len(shots) > 20:
        notes.append("low fandom diversity — one source dominates")
    if complement_meas.get("complement_pair_count", 0) == 0:
        notes.append("no complement pairs in plan — visual rhyming sub-score skipped (not penalized)")

    measurements = {**pacing_meas, **variety_meas, **complement_meas}
    return EngagementReport(
        pacing_curve_match=pacing,
        visual_variety=variety,
        complement_usage=complement,
        composite=composite,
        notes=notes,
        measurements=measurements,
    )


__all__ = ["EngagementReport", "score_engagement"]
