"""qa.refs — every shot's source_id must resolve to a source in the catalog."""

from __future__ import annotations

from fandomforge.qa.gate import GateContext, RuleResult, rule


@rule("qa.refs", "Unresolved references", level="block")
def rule_refs(ctx: GateContext) -> RuleResult:
    if not ctx.shot_list:
        return RuleResult(
            id="qa.refs", name="Unresolved references", level="block",
            status="skipped", message="no shot-list.json",
        )
    if not ctx.source_catalog:
        return RuleResult(
            id="qa.refs", name="Unresolved references", level="block",
            status="fail", message="shot-list present but source-catalog missing",
        )

    known_ids = {s["id"] for s in ctx.source_catalog["sources"]}
    unresolved: list[dict[str, str]] = []
    for shot in ctx.shot_list["shots"]:
        if shot["source_id"] not in known_ids:
            unresolved.append({"shot_id": shot["id"], "source_id": shot["source_id"]})

    if unresolved:
        return RuleResult(
            id="qa.refs", name="Unresolved references", level="block",
            status="fail",
            message=f"{len(unresolved)} shot(s) reference sources not in the catalog",
            evidence={"unresolved": unresolved[:25], "count": len(unresolved)},
        )
    return RuleResult(
        id="qa.refs", name="Unresolved references", level="block",
        status="pass", message=f"all {len(ctx.shot_list['shots'])} shots resolve",
    )
