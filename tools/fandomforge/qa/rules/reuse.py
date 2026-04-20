"""qa.reuse — the third+ reuse of the same source+timecode without --allow-reuse.

Phase 4.10: dance_movement + comedy edits often INTENTIONALLY repeat a
signature clip (choreo loop, recurring gag). Demoted to info-level there
so the repeat shows up as a note but doesn't count toward warn tallies.
"""

from __future__ import annotations

from fandomforge.qa.gate import GateContext, RuleResult, rule


REUSE_YELLOW_CARD = 3


@rule("qa.reuse", "Overused shot yellow card", level="warn",
      type_severity={"dance_movement": "info", "comedy": "info"})
def rule_reuse(ctx: GateContext) -> RuleResult:
    if not ctx.shot_list:
        return RuleResult(
            id="qa.reuse", name="Overused shot yellow card", level="warn",
            status="skipped", message="no shot-list.json",
        )

    counts: dict[tuple[str, str], int] = {}
    for shot in ctx.shot_list["shots"]:
        key = (shot["source_id"], shot.get("source_timecode", ""))
        counts[key] = counts.get(key, 0) + 1

    offenders = [
        {"source_id": sid, "source_timecode": tc, "count": n}
        for (sid, tc), n in counts.items()
        if n >= REUSE_YELLOW_CARD
    ]

    if offenders:
        return RuleResult(
            id="qa.reuse", name="Overused shot yellow card", level="warn",
            status="warn",
            message=f"{len(offenders)} clip(s) used {REUSE_YELLOW_CARD}+ times",
            evidence={"offenders": offenders[:25], "threshold": REUSE_YELLOW_CARD},
        )
    return RuleResult(
        id="qa.reuse", name="Overused shot yellow card", level="warn",
        status="pass", message=f"no clip used {REUSE_YELLOW_CARD}+ times",
    )
