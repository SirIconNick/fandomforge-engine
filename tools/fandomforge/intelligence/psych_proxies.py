"""Psychology proxy telemetry (Phase 5.1).

Stores measurable proxies for emotional contagion / parasocial bonding /
beat entrainment / Gestalt unity per render. NOT graded — these are
un-weighted telemetry stored for future correlation against viewer data.

Per amendment A6: must have a read path. `ff psych report <project>`
surfaces the last-N reports + simple trend output. Without a read path
this would be a write-only sink.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HEART_RATE_BANDS = (
    ("resting", 50, 70),
    ("calm", 70, 100),
    ("active", 100, 130),
    ("hype", 130, 160),
    ("frantic", 160, 220),
)


def _heart_rate_band(bpm: float) -> str:
    for label, lo, hi in HEART_RATE_BANDS:
        if lo <= bpm < hi:
            return label
    return "off-band"


def _eyeline_to_camera_pct(shots: list[dict[str, Any]]) -> float:
    if not shots:
        return 0.0
    n = sum(1 for s in shots if s.get("eyeline") == "camera")
    return round(n / len(shots) * 100.0, 1)


def _character_screen_time(shots: list[dict[str, Any]], fps: float) -> dict[str, float]:
    out: dict[str, float] = {}
    for s in shots:
        chars = s.get("characters") or []
        # Fall back to fandom name if no characters tagged
        if not chars and s.get("fandom"):
            chars = [s["fandom"]]
        if not chars:
            continue
        dur_sec = float(s.get("duration_frames", 0)) / max(1.0, fps)
        for c in chars:
            out[c] = round(out.get(c, 0.0) + dur_sec, 2)
    return out


def _color_grouping_pct(shots: list[dict[str, Any]]) -> float:
    """Percent of adjacent shot pairs whose luma delta < 0.15 — Gestalt
    grouping proxy. Reads color_notes for `luma=<n>` values."""
    if len(shots) < 2:
        return 0.0
    lumas: list[float | None] = []
    for s in shots:
        notes = s.get("color_notes") or ""
        if "luma=" in notes:
            try:
                lumas.append(float(notes.split("luma=")[1].split()[0].rstrip(",")))
                continue
            except (IndexError, ValueError):
                pass
        lumas.append(None)
    pairs = [(a, b) for a, b in zip(lumas[:-1], lumas[1:]) if a is not None and b is not None]
    if not pairs:
        return 0.0
    grouped = sum(1 for a, b in pairs if abs(a - b) < 0.15)
    return round(grouped / len(pairs) * 100.0, 1)


def _fandom_diversity(shots: list[dict[str, Any]], fps: float) -> tuple[dict[str, float], float]:
    """Per-fandom screen time + Shannon-entropy diversity index normalized 0-1."""
    times: dict[str, float] = {}
    for s in shots:
        fandom = s.get("fandom")
        if not fandom:
            continue
        dur = float(s.get("duration_frames", 0)) / max(1.0, fps)
        times[fandom] = round(times.get(fandom, 0.0) + dur, 2)
    if not times:
        return {}, 0.0
    total = sum(times.values()) or 1.0
    probs = [t / total for t in times.values()]
    entropy = -sum(p * math.log(p, 2) for p in probs if p > 0)
    max_entropy = math.log(len(times), 2) if len(times) > 1 else 1.0
    diversity = round(entropy / max_entropy, 3) if max_entropy > 0 else 0.0
    return times, diversity


def _beat_sync_pct(shots: list[dict[str, Any]], fps: float, tolerance_frames: int = 2) -> float:
    if not shots:
        return 0.0
    relevant = 0
    aligned = 0
    for s in shots:
        beat = s.get("beat_sync") or {}
        if beat.get("type") in (None, "free"):
            continue
        relevant += 1
        beat_frame = int(round(float(beat.get("time_sec", 0)) * fps))
        if abs(int(s.get("start_frame", 0)) - beat_frame) <= tolerance_frames:
            aligned += 1
    if relevant == 0:
        return 0.0
    return round(aligned / relevant * 100.0, 1)


def build_report(
    project_dir: Path,
    *,
    video_path: Path | None = None,
) -> dict[str, Any]:
    """Construct a psychology-report dict from a project's artifacts."""
    shot_list_path = project_dir / "data" / "shot-list.json"
    beat_map_path = project_dir / "data" / "beat-map.json"
    intent_path = project_dir / "data" / "intent.json"

    shot_list: dict[str, Any] = {}
    if shot_list_path.exists():
        try:
            shot_list = json.loads(shot_list_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            shot_list = {}
    beat_map: dict[str, Any] = {}
    if beat_map_path.exists():
        try:
            beat_map = json.loads(beat_map_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            beat_map = {}
    intent: dict[str, Any] = {}
    if intent_path.exists():
        try:
            intent = json.loads(intent_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            intent = {}

    fps = float(shot_list.get("fps") or 24.0)
    shots = shot_list.get("shots") or []

    char_times = _character_screen_time(shots, fps)
    primary = max(char_times.items(), key=lambda kv: kv[1])[0] if char_times else None
    primary_share = (
        round(char_times[primary] / sum(char_times.values()) * 100, 1)
        if primary and sum(char_times.values()) > 0 else 0.0
    )

    fandom_times, diversity = _fandom_diversity(shots, fps)
    bpm = float(beat_map.get("bpm") or 0)
    beat_sync = _beat_sync_pct(shots, fps)
    eyeline_pct = _eyeline_to_camera_pct(shots)
    color_group = _color_grouping_pct(shots)

    proxies: dict[str, Any] = {
        "parasocial": {
            "character_screen_time_sec": char_times,
            "primary_character": primary or "",
            "primary_character_share_pct": primary_share,
            "eyeline_to_camera_pct": eyeline_pct,
        },
        "beat_entrainment": {
            "song_bpm": bpm,
            "heart_rate_band": _heart_rate_band(bpm),
            "beat_sync_pct": beat_sync,
        },
        "gestalt_unity": {
            "color_grouping_pct": color_group,
            "fandom_diversity_idx": diversity,
        },
        "fandom_reception": {
            "fandom_screen_time_sec": fandom_times,
        },
    }
    # Strip empty fandom_reception when nothing to report
    if not fandom_times:
        del proxies["fandom_reception"]

    report = {
        "schema_version": 1,
        "project_slug": str(project_dir.name),
        "edit_type": str(intent.get("edit_type", "")),
        "proxies": proxies,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "ff psych proxies (Phase 5.1)",
    }
    if video_path is not None:
        report["video_path"] = str(video_path)
    return report


def write_report(report: dict[str, Any], project_dir: Path) -> Path:
    """Append the report to .history/psychology-reports.jsonl AND write the
    most-recent copy to data/psychology-report.json.

    JSONL history powers the trend command; the latest snapshot is the
    canonical 'current state' artifact downstream tools reference.
    """
    from fandomforge.validation import validate_and_write

    out = project_dir / "data" / "psychology-report.json"
    validate_and_write(report, "psychology-report", out)

    # Append history (best-effort; not blocking)
    history = project_dir / ".history" / "psychology-reports.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    with history.open("a", encoding="utf-8") as f:
        f.write(json.dumps(report) + "\n")

    return out


def load_history(project_dir: Path, limit: int = 20) -> list[dict[str, Any]]:
    """Read the last `limit` psych reports from .history/."""
    history = project_dir / ".history" / "psychology-reports.jsonl"
    if not history.exists():
        return []
    lines = history.read_text(encoding="utf-8").strip().split("\n")
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


__all__ = [
    "build_report",
    "write_report",
    "load_history",
]
