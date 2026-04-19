"""Parse markdown shot list tables into structured ShotEntry objects.

Handles two formats seen in FandomForge projects:

1. Leon-badass format:
   | # | Time | Dur | Shot | Source | TS | Mood | Notes |

2. Savages / ensemble format:
   | # | Time | Dur | Hero | Shot | Source | TS |

Strategy: find each table header row, map column names to field indices, and
parse subsequent rows using that mapping until the next header row (or table end).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ShotEntry:
    number: int
    song_time_sec: float
    duration_sec: float
    source_id: str
    source_timestamp: str
    source_timestamp_sec: float | None = None
    hero: str = ""
    description: str = ""
    mood: str = ""
    act: int = 1
    raw_row: str = ""

    def is_placeholder(self) -> bool:
        return self.source_id in {"", "—", "-"} or self.source_timestamp in {"", "—", "-"}


def _parse_time_to_seconds(s: str) -> float | None:
    s = (s or "").strip().strip("~").strip()
    if not s or s in {"—", "-"}:
        return None
    s = s.lstrip("~ ").strip()
    parts = s.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            m, sec = parts
            return int(m) * 60 + float(sec)
        if len(parts) == 3:
            h, m, sec = parts
            return int(h) * 3600 + int(m) * 60 + float(sec)
    except (ValueError, TypeError):
        return None
    return None


def _parse_duration(s: str) -> float:
    s = (s or "").strip()
    if not s or s in {"—", "-"}:
        return 2.5
    try:
        return float(s)
    except ValueError:
        return 2.5


def _parse_shot_number(s: str) -> int | None:
    try:
        return int((s or "").strip())
    except ValueError:
        return None


def _normalize_header(h: str) -> str:
    """Normalize a header cell to a canonical field name."""
    h = h.strip().lower()
    if h in {"#", "no", "num", "number"}:
        return "number"
    if h in {"time", "song time", "t"}:
        return "song_time"
    if h in {"dur", "duration", "length"}:
        return "duration"
    if h in {"role"}:
        return "role"
    if h in {"hero", "character"}:
        return "hero"
    if h in {"shot", "description", "desc"}:
        return "description"
    if h in {"source", "src"}:
        return "source_id"
    if h in {"ts", "timestamp", "source ts", "source timestamp"}:
        return "source_timestamp"
    if h in {"mood"}:
        return "mood"
    if h in {"beat", "sync"}:
        return "beat"
    if h in {"score", "scores"}:
        return "score"
    if h in {"notes", "note"}:
        return "notes"
    return h  # fallback — unknown column


def _is_separator_row(cols: list[str]) -> bool:
    return all(re.match(r"^:?-+:?$", c) or c == "" for c in cols)


def _split_row(line: str) -> list[str]:
    """Split a markdown table row by | but ignore the leading/trailing pipe."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def parse_shot_list(md_path: str | Path) -> list[ShotEntry]:
    """Parse a shot list markdown file into a list of ShotEntry."""
    path = Path(md_path)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Find act boundaries so we can tag each shot with its act number
    act_re = re.compile(r"^##\s+Act\s+(\d+)", re.IGNORECASE)
    act_line_map: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        m = act_re.match(line)
        if m:
            act_line_map.append((i, int(m.group(1))))

    def act_at_line(line_no: int) -> int:
        act = 1
        for start, num in act_line_map:
            if line_no >= start:
                act = num
            else:
                break
        return act

    shots: list[ShotEntry] = []

    # Walk lines, track current header mapping
    header_map: dict[str, int] | None = None

    for line_no, line in enumerate(lines):
        if not line.strip().startswith("|"):
            header_map = None
            continue

        cols = _split_row(line)
        if not cols:
            continue

        # Separator row: means the PREVIOUS line was the header
        if _is_separator_row(cols):
            continue

        # If header_map is None, and this looks like a header row, parse it
        if header_map is None:
            header_candidates = [_normalize_header(c) for c in cols]
            if "number" in header_candidates and "song_time" in header_candidates:
                header_map = {name: idx for idx, name in enumerate(header_candidates)}
            continue

        # Data row: need at least "number" column to be an int
        num_idx = header_map.get("number")
        if num_idx is None or num_idx >= len(cols):
            continue
        shot_num = _parse_shot_number(cols[num_idx])
        if shot_num is None:
            continue

        def col(name: str) -> str:
            idx = header_map.get(name)
            if idx is None or idx >= len(cols):
                return ""
            return cols[idx]

        song_time = _parse_time_to_seconds(col("song_time"))
        if song_time is None:
            continue

        duration = _parse_duration(col("duration"))
        source_id = re.sub(r"[`\s]+", "", col("source_id")).strip()
        source_ts = col("source_timestamp")
        hero = col("hero")
        # If there's no "hero" but there is a "role", use that for consistency
        if not hero:
            hero = col("role")
        description = col("description")
        mood = col("mood")

        ts_sec = _parse_time_to_seconds(source_ts)

        shots.append(
            ShotEntry(
                number=shot_num,
                song_time_sec=song_time,
                duration_sec=duration,
                source_id=source_id,
                source_timestamp=source_ts,
                source_timestamp_sec=ts_sec,
                hero=hero,
                description=description,
                mood=mood,
                act=act_at_line(line_no),
                raw_row=line,
            )
        )

    return shots


def shots_to_dict(shots: list[ShotEntry]) -> dict[str, Any]:
    """Serialize shots list for JSON output."""
    return {
        "shot_count": len(shots),
        "total_duration": sum(s.duration_sec for s in shots),
        "shots": [
            {
                "number": s.number,
                "act": s.act,
                "song_time_sec": s.song_time_sec,
                "duration_sec": s.duration_sec,
                "source_id": s.source_id,
                "source_timestamp": s.source_timestamp,
                "source_timestamp_sec": s.source_timestamp_sec,
                "hero": s.hero,
                "description": s.description,
                "mood": s.mood,
                "is_placeholder": s.is_placeholder(),
            }
            for s in shots
        ],
    }
