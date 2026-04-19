"""Convert a LayeredPlan or EditPlan JSON into the orchestrator-consumable
pair of `shot-list.md` + `dialogue.json` files."""

from __future__ import annotations

import json
from pathlib import Path


def _seconds_to_mmss(sec: float) -> str:
    mm = int(sec) // 60
    ss = sec - (mm * 60)
    return f"{mm:02d}:{ss:05.2f}"


def _timestamp_to_srt(sec: float) -> str:
    hh = int(sec) // 3600
    mm = (int(sec) % 3600) // 60
    ss = int(sec) % 60
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


def convert(plan_json: str | Path, output_md: str | Path) -> Path:
    """Read an EditPlan JSON and write a shot-list.md file the orchestrator accepts."""
    plan_path = Path(plan_json)
    out_path = Path(output_md)
    plan = json.loads(plan_path.read_text())

    lines: list[str] = [
        f"# Edit Plan — generated from {plan_path.name}",
        "",
        "Auto-generated from the shot optimizer. Do not edit by hand.",
        "",
        "| # | Time | Dur | Shot | Source | TS | Mood |",
        "|---|------|-----|------|--------|----|----|",
    ]
    for i, shot in enumerate(plan["shots"], start=1):
        num = i
        start = shot["start_time"]
        dur = shot["duration"]
        source = shot["source"]
        ts = _timestamp_to_srt(shot["clip_start_sec"])
        desc = (shot.get("desc", "") or shot.get("intent", ""))[:60]
        mood = shot.get("mood_profile") or shot.get("emotion") or ""
        lines.append(
            f"| {num} | {_seconds_to_mmss(start)} | {dur:.2f} | "
            f"{desc} | {source} | {ts} | {mood} |"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))
    return out_path


def convert_dialogue(
    plan_json: str | Path,
    output_json: str | Path,
    *,
    character: str = "Leon",
    default_duck_db: float = -10.0,
    default_gain_db: float = 10.0,
) -> Path:
    """Produce a dialogue.json that orchestrator's mixer can consume.

    Accepts either an EditPlan (legacy, shot_optimizer) OR a LayeredPlan
    (current, layered_planner). Detects shape automatically.

    Args:
        plan_json: Path to LayeredPlan or EditPlan JSON.
        output_json: Destination file.
        character: Display name for the 'character' field on each cue
            (pulled from project-config by callers). Used only for UI/logging;
            the actual audio comes from the wav file.
    """
    plan = json.loads(Path(plan_json).read_text())
    cues = []

    # LayeredPlan has `dialogue_lines` with `placement_sec` + `wav_path` + `text`.
    if "dialogue_lines" in plan:
        for d in plan["dialogue_lines"]:
            if d.get("placement_sec") is None:
                continue
            wav_path = Path(d["wav_path"])
            cues.append({
                "audio": wav_path.name,
                "start": d["placement_sec"],
                "duration": d["duration_sec"],
                "gain_db": d.get("gain_db", default_gain_db),
                "duck_db": d.get("duck_db", default_duck_db),
                "line": d.get("text", ""),
                "character": d.get("character") or character,
                "source": "layered-plan",
            })
    # Legacy EditPlan shape
    else:
        for p in plan.get("dialogue_placements", []):
            wav_path = Path(p["audio_path"])
            cues.append({
                "audio": wav_path.name,
                "start": p["start_time"],
                "duration": p["duration"],
                "gain_db": p.get("gain_db", default_gain_db),
                "duck_db": p.get("duck_db", default_duck_db),
                "line": p.get("expected_line", ""),
                "character": p.get("character") or character,
                "source": "edit-plan",
            })
    out = Path(output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"note": "Generated from edit plan", "cues": cues}, indent=2))
    return out


def convert_layered(plan_json: str | Path, output_md: str | Path) -> Path:
    """Read a LayeredPlan JSON and write a shot-list.md the orchestrator accepts.

    LayeredPlan shape has `shots` with `start_sec`, `duration_sec`,
    `clip_start_sec`, `clip_end_sec`, `source`, `kind` (sync_anchor/broll),
    `era`, `desc`, `intent`. Different from the legacy EditPlan.
    """
    plan_path = Path(plan_json)
    out_path = Path(output_md)
    plan = json.loads(plan_path.read_text())
    if "shots" not in plan or not plan["shots"]:
        raise ValueError(f"Plan {plan_path} has no shots")

    # Auto-detect: if first shot has 'start_time' -> EditPlan, else LayeredPlan
    first = plan["shots"][0]
    if "start_time" in first and "start_sec" not in first:
        return convert(plan_json, output_md)

    lines: list[str] = [
        f"# Layered Edit Plan — {plan_path.name}",
        "",
        "Dialogue-spine-first plan. Sync anchors + beat-snapped B-roll.",
        "",
        "| # | Time | Dur | Shot | Source | TS | Mood |",
        "|---|------|-----|------|--------|----|----|",
    ]
    for i, shot in enumerate(plan["shots"], start=1):
        start = shot["start_sec"]
        dur = shot["duration_sec"]
        source = shot["source"]
        ts = _timestamp_to_srt(shot["clip_start_sec"])
        desc = (shot.get("desc", "") or shot.get("intent", ""))[:60]
        kind = shot.get("kind", "broll")
        mood = "SYNC" if kind == "sync_anchor" else (
            shot.get("intent", "").replace("b-roll ", "").strip("()") or "calm"
        )
        lines.append(
            f"| {i} | {_seconds_to_mmss(start)} | {dur:.2f} | "
            f"{desc} | {source} | {ts} | {mood} |"
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))
    return out_path
