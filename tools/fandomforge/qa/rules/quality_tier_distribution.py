"""qa.quality_tier_distribution — guard against >20% D-tier shots in the
final selection. Phase 3.8 rule.

Reads source-profiles to map source_id → quality_tier, then tallies the
distribution across shot-list shots. Warns when D-tier exceeds 20% of
the total, fails when it exceeds 40%.
"""

from __future__ import annotations

import json

from fandomforge.qa.gate import GateContext, RuleResult, rule

WARN_PCT = 20.0
FAIL_PCT = 40.0


def _load_tier_map(ctx: GateContext) -> dict[str, str]:
    profiles_dir = ctx.project_dir / "data" / "source-profiles"
    if not profiles_dir.exists():
        return {}
    tiers: dict[str, str] = {}
    for p in profiles_dir.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            sid = data.get("source_id")
            tier = data.get("quality_tier")
            if sid and tier:
                tiers[sid] = tier
        except (json.JSONDecodeError, OSError):
            continue
    return tiers


@rule("qa.quality_tier_distribution", "D-tier shot share", level="warn")
def rule_quality_tier_distribution(ctx: GateContext) -> RuleResult:
    if not ctx.shot_list:
        return RuleResult(
            id="qa.quality_tier_distribution", name="", level="",
            status="skipped", message="no shot-list",
        )
    tier_map = _load_tier_map(ctx)
    if not tier_map:
        return RuleResult(
            id="qa.quality_tier_distribution", name="", level="",
            status="skipped",
            message="no source-profiles available — run autopilot to generate",
        )
    shots = ctx.shot_list.get("shots") or []
    if not shots:
        return RuleResult(
            id="qa.quality_tier_distribution", name="", level="",
            status="skipped", message="empty shot list",
        )
    counts = {"S": 0, "A": 0, "B": 0, "C": 0, "D": 0, "?": 0}
    for s in shots:
        sid = s.get("source_id")
        tier = tier_map.get(sid, "?")
        counts[tier if tier in counts else "?"] += 1

    n = len(shots)
    d_pct = counts["D"] / n * 100.0
    distribution_pct = {k: round(v / n * 100, 1) for k, v in counts.items() if v > 0}

    if d_pct >= FAIL_PCT:
        status = "fail"
    elif d_pct >= WARN_PCT:
        status = "warn"
    else:
        status = "pass"
    msg = f"D-tier share {d_pct:.1f}% (warn>={WARN_PCT}, fail>={FAIL_PCT})"
    if status == "pass":
        msg += " — within limits"
    return RuleResult(
        id="qa.quality_tier_distribution", name="", level="",
        status=status,
        message=msg,
        evidence={
            "distribution_pct": distribution_pct,
            "d_tier_pct": round(d_pct, 1),
            "warn_threshold": WARN_PCT, "fail_threshold": FAIL_PCT,
        },
    )
