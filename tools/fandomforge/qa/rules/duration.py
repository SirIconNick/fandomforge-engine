"""qa.duration — shot-list total duration must match song duration ±0.5s."""

from __future__ import annotations

from fandomforge.qa.gate import GateContext, RuleResult, rule


TOLERANCE_SEC = 0.5


@rule("qa.duration", "Duration math", level="block")
def rule_duration(ctx: GateContext) -> RuleResult:
    if not ctx.shot_list or not ctx.beat_map:
        return RuleResult(
            id="qa.duration", name="Duration math", level="block",
            status="skipped", message="shot-list or beat-map missing",
        )

    fps = int(ctx.shot_list["fps"])
    total_frames = sum(int(s["duration_frames"]) for s in ctx.shot_list["shots"])
    shot_total_sec = total_frames / fps
    song_sec = float(ctx.beat_map["duration_sec"])
    delta = shot_total_sec - song_sec

    if abs(delta) > TOLERANCE_SEC:
        return RuleResult(
            id="qa.duration", name="Duration math", level="block",
            status="fail",
            message=f"shot-list total {shot_total_sec:.2f}s deviates from song {song_sec:.2f}s by {delta:+.2f}s (limit {TOLERANCE_SEC}s)",
            evidence={
                "shot_total_sec": round(shot_total_sec, 3),
                "song_duration_sec": round(song_sec, 3),
                "delta_sec": round(delta, 3),
                "tolerance_sec": TOLERANCE_SEC,
            },
        )

    return RuleResult(
        id="qa.duration", name="Duration math", level="block",
        status="pass",
        message=f"shot-list total {shot_total_sec:.2f}s within {TOLERANCE_SEC}s of song {song_sec:.2f}s",
        evidence={
            "shot_total_sec": round(shot_total_sec, 3),
            "song_duration_sec": round(song_sec, 3),
            "delta_sec": round(delta, 3),
        },
    )
