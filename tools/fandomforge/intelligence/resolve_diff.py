"""Resolve project diff reader (Phase 7.1).

Reads a DaVinci Resolve `.drp` file (SQLite under the hood) or a Resolve
`.fcpxml` export and diffs it against the engine's original output to
detect what cuts the user changed manually. The diff feeds the prior
updater (Phase 7.2) which nudges shot-role weights, arc shape, and
cliche thresholds toward the user's actual taste.

`.drp` format note: DaVinci Resolve project files are proprietary
SQLite databases. Schema isn't officially documented; we read what we
can (timeline cuts, source-clip references) and document what's not
parseable. The FCPXML path is more reliable since it's a documented
XML format Resolve can also export.
"""

from __future__ import annotations

import json
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TimelineCut:
    """A cut in the user's edited timeline."""
    timeline_position_sec: float
    source_id: str
    source_in_sec: float
    source_out_sec: float
    duration_sec: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeline_position_sec": round(self.timeline_position_sec, 3),
            "source_id": self.source_id,
            "source_in_sec": round(self.source_in_sec, 3),
            "source_out_sec": round(self.source_out_sec, 3),
            "duration_sec": round(self.duration_sec, 3),
        }


@dataclass
class DiffReport:
    """What changed between the engine's original cuts and the user's edited version."""
    project_slug: str
    original_cut_count: int
    edited_cut_count: int
    cuts_added: list[TimelineCut] = field(default_factory=list)
    cuts_removed: list[TimelineCut] = field(default_factory=list)
    cuts_duration_changed: list[dict[str, Any]] = field(default_factory=list)
    cuts_reordered: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_slug": self.project_slug,
            "original_cut_count": self.original_cut_count,
            "edited_cut_count": self.edited_cut_count,
            "cuts_added": [c.to_dict() for c in self.cuts_added],
            "cuts_removed": [c.to_dict() for c in self.cuts_removed],
            "cuts_duration_changed": list(self.cuts_duration_changed),
            "cuts_reordered": list(self.cuts_reordered),
            "notes": list(self.notes),
        }


def parse_fcpxml(path: Path) -> list[TimelineCut]:
    """Read a Final Cut Pro XML (FCPXML) timeline export and return its cuts.
    Resolve exports FCPXML alongside .drp; FCPXML is the more portable format."""
    if not path.exists():
        return []
    cuts: list[TimelineCut] = []
    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except ET.ParseError:
        return cuts

    # FCPXML supports multiple versions; we walk for asset-clip elements
    # which carry start (timeline position) + offset (source in-point) + duration.
    for clip in root.iter():
        tag = clip.tag.split("}")[-1] if "}" in clip.tag else clip.tag
        if tag not in ("clip", "asset-clip", "ref-clip"):
            continue
        start_attr = clip.get("start") or "0s"
        offset_attr = clip.get("offset") or "0s"
        duration_attr = clip.get("duration") or "0s"
        try:
            start = _fcpxml_time_to_sec(start_attr)
            offset = _fcpxml_time_to_sec(offset_attr)
            dur = _fcpxml_time_to_sec(duration_attr)
        except (ValueError, IndexError):
            continue
        ref = clip.get("ref") or clip.get("name") or "unknown"
        cuts.append(TimelineCut(
            timeline_position_sec=offset,
            source_id=ref,
            source_in_sec=start,
            source_out_sec=start + dur,
            duration_sec=dur,
        ))
    return cuts


def _fcpxml_time_to_sec(s: str) -> float:
    """FCPXML times are like '12000/1000s' or '5s'."""
    s = s.strip()
    if s.endswith("s"):
        s = s[:-1]
    if "/" in s:
        n, d = s.split("/", 1)
        return float(n) / float(d)
    return float(s) if s else 0.0


def parse_drp(path: Path) -> list[TimelineCut]:
    """Read a DaVinci Resolve .drp file (SQLite). Schema isn't officially
    documented; we attempt the typical timeline+clip table reads and
    return what we can extract."""
    if not path.exists():
        return []
    cuts: list[TimelineCut] = []
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return cuts
    try:
        # Common Resolve internal table names
        for table_name in ("timeline_clips", "clips", "TimelineItem"):
            try:
                rows = conn.execute(f"SELECT * FROM {table_name}").fetchall()
            except sqlite3.OperationalError:
                continue
            for row in rows:
                try:
                    cuts.append(TimelineCut(
                        timeline_position_sec=float(row["timeline_in"] or 0) / 1000.0,
                        source_id=str(row["clip_name"] or row["source_id"] or "unknown"),
                        source_in_sec=float(row["source_in"] or 0) / 1000.0,
                        source_out_sec=float(row["source_out"] or 0) / 1000.0,
                        duration_sec=float(row["duration"] or 0) / 1000.0,
                    ))
                except (KeyError, ValueError):
                    continue
            if cuts:
                break
    finally:
        conn.close()
    return cuts


