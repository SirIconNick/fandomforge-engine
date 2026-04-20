"""qa.clip_metadata_coverage — guard against silent regressions in the
Phase 1.3 enrichment pass.

≥90% of shots must have emotional_register, clip_category, and
energy_zone_fit populated. dialogue_clarity_score and lip_sync_confidence
are nullable (no transcript = legitimate null) so they're tracked but
not gated.
"""

from __future__ import annotations

from fandomforge.qa.gate import GateContext, RuleResult, rule

REQUIRED_COVERAGE_PCT = 90.0
REQUIRED_FIELDS = ("emotional_register", "clip_category", "energy_zone_fit")


@rule("qa.clip_metadata_coverage", "Clip-metadata coverage", level="warn")
def rule_clip_metadata_coverage(ctx: GateContext) -> RuleResult:
    if not ctx.shot_list:
        return RuleResult(
            id="qa.clip_metadata_coverage", name="", level="",
            status="skipped", message="no shot-list.json",
        )
    shots = ctx.shot_list.get("shots") or []
    if not shots:
        return RuleResult(
            id="qa.clip_metadata_coverage", name="", level="",
            status="skipped", message="shot-list has no shots",
        )

    n = len(shots)
    coverages: dict[str, float] = {}
    failing: dict[str, float] = {}
    for f in REQUIRED_FIELDS:
        present = sum(
            1 for s in shots
            if f in s and s[f] is not None and s[f] != [] and s[f] != ""
        )
        pct = (present / n) * 100.0
        coverages[f] = round(pct, 1)
        if pct < REQUIRED_COVERAGE_PCT:
            failing[f] = round(pct, 1)

    # Nullable fields — surface as evidence only
    nullable_coverage: dict[str, float] = {}
    for f in ("dialogue_clarity_score", "lip_sync_confidence", "audio_type", "visual_style"):
        present = sum(
            1 for s in shots
            if f in s and s[f] is not None
        )
        nullable_coverage[f] = round((present / n) * 100.0, 1)

    if failing:
        return RuleResult(
            id="qa.clip_metadata_coverage", name="", level="",
            status="warn",
            message=(
                f"clip-metadata coverage below {REQUIRED_COVERAGE_PCT:.0f}% on: "
                + ", ".join(f"{k}={v}%" for k, v in failing.items())
                + ". Run `ff` autopilot to refresh enrichment."
            ),
            evidence={
                "required_pct": REQUIRED_COVERAGE_PCT,
                "coverage": coverages,
                "nullable_coverage": nullable_coverage,
                "total_shots": n,
            },
        )

    return RuleResult(
        id="qa.clip_metadata_coverage", name="", level="",
        status="pass",
        message=(
            f"all required Phase 1.3 fields ≥{REQUIRED_COVERAGE_PCT:.0f}% on {n} shots"
        ),
        evidence={
            "coverage": coverages,
            "nullable_coverage": nullable_coverage,
        },
    )
