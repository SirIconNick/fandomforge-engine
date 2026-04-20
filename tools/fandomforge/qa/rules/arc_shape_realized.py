"""qa.arc_shape_realized — the rendered tension curve must build to climax
and resolve.

Reads tension-curve.json (Phase 2.3). Pass when summary.builds_to_climax
and summary.resolves are both true. Warn otherwise. Fail when there's
no climax act in the plan at all (genuine misconfiguration).
"""

from __future__ import annotations

import json

from fandomforge.qa.gate import GateContext, RuleResult, rule


@rule("qa.arc_shape_realized", "Arc builds to climax + resolves", level="warn")
def rule_arc_shape_realized(ctx: GateContext) -> RuleResult:
    curve_path = ctx.project_dir / "data" / "tension-curve.json"
    if not curve_path.exists():
        return RuleResult(
            id="qa.arc_shape_realized", name="", level="",
            status="skipped", message="no tension-curve.json (run autopilot to build it)",
        )
    try:
        curve = json.loads(curve_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return RuleResult(
            id="qa.arc_shape_realized", name="", level="",
            status="fail",
            message=f"could not read tension-curve.json: {exc}",
        )
    summary = curve.get("summary") or {}
    samples = curve.get("samples") or []

    has_climax = any(s.get("arc_role") == "climax" for s in samples)
    if not has_climax:
        return RuleResult(
            id="qa.arc_shape_realized", name="", level="",
            status="fail",
            message="no climax act in the tension curve — edit-plan misconfigured",
        )

    builds = bool(summary.get("builds_to_climax"))
    resolves = bool(summary.get("resolves"))
    rms = float(summary.get("rms_delta", 0))

    if builds and resolves:
        return RuleResult(
            id="qa.arc_shape_realized", name="", level="",
            status="pass",
            message=(
                f"tension curve builds to climax and resolves "
                f"(rms_delta={rms:.2f}, peak_actual={summary.get('peak_actual')}, "
                f"@t={summary.get('peak_actual_time_sec')}s)"
            ),
            evidence=summary,
        )

    issues: list[str] = []
    if not builds:
        issues.append("does not build to climax")
    if not resolves:
        issues.append("does not resolve in release act")
    return RuleResult(
        id="qa.arc_shape_realized", name="", level="",
        status="warn",
        message=f"tension curve issues: {'; '.join(issues)}; rms_delta={rms:.2f}",
        evidence=summary,
    )
