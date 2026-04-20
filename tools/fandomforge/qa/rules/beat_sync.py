"""qa.beat_sync — every shot with a beat_sync must land within 2 frames of
its beat. Phase 4.3 also records sync_precision_ms — the avg ms-deviation
across all beat-synced shots, so the review module can use it as a
sub-score even when the rule passes.

Phase 4.10: type_severity makes this rule warn-only for emotional / sad /
tribute / cinematic / dialogue_narrative edits where exact frame-level
beat sync is less important than the felt rhythm.
"""

from __future__ import annotations

from fandomforge.qa.gate import GateContext, RuleResult, rule


FRAME_TOLERANCE = 2
PRECISION_TOLERANCE_MS = 150


@rule("qa.beat_sync", "Beat-sync drift", level="block",
      type_severity={"emotional": "warn", "sad_emotional": "warn",
                     "tribute": "warn", "cinematic": "warn",
                     "dialogue_narrative": "warn"})
def rule_beat_sync(ctx: GateContext) -> RuleResult:
    if not ctx.shot_list:
        return RuleResult(
            id="qa.beat_sync", name="Beat-sync drift", level="block",
            status="skipped", message="no shot-list.json",
        )

    fps = int(ctx.shot_list["fps"])
    frame_ms = 1000.0 / fps
    drifted: list[dict[str, object]] = []
    deviations_ms: list[float] = []
    checked = 0
    for shot in ctx.shot_list["shots"]:
        beat = shot.get("beat_sync")
        if not beat or beat.get("type") == "free":
            continue
        checked += 1
        beat_time_sec = float(beat.get("time_sec", 0.0))
        beat_frame = int(round(beat_time_sec * fps))
        delta = int(shot["start_frame"]) - beat_frame
        deviations_ms.append(abs(delta) * frame_ms)
        if abs(delta) > FRAME_TOLERANCE:
            drifted.append({
                "shot_id": shot["id"],
                "beat_time_sec": beat_time_sec,
                "start_frame": int(shot["start_frame"]),
                "expected_frame": beat_frame,
                "delta_frames": delta,
            })

    if not checked:
        return RuleResult(
            id="qa.beat_sync", name="Beat-sync drift", level="block",
            status="skipped", message="no shots had beat_sync metadata",
        )

    avg_dev_ms = sum(deviations_ms) / len(deviations_ms)
    capped_avg = min(PRECISION_TOLERANCE_MS, avg_dev_ms)
    sync_precision_pct = max(0.0, 100.0 * (1.0 - (capped_avg / PRECISION_TOLERANCE_MS)))

    base_evidence = {
        "checked": checked,
        "sync_precision_ms_avg": round(avg_dev_ms, 1),
        "sync_precision_pct": round(sync_precision_pct, 1),
        "tolerance_frames": FRAME_TOLERANCE,
        "tolerance_ms": PRECISION_TOLERANCE_MS,
    }

    if drifted:
        return RuleResult(
            id="qa.beat_sync", name="Beat-sync drift", level="block",
            status="fail",
            message=(
                f"{len(drifted)} shot(s) drift > {FRAME_TOLERANCE} frames; "
                f"avg precision {avg_dev_ms:.1f}ms ({sync_precision_pct:.0f}% sync_quality)"
            ),
            evidence={**base_evidence, "drifted": drifted[:25], "count": len(drifted)},
        )
    return RuleResult(
        id="qa.beat_sync", name="Beat-sync drift", level="block",
        status="pass",
        message=(
            f"all {checked} beat-synced shots within ±{FRAME_TOLERANCE} frames "
            f"(avg {avg_dev_ms:.1f}ms, {sync_precision_pct:.0f}% sync_quality)"
        ),
        evidence=base_evidence,
    )
