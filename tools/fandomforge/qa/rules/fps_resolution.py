"""qa.fps_resolution — shot-list resolution and fps must match edit-plan."""

from __future__ import annotations

from fandomforge.qa.gate import GateContext, RuleResult, rule


@rule("qa.fps_resolution", "FPS / resolution consistency", level="block")
def rule_fps_resolution(ctx: GateContext) -> RuleResult:
    if not ctx.shot_list or not ctx.edit_plan:
        return RuleResult(
            id="qa.fps_resolution", name="FPS / resolution consistency", level="block",
            status="skipped", message="shot-list or edit-plan missing",
        )

    plan_fps = ctx.edit_plan.get("fps")
    plan_res = ctx.edit_plan.get("resolution")
    shot_fps = ctx.shot_list["fps"]
    shot_res = ctx.shot_list["resolution"]

    mismatches: list[str] = []
    if plan_fps is not None and float(plan_fps) != float(shot_fps):
        mismatches.append(f"fps: edit-plan={plan_fps}, shot-list={shot_fps}")
    if plan_res is not None:
        if (plan_res.get("width") != shot_res.get("width")
                or plan_res.get("height") != shot_res.get("height")):
            mismatches.append(
                f"resolution: edit-plan={plan_res}, shot-list={shot_res}"
            )

    if mismatches:
        return RuleResult(
            id="qa.fps_resolution", name="FPS / resolution consistency", level="block",
            status="fail",
            message="; ".join(mismatches),
            evidence={
                "edit_plan_fps": plan_fps,
                "shot_list_fps": shot_fps,
                "edit_plan_resolution": plan_res,
                "shot_list_resolution": shot_res,
            },
        )
    return RuleResult(
        id="qa.fps_resolution", name="FPS / resolution consistency", level="block",
        status="pass",
        message=f"fps={shot_fps}, resolution={shot_res.get('width')}x{shot_res.get('height')}",
    )
