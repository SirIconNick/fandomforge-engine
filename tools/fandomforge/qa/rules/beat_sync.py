"""qa.beat_sync — every shot with a beat_sync must land within 2 frames of its beat."""

from __future__ import annotations

from fandomforge.qa.gate import GateContext, RuleResult, rule


FRAME_TOLERANCE = 2


@rule("qa.beat_sync", "Beat-sync drift", level="block")
def rule_beat_sync(ctx: GateContext) -> RuleResult:
    if not ctx.shot_list:
        return RuleResult(
            id="qa.beat_sync", name="Beat-sync drift", level="block",
            status="skipped", message="no shot-list.json",
        )

    fps = int(ctx.shot_list["fps"])
    drifted: list[dict[str, object]] = []
    checked = 0
    for shot in ctx.shot_list["shots"]:
        beat = shot.get("beat_sync")
        if not beat or beat.get("type") == "free":
            continue
        checked += 1
        beat_time_sec = float(beat.get("time_sec", 0.0))
        beat_frame = int(round(beat_time_sec * fps))
        delta = int(shot["start_frame"]) - beat_frame
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
    if drifted:
        return RuleResult(
            id="qa.beat_sync", name="Beat-sync drift", level="block",
            status="fail",
            message=f"{len(drifted)} shot(s) drift > {FRAME_TOLERANCE} frames from their beat",
            evidence={"drifted": drifted[:25], "count": len(drifted)},
        )
    return RuleResult(
        id="qa.beat_sync", name="Beat-sync drift", level="block",
        status="pass", message=f"all {checked} beat-synced shots within ±{FRAME_TOLERANCE} frames",
    )
