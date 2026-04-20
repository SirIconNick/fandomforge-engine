"""Coherence metric (Phase 4.1) — motion/color/eyeline/pace continuity."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CoherenceReport:
    motion_continuity: float = 0.0
    color_continuity: float = 0.0
    eyeline_match: float = 0.0
    pace_continuity: float = 0.0
    composite: float = 0.0
    notes: list[str] = field(default_factory=list)
    samples: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "motion_continuity": round(self.motion_continuity, 1),
            "color_continuity": round(self.color_continuity, 1),
            "eyeline_match": round(self.eyeline_match, 1),
            "pace_continuity": round(self.pace_continuity, 1),
            "composite": round(self.composite, 1),
            "notes": list(self.notes),
            "samples": dict(self.samples),
        }


def _angle_diff(a: float, b: float) -> float:
    return abs(((a - b) + 180) % 360 - 180)


def _motion_continuity(shots: list[dict[str, Any]]) -> tuple[float, int]:
    pairs = 0
    deltas: list[float] = []
    for prev, nxt in zip(shots[:-1], shots[1:]):
        pmv = prev.get("motion_vector")
        nmv = nxt.get("motion_vector")
        if not isinstance(pmv, (int, float)) or not isinstance(nmv, (int, float)):
            continue
        pairs += 1
        diff = _angle_diff(float(pmv), float(nmv))
        deltas.append(1.0 - (diff / 180.0))
    if not deltas:
        return 0.0, 0
    return sum(deltas) / len(deltas) * 100.0, pairs


def _extract_luma(shot: dict[str, Any]) -> float | None:
    notes = shot.get("color_notes") or ""
    if "luma=" in notes:
        try:
            return float(notes.split("luma=")[1].split()[0].rstrip(","))
        except (IndexError, ValueError):
            return None
    if isinstance(shot.get("avg_luma"), (int, float)):
        return float(shot["avg_luma"])
    return None


def _color_continuity(shots: list[dict[str, Any]]) -> tuple[float, int]:
    pairs = 0
    scores: list[float] = []
    for prev, nxt in zip(shots[:-1], shots[1:]):
        a, b = _extract_luma(prev), _extract_luma(nxt)
        if a is None or b is None:
            continue
        pairs += 1
        delta = abs(a - b)
        if delta <= 0.1:
            scores.append(1.0)
        elif delta >= 0.5:
            scores.append(0.0)
        else:
            scores.append(1.0 - (delta - 0.1) / 0.4)
    if not scores:
        return 0.0, 0
    return sum(scores) / len(scores) * 100.0, pairs


def _eyeline_match(shots: list[dict[str, Any]]) -> tuple[float, int]:
    complementary = {("left", "right"), ("right", "left"), ("up", "down"), ("down", "up")}
    pairs = 0
    scores: list[float] = []
    for prev, nxt in zip(shots[:-1], shots[1:]):
        pe = (prev.get("eyeline") or "").strip()
        ne = (nxt.get("eyeline") or "").strip()
        if not pe or not ne or pe == "mixed" or ne == "mixed":
            continue
        pairs += 1
        if pe == ne:
            scores.append(1.0)
        elif (pe, ne) in complementary:
            scores.append(0.85)
        elif pe == "camera" or ne == "camera":
            scores.append(0.5)
        else:
            scores.append(0.0)
    if not scores:
        return 0.0, 0
    return sum(scores) / len(scores) * 100.0, pairs


def _pace_continuity(
    shots: list[dict[str, Any]], fps: float, acts: list[dict[str, Any]] | None,
) -> tuple[float, int]:
    if len(shots) < 2:
        return 0.0, 0

    def _act_for(t: float) -> int | None:
        if not acts:
            return None
        for a in acts:
            if float(a.get("start_sec", 0)) <= t < float(a.get("end_sec", 0)):
                return int(a.get("number", 0))
        return None

    pairs = 0
    scores: list[float] = []
    for prev, nxt in zip(shots[:-1], shots[1:]):
        prev_dur = float(prev.get("duration_frames", 0)) / fps
        nxt_dur = float(nxt.get("duration_frames", 0)) / fps
        if prev_dur <= 0 or nxt_dur <= 0:
            continue
        pairs += 1
        ratio = max(prev_dur / nxt_dur, nxt_dur / prev_dur)
        prev_t = float(prev.get("start_frame", 0)) / fps
        nxt_t = float(nxt.get("start_frame", 0)) / fps
        if _act_for(prev_t) != _act_for(nxt_t):
            scores.append(1.0)
            continue
        if ratio < 1.5:
            scores.append(1.0)
        elif ratio < 3.0:
            scores.append(0.7)
        elif ratio < 5.0:
            scores.append(0.4)
        else:
            scores.append(0.0)
    if not scores:
        return 0.0, 0
    return sum(scores) / len(scores) * 100.0, pairs


def score_coherence(
    shot_list: dict[str, Any],
    edit_plan: dict[str, Any] | None = None,
) -> CoherenceReport:
    shots = (shot_list or {}).get("shots") or []
    fps = float((shot_list or {}).get("fps") or 24.0)
    acts = (edit_plan or {}).get("acts") or []

    motion_score, motion_pairs = _motion_continuity(shots)
    color_score, color_pairs = _color_continuity(shots)
    eyeline_score, eyeline_pairs = _eyeline_match(shots)
    pace_score, pace_pairs = _pace_continuity(shots, fps, acts)

    populated: list[float] = []
    if motion_pairs > 0:
        populated.append(motion_score)
    if color_pairs > 0:
        populated.append(color_score)
    if eyeline_pairs > 0:
        populated.append(eyeline_score)
    if pace_pairs > 0:
        populated.append(pace_score)
    composite = sum(populated) / len(populated) if populated else 0.0

    notes: list[str] = []
    if motion_pairs == 0:
        notes.append("no motion_vector data on adjacent shots — motion_continuity skipped")
    if color_pairs < 2:
        notes.append("not enough luma data on adjacent shots — color_continuity may be noisy")
    if eyeline_pairs == 0:
        notes.append("no eyeline data — eyeline_match skipped")
    if motion_score < 30 and motion_pairs > 5:
        notes.append("motion_continuity below 30 — many adjacent shots have opposing motion")
    if color_score < 40 and color_pairs > 5:
        notes.append("color_continuity below 40 — luma jumps across cuts will read jarring")

    return CoherenceReport(
        motion_continuity=motion_score,
        color_continuity=color_score,
        eyeline_match=eyeline_score,
        pace_continuity=pace_score,
        composite=composite,
        notes=notes,
        samples={
            "motion_pairs": motion_pairs,
            "color_pairs": color_pairs,
            "eyeline_pairs": eyeline_pairs,
            "pace_pairs": pace_pairs,
        },
    )


__all__ = ["CoherenceReport", "score_coherence"]
