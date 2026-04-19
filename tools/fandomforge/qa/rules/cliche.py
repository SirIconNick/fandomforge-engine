"""qa.cliche — shots flagged cliche_flag=true must carry an override_reason."""

from __future__ import annotations

from fandomforge.qa.gate import GateContext, RuleResult, rule


@rule("qa.cliche", "Cliche shots", level="block")
def rule_cliche(ctx: GateContext) -> RuleResult:
    if not ctx.shot_list:
        return RuleResult(
            id="qa.cliche", name="Cliche shots", level="block",
            status="skipped", message="no shot-list.json",
        )

    flagged: list[dict[str, object]] = []
    overridden: list[dict[str, object]] = []
    for shot in ctx.shot_list["shots"]:
        if not shot.get("cliche_flag"):
            continue
        if shot.get("override_reason"):
            overridden.append({"shot_id": shot["id"], "reason": shot["override_reason"]})
        else:
            flagged.append({
                "shot_id": shot["id"],
                "source_id": shot["source_id"],
                "source_timecode": shot["source_timecode"],
                "description": shot.get("description", ""),
            })

    if flagged:
        return RuleResult(
            id="qa.cliche", name="Cliche shots", level="block",
            status="fail",
            message=f"{len(flagged)} cliche shot(s) need override_reason",
            evidence={
                "flagged": flagged[:20],
                "count": len(flagged),
                "overridden_count": len(overridden),
            },
        )

    msg = "no cliche shots" if not overridden else f"all {len(overridden)} cliche shots carry override reasons"
    return RuleResult(
        id="qa.cliche", name="Cliche shots", level="block",
        status="pass", message=msg,
    )
