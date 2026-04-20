"""qa.aspect_consistency — too many aspect-ratio changes per 10s window
makes the eye constantly readjust. Phase 3.8 rule.

Reads aspect-plan.json. Counts AR transitions per 10-second sliding
window of the timeline; warns when any window contains more than 2 AR
changes (jarring), passes when all windows are ≤2.
"""

from __future__ import annotations

import json

from fandomforge.qa.gate import GateContext, RuleResult, rule

WINDOW_SEC = 10.0
MAX_AR_CHANGES_PER_WINDOW = 2


@rule("qa.aspect_consistency", "Aspect-ratio change density", level="warn")
def rule_aspect_consistency(ctx: GateContext) -> RuleResult:
    plan_path = ctx.project_dir / "data" / "aspect-plan.json"
    if not plan_path.exists():
        return RuleResult(
            id="qa.aspect_consistency", name="", level="",
            status="skipped", message="no aspect-plan.json (skipping)",
        )
    if not ctx.shot_list:
        return RuleResult(
            id="qa.aspect_consistency", name="", level="",
            status="skipped", message="no shot-list",
        )
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return RuleResult(
            id="qa.aspect_consistency", name="", level="",
            status="fail",
            message=f"could not read aspect-plan.json: {exc}",
        )

    decisions = plan.get("decisions") or []
    fps = float(ctx.shot_list.get("fps") or 24.0)
    shots_by_id = {s["id"]: s for s in (ctx.shot_list.get("shots") or [])}

    # Collect AR transitions with their timestamps
    transitions: list[float] = []
    prev_decision: str | None = None
    for d in decisions:
        cur = d.get("decision")
        sid = d.get("shot_id")
        shot = shots_by_id.get(sid)
        if not shot or cur is None:
            continue
        t = float(shot.get("start_frame", 0)) / fps
        if prev_decision is not None and cur != prev_decision and cur != "none" and prev_decision != "none":
            transitions.append(t)
        prev_decision = cur

    if not transitions:
        return RuleResult(
            id="qa.aspect_consistency", name="", level="",
            status="pass",
            message="no aspect-ratio transitions in timeline",
        )

    # Sliding-window count
    busiest_window = (0.0, 0)  # (window_start, count)
    for start in range(0, int(transitions[-1]) + 1, int(WINDOW_SEC)):
        end = start + WINDOW_SEC
        count = sum(1 for t in transitions if start <= t < end)
        if count > busiest_window[1]:
            busiest_window = (float(start), count)

    if busiest_window[1] > MAX_AR_CHANGES_PER_WINDOW:
        return RuleResult(
            id="qa.aspect_consistency", name="", level="",
            status="warn",
            message=(
                f"{busiest_window[1]} AR changes in 10s window starting "
                f"@{busiest_window[0]:.1f}s — eye won't settle"
            ),
            evidence={
                "total_ar_changes": len(transitions),
                "busiest_window_sec": busiest_window[0],
                "busiest_window_count": busiest_window[1],
                "max_per_window": MAX_AR_CHANGES_PER_WINDOW,
            },
        )

    return RuleResult(
        id="qa.aspect_consistency", name="", level="",
        status="pass",
        message=f"{len(transitions)} AR transitions, busiest 10s window has {busiest_window[1]}",
        evidence={"total_ar_changes": len(transitions),
                  "busiest_window_count": busiest_window[1]},
    )
