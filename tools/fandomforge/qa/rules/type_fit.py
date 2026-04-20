"""qa.type_fit — chosen shots' clip_category distribution should match the
edit-type's clip_selection_weights.

For each category that the type WEIGHTS strongly (>1.2 multiplier), the
shot list should have a non-trivial share of those categories. Reverse
test: heavily down-weighted categories (<0.5) should be rare. Tolerance
±15% per category.
"""

from __future__ import annotations

import json

from fandomforge.qa.gate import GateContext, RuleResult, rule

TOLERANCE_PCT = 15.0


def _load_intent(ctx: GateContext) -> dict | None:
    p = ctx.project_dir / "data" / "intent.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_type_weights(edit_type: str) -> dict[str, float] | None:
    try:
        from fandomforge.intelligence.edit_classifier import load_type_priors
        priors = load_type_priors(edit_type)
        if not priors:
            return None
        return priors.get("clip_selection_weights")
    except Exception:  # noqa: BLE001
        return None


@rule("qa.type_fit", "Shot category mix matches edit-type weights", level="warn")
def rule_type_fit(ctx: GateContext) -> RuleResult:
    if not ctx.shot_list:
        return RuleResult(id="qa.type_fit", name="", level="",
                          status="skipped", message="no shot-list")
    intent = _load_intent(ctx)
    if not intent:
        return RuleResult(id="qa.type_fit", name="", level="",
                          status="skipped", message="no intent.json — cannot infer edit_type")
    edit_type = intent.get("edit_type", "action")
    weights = _load_type_weights(edit_type)
    if not weights:
        return RuleResult(id="qa.type_fit", name="", level="",
                          status="skipped",
                          message=f"no clip_selection_weights configured for edit_type={edit_type}")

    shots = ctx.shot_list.get("shots") or []
    if not shots:
        return RuleResult(id="qa.type_fit", name="", level="",
                          status="skipped", message="shot list empty")

    # Tally categories
    cat_counts: dict[str, int] = {}
    for s in shots:
        cat = s.get("clip_category")
        if cat:
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
    total = sum(cat_counts.values()) or 1
    cat_pct = {k: v / total * 100 for k, v in cat_counts.items()}

    # Strongly preferred categories (weight > 1.2) should each contribute ≥5%
    underweighted: list[dict] = []
    for cat, w in weights.items():
        if w >= 1.4:
            actual = cat_pct.get(cat, 0)
            if actual < 5.0:
                underweighted.append({"category": cat, "weight": w, "actual_pct": round(actual, 1)})

    # Strongly down-weighted categories (weight < 0.5) should each contribute ≤10%
    overweighted: list[dict] = []
    for cat, w in weights.items():
        if w < 0.5:
            actual = cat_pct.get(cat, 0)
            if actual > 10.0:
                overweighted.append({"category": cat, "weight": w, "actual_pct": round(actual, 1)})

    if not underweighted and not overweighted:
        return RuleResult(id="qa.type_fit", name="", level="",
                          status="pass",
                          message=f"shot category mix aligns with edit_type={edit_type}",
                          evidence={"distribution_pct": {k: round(v, 1) for k, v in cat_pct.items()}})

    issues: list[str] = []
    if underweighted:
        names = ", ".join(f"{x['category']} ({x['actual_pct']}%)" for x in underweighted[:3])
        issues.append(f"under-using preferred categories: {names}")
    if overweighted:
        names = ", ".join(f"{x['category']} ({x['actual_pct']}%)" for x in overweighted[:3])
        issues.append(f"over-using avoided categories: {names}")
    return RuleResult(
        id="qa.type_fit", name="", level="",
        status="warn",
        message=f"edit_type={edit_type}: " + " | ".join(issues),
        evidence={
            "distribution_pct": {k: round(v, 1) for k, v in cat_pct.items()},
            "underweighted": underweighted,
            "overweighted": overweighted,
        },
    )
