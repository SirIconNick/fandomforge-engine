"""Generate a render-notes.md sidecar: human-readable checklist the editor
sees when they open the project. Includes loudness targets, known cliche
overrides, per-act reminders, QA warnings.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


def write_render_notes(
    *,
    output_path: Path,
    edit_plan: dict[str, Any],
    shot_list: dict[str, Any],
    audio_plan: dict[str, Any] | None,
    qa_report: dict[str, Any] | None,
    nle_name: str,
) -> Path:
    lines: list[str] = []
    slug = edit_plan.get("project_slug", "unknown")
    concept_obj = edit_plan.get("concept") or {}
    concept = concept_obj.get("one_sentence", "")
    theme = concept_obj.get("theme", "")
    song = edit_plan.get("song", {})
    platform = edit_plan.get("platform_target", "master")
    length = edit_plan.get("length_seconds", "?")

    lines.append(f"# {slug} render notes ({nle_name})")
    lines.append("")
    lines.append(f"Generated {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")

    lines.append("## Concept")
    if theme:
        lines.append(f"Theme: **{theme}**")
    lines.append(concept or "_no concept in edit-plan_")
    lines.append("")

    lines.append("## Song + Delivery")
    lines.append(f"- Song: {song.get('title', '?')} by {song.get('artist', '?')}")
    if song.get("credit_line"):
        lines.append(f"- Credit line: {song['credit_line']}")
    lines.append(f"- Platform: {platform}")
    lines.append(f"- Target length: {length}s")
    if edit_plan.get("target_lufs"):
        lines.append(f"- Target loudness: {edit_plan['target_lufs']} LUFS")
    lines.append("")

    if audio_plan:
        lines.append("## Audio plan")
        lines.append(f"- LUFS target: {audio_plan.get('target_lufs')}")
        lines.append(f"- True-peak ceiling: {audio_plan.get('true_peak_ceiling_dbtp')} dBTP")
        lines.append(f"- Song gain: {audio_plan.get('song_gain_db', 0)} dB")
        duck_events = audio_plan.get("duck_events") or []
        if duck_events:
            lines.append(f"- {len(duck_events)} dialogue duck event(s) pre-keyframed. Verify attack/release curves land cleanly.")
        impacts = audio_plan.get("impacts") or []
        if impacts:
            lines.append(f"- {len(impacts)} SFX impact(s) synced to drops. Double-check gain balance.")
        lines.append("")

    # Per-act checklist.
    acts = edit_plan.get("acts") or []
    if acts:
        lines.append("## Act-by-act checklist")
        for act in acts:
            lines.append(f"### Act {act['number']} — {act.get('name', '')}  "
                         f"({act.get('start_sec')}s–{act.get('end_sec')}s)")
            lines.append(f"- Emotional goal: {act.get('emotional_goal', '')}")
            if act.get("key_image"):
                lines.append(f"- Key image: {act['key_image']}")
            if act.get("avoid"):
                lines.append(f"- Avoid: {', '.join(act['avoid'])}")
            if act.get("learning"):
                lines.append(f"- Learning: {act['learning']}")
        lines.append("")

    # Cliche overrides.
    cliches = [s for s in shot_list.get("shots", []) if s.get("cliche_flag")]
    if cliches:
        lines.append("## Cliche overrides in this edit")
        for s in cliches:
            reason = s.get("override_reason") or "(no reason given)"
            lines.append(f"- Shot `{s['id']}` ({s.get('description','')}): {reason}")
        lines.append("")

    # QA warnings.
    if qa_report:
        warns = [r for r in qa_report.get("rules", []) if r["status"] in {"warn", "overridden"}]
        if warns:
            lines.append("## QA notes")
            for r in warns:
                tag = "WARN" if r["status"] == "warn" else "OVERRIDDEN"
                lines.append(f"- [{tag}] {r['name']}: {r.get('message', '')}")
            lines.append("")

    # Generic closer.
    lines.append("## Open the project")
    lines.append(f"Import the {nle_name} file next to this notes document.")
    lines.append("Media is linked via absolute paths. If the NLE prompts to relink,")
    lines.append("point it at the project's `raw/` and `derived/` directories.")
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path
