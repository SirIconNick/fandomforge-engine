"""Three-act arc architect — populates edit-plan.acts[] from intent + beat-map.

The arc architect's job is to set the *pacing budget* per act so the edit
breathes during low-energy passages and hits fast during drops. Without
this layer the engine averages 0.95s shot durations across the whole
song, which is why renders feel "too fast everywhere."

Per amendment A3: this module is cross-type by construction. Every edit
type uses the same Act schema; per-type behavior comes from a small
template table keyed on edit_type, NOT from hardcoded branches inside
the build function.

Inputs:
  - intent (intent.schema.json) — edit_type, target_duration_sec, tone_vector
  - beat_map (beat-map.schema.json) — drops, buildups, breakdowns, downbeats
  - energy_zones (energy-zones.schema.json, optional) — refines act
    boundaries to align with real low/mid/high energy transitions

Output:
  - list[Act] ready to slot into edit-plan.json acts[]
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class ActTemplate:
    """Pacing/tension/role plan for a single act in an arc template."""
    arc_role: str  # setup | escalation | climax | release | interlude
    pacing: str    # slow | medium | fast | frantic
    tension_target: float  # -1 to +1
    energy_target: float  # 0-100
    duration_pct: float    # share of total runtime (template default; can be
                           # overridden by drop alignment)
    name: str              # display label for this act
    emotional_goal: str    # short description for the Act schema's required field
    beat_targets: list[str] = field(default_factory=list)


# Per-edit-type act templates. Each template is a list of ActTemplate
# whose `duration_pct` values sum to 1.0. Cross-type design: every edit
# type has setup/escalation/climax/release; only the proportions and
# pacing labels differ.
ARC_TEMPLATES: dict[str, list[ActTemplate]] = {
    "action": [
        ActTemplate("setup", "medium", 0.2, 35, 0.20, "Setup", "establish stakes + character"),
        ActTemplate("escalation", "fast", 0.7, 70, 0.50, "Build & Escalation", "stack pressure", beat_targets=["buildup"]),
        ActTemplate("climax", "frantic", 1.0, 95, 0.20, "Climax", "the defining hit", beat_targets=["main_drop"]),
        ActTemplate("release", "medium", -0.2, 50, 0.10, "Release", "exhale + final image"),
    ],
    "emotional": [
        ActTemplate("setup", "slow", -0.1, 25, 0.30, "Quiet Open", "establish the loss / longing"),
        ActTemplate("escalation", "slow", 0.4, 45, 0.35, "Slow Climb", "memory + tension build"),
        ActTemplate("climax", "medium", 0.85, 75, 0.20, "Emotional Peak", "the moment of impact"),
        ActTemplate("release", "slow", -0.4, 35, 0.15, "Resolution", "fade / absence / breath"),
    ],
    "tribute": [
        ActTemplate("setup", "slow", 0.0, 30, 0.25, "Origin", "who they were"),
        ActTemplate("escalation", "medium", 0.5, 60, 0.40, "Journey", "the work / the trials"),
        ActTemplate("climax", "fast", 0.9, 85, 0.20, "Defining Moment", "their peak"),
        ActTemplate("release", "slow", -0.1, 50, 0.15, "Legacy", "what they left behind"),
    ],
    "shipping": [
        ActTemplate("setup", "slow", 0.1, 35, 0.30, "First Contact", "tension between them"),
        ActTemplate("escalation", "medium", 0.5, 60, 0.40, "Pull", "longing + near-misses"),
        ActTemplate("climax", "medium", 0.9, 80, 0.20, "Connection", "the kiss / embrace / promise"),
        ActTemplate("release", "slow", 0.2, 55, 0.10, "Rest", "settled together"),
    ],
    "speed_amv": [
        ActTemplate("setup", "fast", 0.3, 60, 0.10, "Quick Open", "5-second hook"),
        ActTemplate("escalation", "fast", 0.7, 80, 0.30, "Build", "fast cuts toward drop"),
        ActTemplate("climax", "frantic", 1.0, 100, 0.50, "Sustained Drop", "max pace throughout drops"),
        ActTemplate("release", "fast", 0.3, 70, 0.10, "Outro", "one last hard cut"),
    ],
    "cinematic": [
        ActTemplate("setup", "slow", 0.0, 25, 0.30, "Introduction", "lay the world out"),
        ActTemplate("escalation", "medium", 0.4, 50, 0.35, "Development", "complications stack"),
        ActTemplate("climax", "medium", 0.8, 75, 0.20, "Confrontation", "the defining beat"),
        ActTemplate("release", "slow", -0.2, 40, 0.15, "Denouement", "settle the dust"),
    ],
    "comedy": [
        ActTemplate("setup", "medium", 0.2, 40, 0.25, "Premise", "set up the joke"),
        ActTemplate("escalation", "medium", 0.5, 55, 0.30, "Build", "raise the stakes absurdly"),
        ActTemplate("climax", "fast", 0.9, 80, 0.20, "Punchline", "the hit"),
        ActTemplate("interlude", "slow", -0.1, 35, 0.10, "Beat", "let it land"),
        ActTemplate("release", "medium", 0.3, 50, 0.15, "Tag", "callback or topper"),
    ],
    "hype_trailer": [
        ActTemplate("setup", "slow", 0.1, 30, 0.20, "Tease", "hint at scale"),
        ActTemplate("escalation", "fast", 0.7, 75, 0.40, "Reveal Cascade", "rapid escalation"),
        ActTemplate("climax", "frantic", 1.0, 95, 0.30, "Title Drop", "biggest hit"),
        ActTemplate("release", "medium", 0.4, 65, 0.10, "Sting", "logo + release date"),
    ],
    "dialogue_narrative": [
        ActTemplate("setup", "slow", 0.0, 20, 0.25, "Voice In", "first dialogue line lands clean"),
        ActTemplate("escalation", "medium", 0.4, 50, 0.40, "Story Build", "intercut dialogue + visual reinforcement"),
        ActTemplate("climax", "medium", 0.8, 70, 0.20, "Defining Line", "the line that lands the meaning"),
        ActTemplate("release", "slow", -0.1, 40, 0.15, "Resolution", "visual answer to the question posed"),
    ],
    "dance_movement": [
        ActTemplate("setup", "medium", 0.3, 50, 0.15, "Step In", "intro the rhythm"),
        ActTemplate("escalation", "fast", 0.6, 70, 0.30, "Build Steps", "stack movement vocabulary"),
        ActTemplate("climax", "frantic", 0.95, 95, 0.40, "Sustained Groove", "movement-on-beat across entire chorus"),
        ActTemplate("release", "medium", 0.3, 65, 0.15, "Closing Pose", "land on the final beat"),
    ],
    "sad_emotional": [
        ActTemplate("setup", "slow", -0.2, 20, 0.30, "Vacant Open", "establish the absence"),
        ActTemplate("escalation", "slow", 0.3, 35, 0.35, "Memory", "what was"),
        ActTemplate("climax", "slow", 0.7, 60, 0.20, "Realization", "the moment it lands"),
        ActTemplate("release", "slow", -0.5, 25, 0.15, "Empty Frame", "the after"),
    ],
}

# Variable-length math (Phase 2.10 hook) — short edits compress the arc;
# long edits add interludes between acts.
SHORT_EDIT_THRESHOLD_SEC = 25.0
LONG_EDIT_THRESHOLD_SEC = 240.0


def _select_template(edit_type: str) -> list[ActTemplate]:
    """Pick the template list for an edit_type. Falls back to action.
    Returns a fresh list of fresh ActTemplate copies so downstream mutation
    (compress / expand / snap) never bleeds into the module-level table."""
    src = ARC_TEMPLATES.get(edit_type) or ARC_TEMPLATES["action"]
    return [
        ActTemplate(
            arc_role=a.arc_role, pacing=a.pacing,
            tension_target=a.tension_target, energy_target=a.energy_target,
            duration_pct=a.duration_pct, name=a.name,
            emotional_goal=a.emotional_goal, beat_targets=list(a.beat_targets),
        )
        for a in src
    ]


def _compress_for_short(acts: list[ActTemplate], target_duration_sec: float) -> list[ActTemplate]:
    """Sub-25s edits collapse to a single-beat shape: brief setup → climax."""
    # Find the climax act; keep it + a setup beat if there's room
    climax = next((a for a in acts if a.arc_role == "climax"), acts[-1])
    if target_duration_sec <= 12:
        return [
            ActTemplate(
                arc_role="climax", pacing=climax.pacing, tension_target=climax.tension_target,
                energy_target=climax.energy_target, duration_pct=1.0,
                name="Single Beat", emotional_goal="one statement, fast",
            )
        ]
    return [
        ActTemplate(
            arc_role="setup", pacing="medium", tension_target=0.2, energy_target=40,
            duration_pct=0.3, name="Tease", emotional_goal="set the moment",
        ),
        ActTemplate(
            arc_role="climax", pacing=climax.pacing, tension_target=climax.tension_target,
            energy_target=climax.energy_target, duration_pct=0.7,
            name="Hit", emotional_goal=climax.emotional_goal,
        ),
    ]


def _expand_for_long(acts: list[ActTemplate], target_duration_sec: float) -> list[ActTemplate]:
    """4min+ edits get rest beats inserted as interludes between escalation
    and climax so the audience doesn't fatigue."""
    out: list[ActTemplate] = []
    for a in acts:
        out.append(a)
        if a.arc_role == "escalation":
            # Insert a rest interlude
            out.append(ActTemplate(
                arc_role="interlude", pacing="slow", tension_target=0.3,
                energy_target=45, duration_pct=0.05,
                name="Rest", emotional_goal="breathe before climax",
            ))
    # Renormalize duration_pct to sum to 1.0
    total = sum(a.duration_pct for a in out)
    if total > 0:
        for a in out:
            a.duration_pct = a.duration_pct / total
    return out


