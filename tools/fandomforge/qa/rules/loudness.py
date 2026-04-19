"""qa.loudness — target LUFS and true-peak ceiling must be physically achievable.

Without running a full render we can't measure actual loudness, so this rule
is a sanity check against the audio-plan. If the music gain + dialogue gain
plan to exceed the true-peak ceiling, block export.
"""

from __future__ import annotations

from fandomforge.qa.gate import GateContext, RuleResult, rule


@rule("qa.loudness", "Loudness targets", level="block")
def rule_loudness(ctx: GateContext) -> RuleResult:
    if not ctx.audio_plan:
        return RuleResult(
            id="qa.loudness", name="Loudness targets", level="block",
            status="skipped", message="no audio-plan.json",
        )

    ap = ctx.audio_plan
    target_lufs = float(ap["target_lufs"])
    ceiling_dbtp = float(ap["true_peak_ceiling_dbtp"])

    # Layer gain_db values are RELATIVE boosts on each stem. They are not
    # absolute dBTP readings. We can still catch clearly broken plans:
    #   - Any layer gain > +6 dB is suspicious (invites clipping)
    #   - Target LUFS outside a reasonable broadcast range
    #   - Ceiling > 0 dBTP is nonsensical
    layers = ap.get("layers", [])
    gains = [float(layer.get("gain_db", 0.0)) for layer in layers]
    max_single = max(gains) if gains else 0.0

    violations: list[str] = []
    if max_single > 6.0:
        violations.append(
            f"layer gain {max_single} dB > +6 dB is likely to clip after summing"
        )
    if target_lufs > -8 or target_lufs < -24:
        violations.append(
            f"target_lufs {target_lufs} outside reasonable broadcast range (-24..-8)"
        )
    if ceiling_dbtp > 0:
        violations.append(
            f"true_peak_ceiling_dbtp {ceiling_dbtp} > 0 dBTP will always clip"
        )

    if violations:
        return RuleResult(
            id="qa.loudness", name="Loudness targets", level="block",
            status="fail",
            message="; ".join(violations),
            evidence={
                "target_lufs": target_lufs,
                "true_peak_ceiling_dbtp": ceiling_dbtp,
                "max_layer_gain_db": max_single,
            },
        )

    return RuleResult(
        id="qa.loudness", name="Loudness targets", level="block",
        status="pass",
        message=f"targets OK: {target_lufs} LUFS, {ceiling_dbtp} dBTP",
        evidence={
            "max_layer_gain_db": max_single,
        },
    )
