"""Slot-fit scorer (Phase 2.2) — score how well a candidate shot fits a slot.

Together with the arc architect (Phase 2.1) this is the structural fix for
"the engine throws shots together without coherent structure." The arc
architect sets the *budget* per act (pacing band, energy target, emotional
goal). The slot-fit scorer applies that budget when picking among candidate
clips for a specific timeline slot.

Scoring (each dimension 0-1, weights sum to 1.0):
  emotional_register_match  30%  candidate's emotional vector vs act's tone
  energy_zone_fit           20%  candidate's category-zone affinity vs slot zone
  motion_continuity         15%  candidate's motion vector vs adjacent shots
  color_continuity          10%  candidate's luma vs adjacent shots
  edit_type_preference      15%  category-bias from clip-category for edit_type
  duration_fit              10%  candidate duration vs act pacing band

Inputs the scorer needs (per call):
  - candidate clip metadata (from clip_metadata.py — Phase 1.3)
  - the act it would land in (from arc-architect's output)
  - the energy zone covering the slot's timestamp (from energy_zones.py)
  - prior + next shot in the timeline (for continuity)
  - the active edit_type (drives category bias)

Designed cross-type (amendment A3): no per-type branches inside score().
Per-type behavior comes from the clip-category taxonomy's edit_type_bias
table + the arc-architect's pacing band.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fandomforge.intelligence.arc_architect import shot_duration_band
from fandomforge.intelligence.clip_categories import edit_type_bias


# Default weights per the v2 plan. Tuned for cross-type baseline; per-type
# adjustments can be supplied at call time via the `weights` kwarg.
DEFAULT_WEIGHTS = {
    "emotional_register_match": 0.30,
    "energy_zone_fit": 0.20,
    "motion_continuity": 0.15,
    "color_continuity": 0.10,
    "edit_type_preference": 0.15,
    "duration_fit": 0.10,
}


@dataclass
class SlotContext:
    """The 'where am I placing this' context the scorer needs."""
    act_index: int
    act_pacing: str           # slow|medium|fast|frantic
    act_energy_target: float  # 0-100
    act_arc_role: str         # setup|escalation|climax|release|interlude
    slot_time_sec: float
    slot_duration_sec: float
    energy_zone_label: str    # low|mid|high|drop|buildup|breakdown
    edit_type: str
    tone_target: list[float]  # 8-dim from intent.tone_vector
    prev_shot: dict[str, Any] | None = None
    next_shot: dict[str, Any] | None = None


@dataclass
class SlotFitScore:
    """Per-dimension breakdown + composite for a candidate."""
    composite: float
    breakdown: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "composite": round(self.composite, 3),
            "breakdown": {k: round(v, 3) for k, v in self.breakdown.items()},
            "notes": list(self.notes),
        }


def _emotional_register_match(candidate: dict[str, Any], tone_target: list[float]) -> float:
    """Cosine-like similarity between candidate's emotional_register and the
    intent's tone_vector. Falls back to 0.5 (neutral) when candidate has no
    register data — better than penalizing unenriched clips into oblivion."""
    reg = candidate.get("emotional_register")
    if not isinstance(reg, list) or len(reg) != 8:
        return 0.5
    if not isinstance(tone_target, list) or len(tone_target) != 8:
        return 0.5
    # Both 0-1 vectors; cosine similarity
    a = [float(x) for x in reg]
    b = [float(x) for x in tone_target]
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        # If either is all-zero, use a neutral mid-score rather than 0
        return 0.5
    return max(0.0, min(1.0, dot / (na * nb)))


def _energy_zone_fit(candidate: dict[str, Any], slot_zone: str) -> float:
    """Lookup candidate's clip_category, ask the taxonomy whether that
    category fits the slot's zone. 1.0 if the category lists this zone in
    its energy_zone_affinity, 0.3 otherwise (still placeable, just sub-
    optimal). Unknown categories get 0.5."""
    from fandomforge.intelligence.clip_categories import categories_for_zone
    cat = candidate.get("clip_category")
    if not cat:
        return 0.5
    affinity = set(categories_for_zone(slot_zone))
    return 1.0 if cat in affinity else 0.3


def _motion_continuity(candidate: dict[str, Any], prev: dict[str, Any] | None, next_: dict[str, Any] | None) -> float:
    """Penalize abrupt motion-vector reversals across adjacent shots.
    Score 1.0 = smooth flow; 0.0 = 180° flip.

    motion_vector is in degrees (0-360) or null (static). Static next to
    static is fine; static next to motion is fine; opposing motion is bad.
    """
    cand_mv = candidate.get("motion_vector")
    pscore = 1.0
    nscore = 1.0
    if prev is not None and isinstance(prev.get("motion_vector"), (int, float)) and isinstance(cand_mv, (int, float)):
        pscore = _motion_smoothness(float(prev["motion_vector"]), float(cand_mv))
    if next_ is not None and isinstance(next_.get("motion_vector"), (int, float)) and isinstance(cand_mv, (int, float)):
        nscore = _motion_smoothness(float(cand_mv), float(next_["motion_vector"]))
    return (pscore + nscore) / 2


def _motion_smoothness(angle_a: float, angle_b: float) -> float:
    """1.0 for parallel (same direction), 0.0 for opposite."""
    diff = abs(((angle_a - angle_b) + 180) % 360 - 180)  # 0-180
    return 1.0 - (diff / 180.0)


def _color_continuity(candidate: dict[str, Any], prev: dict[str, Any] | None, next_: dict[str, Any] | None) -> float:
    """Penalize big luma jumps between adjacent shots. We only have luma
    available cheaply (avg per shot); chroma continuity is a Phase 4 thing.
    """
    cand_luma = _extract_luma(candidate)
    if cand_luma is None:
        return 0.5
    deltas: list[float] = []
    for nb in (prev, next_):
        if nb is None:
            continue
        nb_luma = _extract_luma(nb)
        if nb_luma is None:
            continue
        deltas.append(abs(cand_luma - nb_luma))
    if not deltas:
        return 0.5
    avg_delta = sum(deltas) / len(deltas)
    # Map 0..0.5 luma delta → 1..0 score (linear)
    return max(0.0, 1.0 - (avg_delta / 0.5))


def _extract_luma(shot: dict[str, Any]) -> float | None:
    """Pull a normalized luma 0-1 from a shot dict. Try a few fields."""
    notes = shot.get("color_notes") or ""
    if "luma=" in notes:
        try:
            return float(notes.split("luma=")[1].split()[0].rstrip(","))
        except (IndexError, ValueError):
            pass
    if isinstance(shot.get("avg_luma"), (int, float)):
        return float(shot["avg_luma"])
    return None


def _edit_type_preference(candidate: dict[str, Any], edit_type: str) -> float:
    """How much does this edit type prefer this clip category?

    Reads two sources, in order of precedence:
      1. edit-types.json → clip_selection_weights (Phase 2.4 per-type override)
      2. clip-categories.json → edit_type_bias (Phase 0.5.4 base bias)

    Map bias [0..2] → score [0..1] (1.0 at bias=1.0 neutral, 0 at bias=0).
    """
    cat = candidate.get("clip_category")
    if not cat:
        return 0.5

    # Per-type override (Phase 2.4)
    try:
        from fandomforge.intelligence.edit_classifier import load_type_priors
        priors = load_type_priors(edit_type)
        if priors:
            csw = priors.get("clip_selection_weights") or {}
            if cat in csw:
                return max(0.0, min(1.0, float(csw[cat]) / 2.0))
    except Exception:  # noqa: BLE001
        pass

    # Base bias from taxonomy
    try:
        bias = edit_type_bias(cat, edit_type)
    except KeyError:
        return 0.5
    return max(0.0, min(1.0, bias / 2.0))


def _duration_fit(candidate: dict[str, Any], pacing: str) -> float:
    """Score the candidate's intended duration against the act's pacing band.
    The proposer hasn't bound the candidate to a slot yet; use the
    candidate's intrinsic duration when known, else neutral."""
    band = shot_duration_band(pacing)
    cd = candidate.get("intended_duration_sec") or candidate.get("duration_sec")
    if cd is None:
        return 0.5
    cd = float(cd)
    lo, hi = band
    if lo <= cd <= hi:
        return 1.0
    # Linear penalty outside band
    if cd < lo:
        return max(0.0, cd / lo)
    return max(0.0, hi / cd)