def shot_list_to_cuts(shot_list: dict[str, Any]) -> list[TimelineCut]:
    """Convert the engine's shot-list.json to the same TimelineCut shape
    so we can diff them apples-to-apples."""
    cuts: list[TimelineCut] = []
    fps = float(shot_list.get("fps") or 24.0)
    for s in shot_list.get("shots") or []:
        start_frame = int(s.get("start_frame", 0))
        dur_frames = int(s.get("duration_frames", 0))
        # parse source_timecode HH:MM:SS.mmm
        tc = s.get("source_timecode", "0:00:00.000")
        try:
            h, m, sec = tc.split(":")
            src_in = int(h) * 3600 + int(m) * 60 + float(sec)
        except (ValueError, AttributeError):
            src_in = 0.0
        dur_sec = dur_frames / fps
        cuts.append(TimelineCut(
            timeline_position_sec=start_frame / fps,
            source_id=str(s.get("source_id", "")),
            source_in_sec=src_in,
            source_out_sec=src_in + dur_sec,
            duration_sec=dur_sec,
        ))
    return cuts


def diff_cuts(
    original: list[TimelineCut],
    edited: list[TimelineCut],
    *,
    project_slug: str = "",
    duration_tolerance_sec: float = 0.1,
) -> DiffReport:
    """Compare two cut lists. Cuts are matched by (source_id, source_in_sec)
    pairs — order-independent — so reorders can be detected separately
    from add/remove."""
    orig_keys = {(c.source_id, round(c.source_in_sec, 1)): c for c in original}
    edit_keys = {(c.source_id, round(c.source_in_sec, 1)): c for c in edited}

    added = [edit_keys[k] for k in edit_keys.keys() if k not in orig_keys]
    removed = [orig_keys[k] for k in orig_keys.keys() if k not in edit_keys]

    duration_changed: list[dict[str, Any]] = []
    for k in orig_keys:
        if k in edit_keys:
            o, e = orig_keys[k], edit_keys[k]
            if abs(o.duration_sec - e.duration_sec) > duration_tolerance_sec:
                duration_changed.append({
                    "source_id": k[0],
                    "source_in_sec": k[1],
                    "original_duration_sec": round(o.duration_sec, 3),
                    "edited_duration_sec": round(e.duration_sec, 3),
                })

    # Reorder detection: same set of cuts but different timeline order
    reordered: list[dict[str, Any]] = []
    common_keys = orig_keys.keys() & edit_keys.keys()
    if common_keys:
        orig_order = [k for k in orig_keys if k in common_keys]
        edit_order = [k for k in edit_keys if k in common_keys]
        if orig_order != edit_order:
            reordered.append({
                "common_count": len(common_keys),
                "orig_first_5": orig_order[:5],
                "edit_first_5": edit_order[:5],
            })

    notes: list[str] = []
    if added:
        notes.append(f"{len(added)} cut(s) added by user")
    if removed:
        notes.append(f"{len(removed)} cut(s) removed by user")
    if duration_changed:
        notes.append(f"{len(duration_changed)} cut(s) had duration changed")
    if reordered:
        notes.append("cuts were reordered")

    return DiffReport(
        project_slug=project_slug,
        original_cut_count=len(original),
        edited_cut_count=len(edited),
        cuts_added=added,
        cuts_removed=removed,
        cuts_duration_changed=duration_changed,
        cuts_reordered=reordered,
        notes=notes,
    )


def diff_project_against_user_edit(
    project_dir: Path,
    user_edit_path: Path,
) -> DiffReport:
    """High-level: load the engine's shot-list as 'original', the user's
    edited Resolve project as 'edited', return the diff."""
    sl_path = project_dir / "data" / "shot-list.json"
    if not sl_path.exists():
        raise FileNotFoundError(f"no shot-list.json in {project_dir}")
    shot_list = json.loads(sl_path.read_text(encoding="utf-8"))
    original = shot_list_to_cuts(shot_list)

    suffix = user_edit_path.suffix.lower()
    if suffix == ".fcpxml":
        edited = parse_fcpxml(user_edit_path)
    elif suffix == ".drp":
        edited = parse_drp(user_edit_path)
    else:
        raise ValueError(f"unsupported user-edit format {suffix} (use .fcpxml or .drp)")

    return diff_cuts(original, edited, project_slug=str(project_dir.name))


__all__ = [
    "DiffReport",
    "TimelineCut",
    "diff_cuts",
    "diff_project_against_user_edit",
    "parse_drp",
    "parse_fcpxml",
    "shot_list_to_cuts",
]
