"""qa.emotion_variance — warn when the emotional arc flatlines for too long.

Phase 4.10: sad_emotional edits intentionally sustain a single emotional
register (grief / longing) across long stretches. A 20s "dead zone" in a
sad edit is the whole point, not a flaw. Demoted to info-level there so
it shows up as a note but doesn't count toward warn tallies.
"""

from __future__ import annotations

import json

from fandomforge.qa.gate import GateContext, RuleResult, rule


MIN_DEAD_ZONE_SEC = 20.0
FLAT_TOLERANCE = 0.05


@rule("qa.emotion_variance", "Emotion variance", level="warn",
      type_severity={"sad_emotional": "info"})
def rule_emotion_variance(ctx: GateContext) -> RuleResult:
    arc_path = ctx.project_dir / "data" / "emotion-arc.json"
    if not arc_path.exists():
        return RuleResult(
            id="qa.emotion_variance", name="Emotion variance", level="warn",
            status="skipped",
            message="no emotion-arc.json (run `ff emotion arc`)",
        )

    try:
        arc = json.loads(arc_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return RuleResult(
            id="qa.emotion_variance", name="Emotion variance", level="warn",
            status="skipped",
            message=f"couldn't parse emotion-arc.json: {exc}",
        )

    from fandomforge.intelligence.emotion_arc import detect_dead_zones

    dead = detect_dead_zones(
        arc, min_gap_sec=MIN_DEAD_ZONE_SEC, flat_tolerance=FLAT_TOLERANCE
    )

    if not dead:
        return RuleResult(
            id="qa.emotion_variance", name="Emotion variance", level="warn",
            status="pass",
            message=f"no dead zones longer than {MIN_DEAD_ZONE_SEC:.0f}s across {len(arc.get('samples', []))} samples",
        )

    evidence = {
        "dead_zones": [
            {"start_sec": round(s, 2), "end_sec": round(e, 2), "duration_sec": round(e - s, 2)}
            for s, e in dead
        ],
        "min_gap_sec": MIN_DEAD_ZONE_SEC,
    }
    return RuleResult(
        id="qa.emotion_variance", name="Emotion variance", level="warn",
        status="warn",
        message=(
            f"{len(dead)} emotional dead zone{'s' if len(dead) != 1 else ''} "
            f"longer than {MIN_DEAD_ZONE_SEC:.0f}s — consider varying shot roles or mood tags"
        ),
        evidence=evidence,
    )