def score_candidate(
    candidate: dict[str, Any],
    context: SlotContext,
    *,
    weights: dict[str, float] | None = None,
) -> SlotFitScore:
    """Compute a 0-1 composite + per-dim breakdown for a single candidate."""
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update(weights)

    breakdown = {
        "emotional_register_match": _emotional_register_match(candidate, context.tone_target),
        "energy_zone_fit": _energy_zone_fit(candidate, context.energy_zone_label),
        "motion_continuity": _motion_continuity(candidate, context.prev_shot, context.next_shot),
        "color_continuity": _color_continuity(candidate, context.prev_shot, context.next_shot),
        "edit_type_preference": _edit_type_preference(candidate, context.edit_type),
        "duration_fit": _duration_fit(candidate, context.act_pacing),
    }
    composite = sum(w.get(k, 0) * v for k, v in breakdown.items())
    notes: list[str] = []
    if breakdown["edit_type_preference"] < 0.3:
        notes.append(
            f"category {candidate.get('clip_category', '?')} is sub-preferred for {context.edit_type}"
        )
    if breakdown["duration_fit"] < 0.5:
        notes.append(
            f"duration {candidate.get('intended_duration_sec', '?')}s outside pacing band "
            f"{shot_duration_band(context.act_pacing)} for {context.act_pacing}"
        )
    if breakdown["energy_zone_fit"] < 0.5:
        notes.append(
            f"category {candidate.get('clip_category', '?')} not affined to {context.energy_zone_label} zone"
        )
    return SlotFitScore(
        composite=round(max(0.0, min(1.0, composite)), 3),
        breakdown=breakdown,
        notes=notes,
    )


