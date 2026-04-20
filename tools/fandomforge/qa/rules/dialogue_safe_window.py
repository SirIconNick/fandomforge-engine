"""qa.dialogue_safe_window — every dialogue cue must land in a SAFE window.

Reads dialogue-placement-plan.json (Phase 1.2). Pass when every cue is
PLACE@SAFE, warn when any cue is PLACE@RISKY or SHIFTED to land safely,
fail when any cue is REJECTed because no SAFE window was reachable.
"""

from __future__ import annotations

import json

from fandomforge.qa.gate import GateContext, RuleResult, rule


@rule("qa.dialogue_safe_window", "Dialogue lands in SAFE windows", level="block")
def rule_dialogue_safe_window(ctx: GateContext) -> RuleResult:
    plan_path = ctx.project_dir / "data" / "dialogue-placement-plan.json"
    if not plan_path.exists():
        return RuleResult(
            id="qa.dialogue_safe_window", name="", level="",
            status="skipped",
            message="no dialogue-placement-plan.json (no dialogue cues to check)",
        )
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return RuleResult(
            id="qa.dialogue_safe_window", name="", level="",
            status="fail",
            message=f"could not read dialogue-placement-plan.json: {exc}",
        )

    placements = plan.get("placements") or []
    if not placements:
        return RuleResult(
            id="qa.dialogue_safe_window", name="", level="",
            status="skipped", message="placements list empty",
        )

    rejected = [p for p in placements if p.get("decision") == "REJECT"]
    risky = [p for p in placements if p.get("flag_at_placement") == "RISKY"]
    shifted = [p for p in placements if p.get("decision") == "SHIFT"]

    if rejected:
        return RuleResult(
            id="qa.dialogue_safe_window", name="", level="",
            status="fail",
            message=f"{len(rejected)} dialogue cue(s) REJECTED — no SAFE window reachable",
            evidence={
                "rejected": [
                    {
                        "cue_index": p["cue_index"],
                        "requested_start_sec": p["requested_start_sec"],
                        "reason": p["reason"],
                        "suggested_alternative_sec": p.get("suggested_alternative_sec"),
                    } for p in rejected[:10]
                ],
                "risky_count": len(risky),
                "shifted_count": len(shifted),
            },
        )

    if risky:
        return RuleResult(
            id="qa.dialogue_safe_window", name="", level="",
            status="warn",
            message=(
                f"{len(risky)} dialogue cue(s) placed in RISKY windows; "
                f"{len(shifted)} shifted to safer windows"
            ),
            evidence={
                "risky": [
                    {"cue_index": p["cue_index"], "reason": p["reason"]}
                    for p in risky[:10]
                ],
                "shifted_count": len(shifted),
            },
        )

    return RuleResult(
        id="qa.dialogue_safe_window", name="", level="",
        status="pass",
        message=f"all {len(placements)} dialogue cue(s) land in SAFE windows "
                f"({len(shifted)} via shift)",
    )
