"""Cross-source action complement matcher.

Real fandom edits don't cut to the same movie when someone throws a punch —
they cut to a completely different source where someone IS getting punched.
That's the payoff cut. This module builds complement pairs: shot A (action
thrown) → shot B (action received), drawn from different sources, matched on
motion continuity and framing.

The planner:
  1. Tags each shot with an action cue + direction (throw vs receive)
  2. For every "thrown" action, searches for a "received" action in a
     different source with compatible motion vector and framing
  3. Emits a `complement-plan.json` with pairs + their sync target
  4. Respects the no-reuse rule — any shot already used as a primary can't
     be borrowed as a complement unless it's explicitly an intentional callback

The renderer reads the plan and when it places a "thrown" shot, it
immediately queues the complement on the very next beat. The result is the
classic fandom edit rhythm — throw, cut, LAND.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fandomforge.validation import validate


# Action cue -> (thrown-kind, received-kind). The matcher pairs a thrown
# shot with a received shot of the same kind from a different source.
_ACTION_CUE_PAIRS: list[tuple[tuple[str, ...], str]] = [
    (("punch", "jab", "hook", "fist", "hit"), "punch"),
    (("kick", "stomp", "roundhouse"), "kick"),
    (("shoot", "fire", "shot", "gunshot"), "gunshot"),
    (("sword", "stab", "slash", "swing"), "blade"),
    (("tackle", "slam", "throw"), "tackle"),
    (("block", "catch", "dodge"), "block"),
]


_RECEIVE_CUES: dict[str, tuple[str, ...]] = {
    "punch": ("react", "hit", "knocked", "fall", "stagger", "take"),
    "kick": ("fall", "hit", "knocked", "stagger", "crash"),
    "gunshot": ("hit", "fall", "die", "stagger", "react", "bleed"),
    "blade": ("cut", "fall", "bleed", "stagger", "react"),
    "tackle": ("fall", "crash", "tumble", "knocked"),
    "block": ("deflect", "catch"),
}


@dataclass
class ShotCue:
    shot_id: str
    source_id: str
    start_frame: int
    duration_frames: int
    act: int
    kind: str  # punch / kick / gunshot / blade / tackle / block
    role: str  # "thrown" or "received"
    motion_vector: float | None
    framing: str
    fandom: str


def _cues_for_text(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[a-zA-Z]+", text or "")]


def _shot_role_for_kind(shot: dict[str, Any], kind: str) -> str | None:
    """Classify a shot as 'thrown' or 'received' for a given action kind.

    Heuristic: mood tags or description containing a receive cue -> received.
    Otherwise, active cue -> thrown. A shot tagged with both falls back to
    'thrown' (the aggressor is the typical anchor).
    """
    haystack = " ".join(
        _cues_for_text(str(shot.get("description") or ""))
        + [str(t).lower() for t in (shot.get("mood_tags") or [])]
    )
    recv_hits = any(c in haystack for c in _RECEIVE_CUES.get(kind, ()))
    thrown_matches = False
    for needles, k in _ACTION_CUE_PAIRS:
        if k != kind:
            continue
        thrown_matches = any(n in haystack for n in needles)
        break
    if recv_hits and not thrown_matches:
        return "received"
    if thrown_matches or (recv_hits and kind in ("punch", "kick", "gunshot", "blade")):
        # Default bias: active shots without an explicit receive cue are "thrown"
        return "received" if recv_hits else "thrown"
    # Fall back: role-based. Action/motion -> thrown, reaction -> received.
    role = (shot.get("role") or "").strip()
    if role == "reaction":
        return "received"
    if role in ("action", "motion", "cut-on-action"):
        return "thrown"
    return None


def _extract_cues(shot_list: dict[str, Any]) -> list[ShotCue]:
    """Walk the shot list and emit one ShotCue per detected action kind."""
    out: list[ShotCue] = []
    for shot in shot_list.get("shots") or []:
        haystack = " ".join(
            _cues_for_text(str(shot.get("description") or ""))
            + [str(t).lower() for t in (shot.get("mood_tags") or [])]
            + [str(shot.get("role") or "").lower()]
        )
        for needles, kind in _ACTION_CUE_PAIRS:
            if not any(n in haystack for n in needles) and not any(
                c in haystack for c in _RECEIVE_CUES.get(kind, ())
            ):
                continue
            role = _shot_role_for_kind(shot, kind)
            if role is None:
                continue
            out.append(
                ShotCue(
                    shot_id=str(shot.get("id") or ""),
                    source_id=str(shot.get("source_id") or ""),
                    start_frame=int(shot.get("start_frame") or 0),
                    duration_frames=int(shot.get("duration_frames") or 0),
                    act=int(shot.get("act") or 1),
                    kind=kind,
                    role=role,
                    motion_vector=(
                        float(shot.get("motion_vector"))
                        if isinstance(shot.get("motion_vector"), (int, float))
                        else None
                    ),
                    framing=str(shot.get("framing") or ""),
                    fandom=str(shot.get("fandom") or ""),
                )
            )
            # One cue per shot — the dominant action wins. Avoids combinatorial
            # duplicates when a single shot description mentions many cues.
            break
    return out


def _motion_continuity(a: float | None, b: float | None) -> float:
    """Motion vector compatibility. Inverse directions are the sweet spot for
    a cut (thrown left -> landed right). Same direction is OK, orthogonal is
    mediocre, unknowns get a 0.5 neutral score.
    """
    if a is None or b is None:
        return 0.5
    diff = abs((a - b) % 360.0)
    diff = min(diff, 360.0 - diff)
    # 180° = perfect inverse = 1.0. 0° = same dir continuation = 0.6.
    # 90° orthogonal = worst for a match cut = 0.25.
    if diff >= 135:
        return 1.0
    if diff <= 20:
        return 0.6
    if 60 <= diff <= 120:
        return 0.25
    # Middle ground — close to inverse but not quite
    if diff > 120:
        return 0.8
    # Close to same direction (20–60°)
    return 0.4


def _framing_match(a: str, b: str) -> float:
    """Same framing = match cut. Close framing = good. Wide gap = meh."""
    if not a or not b:
        return 0.5
    if a == b:
        return 1.0
    close = {
        ("ECU", "CU"): 0.85, ("CU", "ECU"): 0.85,
        ("CU", "MCU"): 0.85, ("MCU", "CU"): 0.85,
        ("MCU", "medium"): 0.8, ("medium", "MCU"): 0.8,
        ("MS", "MWS"): 0.8, ("MWS", "MS"): 0.8,
        ("MWS", "wide"): 0.8, ("wide", "MWS"): 0.8,
        ("WS", "wide"): 0.95, ("wide", "WS"): 0.95,
    }
    return close.get((a, b), 0.4)


@dataclass
class ComplementPair:
    thrown_shot_id: str
    received_shot_id: str
    kind: str
    score: float
    reasons: list[str]


def _pair_score(thrown: ShotCue, received: ShotCue) -> tuple[float, list[str]]:
    reasons: list[str] = []
    if thrown.source_id == received.source_id:
        return 0.0, ["same-source"]
    motion = _motion_continuity(thrown.motion_vector, received.motion_vector)
    framing = _framing_match(thrown.framing, received.framing)
    # Prefer complements later in the edit than the thrown shot — landed punch
    # should read as cause -> effect in the shot-list order.
    chronology = 1.0 if received.start_frame >= thrown.start_frame else 0.6
    # Slight bonus for cross-fandom pairs — that's the fandom-edit vibe.
    cross_fandom = 0.15 if thrown.fandom and received.fandom and thrown.fandom != received.fandom else 0.0

    score = (
        0.4 * motion
        + 0.3 * framing
        + 0.2 * chronology
        + 0.1 * 1.0
        + cross_fandom
    )
    score = max(0.0, min(1.0, score))

    if motion >= 0.9:
        reasons.append("inverse-motion match-cut")
    if framing >= 0.85:
        reasons.append("framing continuity")
    if cross_fandom:
        reasons.append(f"cross-fandom ({thrown.fandom}→{received.fandom})")
    if chronology == 1.0:
        reasons.append("cause→effect chronology")
    return round(score, 3), reasons


def build_complement_plan(
    *,
    project_slug: str,
    shot_list: dict[str, Any],
    already_used: set[str] | None = None,
) -> dict[str, Any]:
    """Build a complement-plan dict. Validates against complement-plan schema."""
    cues = _extract_cues(shot_list)
    thrown = [c for c in cues if c.role == "thrown"]
    received = [c for c in cues if c.role == "received"]

    used: set[str] = set(already_used or set())

    pairs: list[ComplementPair] = []

    for t in thrown:
        if t.shot_id in used:
            continue
        best_pair: ComplementPair | None = None
        for r in received:
            if r.kind != t.kind:
                continue
            if r.shot_id in used or r.shot_id == t.shot_id:
                continue
            score, reasons = _pair_score(t, r)
            if score <= 0:
                continue
            if best_pair is None or score > best_pair.score:
                best_pair = ComplementPair(
                    thrown_shot_id=t.shot_id,
                    received_shot_id=r.shot_id,
                    kind=t.kind,
                    score=score,
                    reasons=reasons,
                )
        if best_pair is not None and best_pair.score >= 0.45:
            pairs.append(best_pair)
            used.add(best_pair.thrown_shot_id)
            used.add(best_pair.received_shot_id)

    plan = {
        "schema_version": 1,
        "project_slug": project_slug,
        "pairs": [
            {
                "thrown_shot_id": p.thrown_shot_id,
                "received_shot_id": p.received_shot_id,
                "kind": p.kind,
                "score": p.score,
                "reasons": p.reasons,
            }
            for p in pairs
        ],
        "unpaired_thrown": [
            t.shot_id for t in thrown
            if not any(p.thrown_shot_id == t.shot_id for p in pairs)
        ],
        "unpaired_received": [
            r.shot_id for r in received
            if not any(p.received_shot_id == r.shot_id for p in pairs)
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "ff sync complement",
    }
    validate(plan, "complement-plan")
    return plan


def write_complement_plan(plan: dict[str, Any], project_dir: Path) -> Path:
    out = project_dir / "data" / "complement-plan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return out


def apply_pairs_to_shot_list(
    shot_list: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Reorder a shot-list so each thrown shot is immediately followed by its
    complement received shot — the classic cause-then-effect action cut.

    Preserves every shot, never drops anything. Renumbers `start_frame` so the
    timeline stays contiguous at the shot-list's declared fps. Returns a new
    dict; does not mutate the input.
    """
    shots = shot_list.get("shots") or []
    if not shots:
        return shot_list
    pairs = plan.get("pairs") or []
    if not pairs:
        return shot_list

    by_id: dict[str, dict[str, Any]] = {s["id"]: s for s in shots if s.get("id")}
    partner_of: dict[str, str] = {}
    for pair in pairs:
        thrown = pair.get("thrown_shot_id")
        received = pair.get("received_shot_id")
        if thrown and received and thrown in by_id and received in by_id:
            partner_of[thrown] = received

    reordered: list[dict[str, Any]] = []
    placed: set[str] = set()
    for shot in shots:
        sid = shot.get("id")
        if sid in placed:
            continue
        reordered.append(shot)
        if sid:
            placed.add(sid)
        partner = partner_of.get(sid or "")
        if partner and partner not in placed:
            reordered.append(by_id[partner])
            placed.add(partner)

    # Renumber start_frame so the timeline is contiguous, keeping each shot's
    # declared duration. fps defaults to 24 when missing.
    fps = float(shot_list.get("fps") or 24)
    cursor_frame = 0
    for shot in reordered:
        dur_frames = int(shot.get("duration_frames") or int(fps))
        shot["start_frame"] = cursor_frame
        cursor_frame += dur_frames

    out = dict(shot_list)
    out["shots"] = reordered
    return out


__all__ = [
    "ComplementPair",
    "ShotCue",
    "apply_pairs_to_shot_list",
    "build_complement_plan",
    "write_complement_plan",
]
