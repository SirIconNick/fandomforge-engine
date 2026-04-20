"""qa.color_grade_confidence — when the color grader has stamped a
confidence score per clip (Phase 3.3), this rule warns if any clip is
below 0.6 confidence — meaning the auto-grade probably didn't unify
that clip well and a manual Resolve pass is recommended."""

from __future__ import annotations

from fandomforge.qa.gate import GateContext, RuleResult, rule

CONFIDENCE_FLOOR = 0.6


@rule("qa.color_grade_confidence", "Per-shot color grade confidence", level="warn")
def rule_color_grade_confidence(ctx: GateContext) -> RuleResult:
    if not ctx.shot_list:
        return RuleResult(
            id="qa.color_grade_confidence", name="", level="",
            status="skipped", message="no shot-list",
        )
    shots = ctx.shot_list.get("shots") or []
    if not shots:
        return RuleResult(
            id="qa.color_grade_confidence", name="", level="",
            status="skipped", message="empty shot list",
        )

    # Field stamp comes from Phase 3.3 unified-filter pass — until that
    # ships per-shot the field is missing on every shot and we skip.
    has_data = sum(
        1 for s in shots
        if isinstance(s.get("color_grade_confidence"), (int, float))
    )
    if has_data == 0:
        return RuleResult(
            id="qa.color_grade_confidence", name="", level="",
            status="skipped",
            message="no per-shot color_grade_confidence stamped — Phase 3.3 not yet wired into render",
        )

    low: list[dict[str, object]] = []
    scores = []
    for s in shots:
        v = s.get("color_grade_confidence")
        if not isinstance(v, (int, float)):
            continue
        scores.append(float(v))
        if v < CONFIDENCE_FLOOR:
            low.append({"shot_id": s.get("id"), "confidence": round(float(v), 2)})

    if not scores:
        return RuleResult(
            id="qa.color_grade_confidence", name="", level="",
            status="skipped", message="no numeric confidence values found",
        )

    avg = sum(scores) / len(scores)

    if low:
        return RuleResult(
            id="qa.color_grade_confidence", name="", level="",
            status="warn",
            message=(
                f"{len(low)} shot(s) below color-grade confidence {CONFIDENCE_FLOOR} "
                f"(avg {avg:.2f}); recommend manual Resolve pass on these"
            ),
            evidence={"low_confidence_shots": low[:25],
                      "count_low": len(low),
                      "avg_confidence": round(avg, 2),
                      "floor": CONFIDENCE_FLOOR},
        )
    return RuleResult(
        id="qa.color_grade_confidence", name="", level="",
        status="pass",
        message=f"all shots ≥{CONFIDENCE_FLOOR} confidence (avg {avg:.2f})",
        evidence={"avg_confidence": round(avg, 2), "checked": len(scores)},
    )