def _align_act_boundaries_to_beats(
    acts: list[dict[str, Any]],
    drops: list[dict[str, Any]],
    target_duration_sec: float,
) -> list[dict[str, Any]]:
    """Snap act boundaries to the nearest drop or buildup so the climax
    actually falls on the song's biggest hit. Conservative: snaps within
    ±15% of the planned boundary, otherwise keeps the template position.
    """
    if not drops:
        return acts
    drop_times = sorted(float(d.get("time", 0)) for d in drops if isinstance(d.get("time"), (int, float)))
    if not drop_times:
        return acts

    for act in acts:
        if act.get("arc_role") != "climax":
            continue
        planned_start = float(act["start_sec"])
        planned_end = float(act["end_sec"])
        tolerance = (planned_end - planned_start) * 0.30
        # Find the strongest drop in tolerance window (closest to planned_start)
        candidates = [d for d in drop_times if abs(d - planned_start) <= tolerance]
        if not candidates:
            continue
        best = min(candidates, key=lambda d: abs(d - planned_start))
        delta = best - planned_start
        # Apply delta to all subsequent boundaries proportionally so total
        # duration stays the same.
        for a in acts:
            if a["start_sec"] >= planned_start:
                a["start_sec"] = max(0.0, a["start_sec"] + delta)
                a["end_sec"] = min(target_duration_sec, a["end_sec"] + delta)
    # Final clamp + sanity
    if acts:
        acts[-1]["end_sec"] = target_duration_sec
    return acts


