"""qa.safe_area — every shot must carry safe_area_ok=true for the target platform.

This rule is conservative: we trust the `safe_area_ok` flag produced by the
shot matcher / editor. If it's false (or missing for a platform that has strict
safe-area requirements like TikTok/Reels/Shorts), the shot blocks export.
"""

from __future__ import annotations

from fandomforge.qa.gate import GateContext, RuleResult, rule


STRICT_PLATFORMS = {"tiktok", "reels", "shorts"}


@rule("qa.safe_area", "Safe-area compliance", level="block")
def rule_safe_area(ctx: GateContext) -> RuleResult:
    if not ctx.shot_list:
        return RuleResult(
            id="qa.safe_area", name="Safe-area compliance", level="block",
            status="skipped", message="no shot-list.json",
        )

    platform = ""
    if ctx.edit_plan:
        platform = str(ctx.edit_plan.get("platform_target", "")).lower()

    bad: list[dict[str, object]] = []
    for shot in ctx.shot_list["shots"]:
        if "safe_area_ok" not in shot:
            if platform in STRICT_PLATFORMS:
                bad.append({"shot_id": shot["id"], "reason": "missing safe_area_ok on strict platform"})
            continue
        if shot["safe_area_ok"] is False:
            bad.append({"shot_id": shot["id"], "reason": "flagged safe_area_ok=false"})

    # Check titles too if we have a title-plan.
    if ctx.title_plan:
        for title in ctx.title_plan["titles"]:
            if title.get("safe_area_ok") is False:
                bad.append({"title_id": title["id"], "reason": "title outside safe area"})

    if bad:
        return RuleResult(
            id="qa.safe_area", name="Safe-area compliance", level="block",
            status="fail",
            message=f"{len(bad)} shot(s)/title(s) fail safe-area",
            evidence={"violations": bad[:25], "platform": platform, "count": len(bad)},
        )
    return RuleResult(
        id="qa.safe_area", name="Safe-area compliance", level="block",
        status="pass", message=f"all shots compliant with {platform or 'master'} safe area",
    )
