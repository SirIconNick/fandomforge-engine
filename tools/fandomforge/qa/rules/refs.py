"""qa.refs — every shot's source_id must resolve to a source in the catalog.

Accepts three match modes between shot.source_id and catalog entries:
  1. Exact match on catalog.id (legacy, blake3 hash style).
  2. Match on the path stem of catalog.path (Phase 0.5.7 alignment —
     shot_proposer emits path stems, source-catalog retains blake3 ids for
     content-addressing; the file stem is the bridge).
  3. Match on catalog.source_name if present.

Falling back across modes prevents false qa.refs failures when the engine's
id scheme is mid-transition across subsystems.
"""

from __future__ import annotations

from pathlib import Path

from fandomforge.qa.gate import GateContext, RuleResult, rule


def _build_resolution_index(catalog: dict) -> set[str]:
    """Every acceptable spelling of a source id, unioned across catalog entries."""
    known: set[str] = set()
    for entry in catalog.get("sources") or []:
        if isinstance(entry.get("id"), str):
            known.add(entry["id"])
        if isinstance(entry.get("source_name"), str):
            known.add(entry["source_name"])
        path = entry.get("path")
        if isinstance(path, str) and path:
            known.add(Path(path).stem)
    return known


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

    known_ids = _build_resolution_index(ctx.source_catalog)
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