def build_acts(
    intent: dict[str, Any],
    *,
    beat_map: dict[str, Any] | None = None,
    energy_zones: dict[str, Any] | None = None,
    target_duration_sec: float | None = None,
) -> list[dict[str, Any]]:
    """Build the acts[] block for an edit-plan.

    Returns a list of dicts ready to slot into `edit_plan["acts"]` and
    pass schema validation.
    """
    edit_type = intent.get("edit_type", "action")
    duration = float(target_duration_sec or intent.get("target_duration_sec") or 60.0)
    if duration <= 0:
        duration = 60.0

    template = _select_template(edit_type)

    if duration <= SHORT_EDIT_THRESHOLD_SEC:
        template = _compress_for_short(template, duration)
    elif duration >= LONG_EDIT_THRESHOLD_SEC:
        template = _expand_for_long(template, duration)

    # Convert ActTemplate → schema-shaped dict, allocating start/end by pct
    acts: list[dict[str, Any]] = []
    cursor = 0.0
    for i, t in enumerate(template, start=1):
        seg_dur = duration * t.duration_pct
        start = cursor
        end = min(duration, cursor + seg_dur)
        acts.append({
            "number": i,
            "name": t.name,
            "start_sec": round(start, 3),
            "end_sec": round(end, 3),
            "energy_target": float(t.energy_target),
            "emotional_goal": t.emotional_goal,
            "pacing": t.pacing,
            "tension_target": float(t.tension_target),
            "arc_role": t.arc_role,
            "beat_targets": list(t.beat_targets),
        })
        cursor = end

    # Snap climax to a drop if available
    if beat_map and beat_map.get("drops"):
        acts = _align_act_boundaries_to_beats(acts, beat_map["drops"], duration)

    # Force last act to land on duration exactly
    if acts:
        acts[-1]["end_sec"] = round(duration, 3)
    return acts


def shot_duration_band(pacing: str) -> tuple[float, float]:
    """Return the (min_sec, max_sec) shot-duration range for a pacing label.

    Used by the slot-fit scorer (Phase 2.2) to know how long shots in this
    act should be. Matches the breathing/breathing/quick/frantic mental
    model: slow = let scenes hold, frantic = sub-half-second cuts on drop.
    """
    return {
        "slow":    (2.0, 4.5),
        "medium":  (1.0, 2.0),
        "fast":    (0.5, 1.0),
        "frantic": (0.25, 0.6),
    }.get(pacing, (1.0, 2.0))


__all__ = [
    "ARC_TEMPLATES",
    "ActTemplate",
    "build_acts",
    "shot_duration_band",
]