def pick_best(
    candidates: list[dict[str, Any]],
    context: SlotContext,
    *,
    weights: dict[str, float] | None = None,
) -> tuple[dict[str, Any], SlotFitScore] | tuple[None, None]:
    """Return the (candidate, score) with the highest composite. None if
    candidates is empty."""
    if not candidates:
        return None, None
    scored = [(c, score_candidate(c, context, weights=weights)) for c in candidates]
    scored.sort(key=lambda pair: pair[1].composite, reverse=True)
    return scored[0]


def find_act_for_time(acts: list[dict[str, Any]], time_sec: float) -> dict[str, Any] | None:
    """Helper: locate the act dict covering a given timeline timestamp."""
    for a in acts:
        if float(a["start_sec"]) <= time_sec < float(a["end_sec"]):
            return a
    if acts and time_sec >= float(acts[-1]["end_sec"]):
        return acts[-1]
    return None


def find_zone_for_time(zones: list[dict[str, Any]], time_sec: float) -> str:
    """Helper: locate the energy-zone label at a given timestamp.
    Returns 'mid' as a safe fallback when zone data is unavailable."""
    for z in zones or []:
        if float(z.get("start_sec", 0)) <= time_sec < float(z.get("end_sec", 0)):
            return z.get("label", "mid")
    return "mid"


def build_context(
    *,
    edit_plan: dict[str, Any],
    intent: dict[str, Any],
    energy_zones: dict[str, Any] | None,
    slot_time_sec: float,
    slot_duration_sec: float,
    prev_shot: dict[str, Any] | None = None,
    next_shot: dict[str, Any] | None = None,
) -> SlotContext:
    """Convenience constructor — pulls act + zone + tone fields from the
    canonical artifacts so callers don't have to assemble the context dict
    by hand."""
    acts = edit_plan.get("acts") or []
    act = find_act_for_time(acts, slot_time_sec)
    zone_label = find_zone_for_time((energy_zones or {}).get("zones") or [], slot_time_sec)
    return SlotContext(
        act_index=int(act["number"]) if act else 1,
        act_pacing=str(act.get("pacing", "medium")) if act else "medium",
        act_energy_target=float(act.get("energy_target", 50)) if act else 50.0,
        act_arc_role=str(act.get("arc_role", "setup")) if act else "setup",
        slot_time_sec=slot_time_sec,
        slot_duration_sec=slot_duration_sec,
        energy_zone_label=zone_label,
        edit_type=str(intent.get("edit_type") or "action"),
        tone_target=list(intent.get("tone_vector") or [0.0] * 8),
        prev_shot=prev_shot,
        next_shot=next_shot,
    )


__all__ = [
    "DEFAULT_WEIGHTS",
    "SlotContext",
    "SlotFitScore",
    "score_candidate",
    "pick_best",
    "find_act_for_time",
    "find_zone_for_time",
    "build_context",
]
