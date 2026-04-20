"""qa.dialogue_overlap — dialogue shots under unrelated VO get a 1.2s cap.

Per NOTES.md:89 — dialogue shots need deprioritization and a 1.2s duration cap
under unrelated voiceover. This rule scans the shot-list for shots flagged
with mood 'dialogue' or for characters talking while another dialogue layer
is active, and flags any shot that exceeds the cap.

Phase 4.10: applies_to=["dialogue_narrative"] — only dialogue-driven edits
mix dialogue over VO. Action / dance / tribute edits generally strip or
duck source audio, so the cap doesn't apply there.
"""

from __future__ import annotations

from fandomforge.qa.gate import GateContext, RuleResult, rule


DIALOGUE_CAP_SEC = 1.2


def _shot_has_dialogue(shot: dict) -> bool:
    """A shot is considered a 'dialogue' shot if any mood tag says so."""
    moods = [m.lower() for m in shot.get("mood_tags", [])]
    return any(m in {"dialogue", "speaking", "monologue", "vo"} for m in moods)


@rule("qa.dialogue_overlap", "Dialogue overlap cap", level="warn",
      applies_to=["dialogue_narrative"])
def rule_dialogue_overlap(ctx: GateContext) -> RuleResult:
    if not ctx.shot_list:
        return RuleResult(
            id="qa.dialogue_overlap", name="Dialogue overlap cap", level="warn",
            status="skipped", message="no shot-list.json",
        )
    fps = int(ctx.shot_list["fps"])

    overruns: list[dict[str, object]] = []
    for shot in ctx.shot_list["shots"]:
        if not _shot_has_dialogue(shot):
            continue
        dur_sec = int(shot["duration_frames"]) / fps
        if dur_sec > DIALOGUE_CAP_SEC:
            overruns.append({
                "shot_id": shot["id"],
                "duration_sec": round(dur_sec, 3),
                "cap_sec": DIALOGUE_CAP_SEC,
            })

    if overruns:
        return RuleResult(
            id="qa.dialogue_overlap", name="Dialogue overlap cap", level="warn",
            status="warn",
            message=f"{len(overruns)} dialogue shot(s) exceed the {DIALOGUE_CAP_SEC}s cap",
            evidence={"overruns": overruns[:25], "cap_sec": DIALOGUE_CAP_SEC},
        )

    return RuleResult(
        id="qa.dialogue_overlap", name="Dialogue overlap cap", level="warn",
        status="pass", message="no dialogue shots exceed the cap",
    )
