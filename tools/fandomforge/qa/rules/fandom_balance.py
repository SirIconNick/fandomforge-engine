"""qa.fandom_balance — per-act fandom shares must be within tolerance of plan."""

from __future__ import annotations

from fandomforge.qa.gate import GateContext, RuleResult, rule


SHARE_TOLERANCE = 0.15  # +/- 15 percentage points per fandom per act


@rule("qa.fandom_balance", "Fandom balance", level="warn")
def rule_fandom_balance(ctx: GateContext) -> RuleResult:
    """Warn-level (not block) because slight drift is often an intentional
    editorial call. QA gate records the actual vs. target shares so the
    dashboard can surface the delta."""
    if not ctx.shot_list or not ctx.edit_plan:
        return RuleResult(
            id="qa.fandom_balance", name="Fandom balance", level="warn",
            status="skipped", message="shot-list or edit-plan missing",
        )

    # Group shots by act.
    per_act: dict[int, dict[str, int]] = {}
    for shot in ctx.shot_list["shots"]:
        act = int(shot["act"])
        fandom = shot.get("fandom", "Unknown")
        per_act.setdefault(act, {})
        per_act[act][fandom] = per_act[act].get(fandom, 0) + 1

    violations: list[dict[str, object]] = []
    for act in ctx.edit_plan["acts"]:
        target = act.get("fandom_focus") or {}
        if not target:
            continue
        act_num = int(act["number"])
        counts = per_act.get(act_num, {})
        total = sum(counts.values()) or 1
        actual = {k: v / total for k, v in counts.items()}
        for fandom, target_share in target.items():
            got = actual.get(fandom, 0.0)
            delta = got - target_share
            if abs(delta) > SHARE_TOLERANCE:
                violations.append({
                    "act": act_num,
                    "fandom": fandom,
                    "target": round(target_share, 3),
                    "actual": round(got, 3),
                    "delta": round(delta, 3),
                })

    if violations:
        return RuleResult(
            id="qa.fandom_balance", name="Fandom balance", level="warn",
            status="warn",
            message=f"{len(violations)} per-act fandom share(s) off target by > {SHARE_TOLERANCE}",
            evidence={"violations": violations[:25], "tolerance": SHARE_TOLERANCE},
        )

    return RuleResult(
        id="qa.fandom_balance", name="Fandom balance", level="warn",
        status="pass", message="per-act fandom shares within tolerance",
    )
