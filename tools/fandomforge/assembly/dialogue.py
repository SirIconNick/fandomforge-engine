"""Dialogue-script parsing and compilation.

The dialogue-script.md files in each project have tables of character dialogue
cues that thread through the edit. This module parses those tables into
structured DialogueCue lists, and can also load a clean JSON format directly.

JSON schema:
    {
      "cues": [
        {
          "audio": "leon_top-down.wav",
          "start": 83.0,
          "duration": 3.0,
          "gain_db": 0.0,
          "duck_db": -6.0,
          "character": "Leon",
          "line": "These things always start from the top down.",
          "source": "RE6",
          "visual_note": "plays over 4 rapid face-cuts across eras"
        },
        ...
      ]
    }
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class DialogueEntry:
    """A single dialogue cue — audio clip + timing + visual context."""

    audio_filename: str
    start_sec: float
    duration_sec: float = 3.0
    gain_db: float = 0.0
    duck_db: float = -6.0
    character: str = ""
    line: str = ""
    source: str = ""
    visual_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "audio": self.audio_filename,
            "start": self.start_sec,
            "duration": self.duration_sec,
            "gain_db": self.gain_db,
            "duck_db": self.duck_db,
            "character": self.character,
            "line": self.line,
            "source": self.source,
            "visual_note": self.visual_note,
        }


def _parse_time(s: str) -> float | None:
    """Parse 'MM:SS', 'HH:MM:SS', 'SS.ss', 'MM:SS.sss' into seconds."""
    s = (s or "").strip().strip("*").strip()
    if not s or s in {"—", "-"}:
        return None
    # Strip bold markers, spaces
    s = re.sub(r"[*`_~]", "", s).strip()
    parts = s.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except (ValueError, TypeError):
        return None
    return None


def _parse_time_range(s: str) -> tuple[float | None, float | None]:
    """Parse '0:48–0:52' or '0:48-0:52' into (start, end) seconds."""
    if not s:
        return (None, None)
    s = s.strip().strip("*").strip()
    # Handle em dash, en dash, regular dash
    for sep in ("—", "–", "-"):
        if sep in s:
            parts = s.split(sep, 1)
            if len(parts) == 2:
                return (_parse_time(parts[0]), _parse_time(parts[1]))
    # Single time
    t = _parse_time(s)
    return (t, None)


def _guess_audio_filename(character: str, line: str, used_names: set[str]) -> str:
    """Given a character + line, make up a reasonable audio filename."""
    char = re.sub(r"[^a-zA-Z]", "", character.lower()) or "dialogue"
    # Take a few significant words from the line
    words = re.findall(r"\b[a-zA-Z']+\b", line.lower())
    stopwords = {"the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "at", "for",
                 "is", "it", "i", "you", "he", "she", "we", "they", "be", "been", "this",
                 "that", "are", "was", "were", "will", "should", "can"}
    significant = [w for w in words if w not in stopwords and len(w) > 2][:3]
    slug = "-".join(significant) or "line"
    # Normalize apostrophes
    slug = slug.replace("'", "")
    base = f"{char}_{slug}"
    name = base + ".wav"
    i = 2
    while name in used_names:
        name = f"{base}-{i}.wav"
        i += 1
    return name


def parse_dialogue_script(md_path: str | Path) -> list[DialogueEntry]:
    """Parse a dialogue-script.md file into DialogueEntry list.

    Looks for table rows with columns matching either:
      Leon project: | Song time | Voice | Content | Visual source | Visual content | Notes |
      Savages project: | Song time | Voice | Line | Visual source | Visual content | Notes |
    """
    path = Path(md_path)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    entries: list[DialogueEntry] = []
    header_map: dict[str, int] | None = None
    used_names: set[str] = set()

    def _normalize_hdr(h: str) -> str:
        h = h.strip().lower().replace("*", "").replace("`", "")
        if h in {"song time", "time", "song_time"}:
            return "time"
        if h in {"voice", "speaker", "character"}:
            return "voice"
        if h in {"content", "line", "dialogue"}:
            return "line"
        if h in {"visual source", "source"}:
            return "visual_source"
        if h in {"visual content", "visual"}:
            return "visual_content"
        if h in {"notes", "note"}:
            return "notes"
        if h in {"audio", "audio file", "file"}:
            return "audio"
        return h

    def _split_row(ln: str) -> list[str]:
        ln = ln.strip()
        if ln.startswith("|"):
            ln = ln[1:]
        if ln.endswith("|"):
            ln = ln[:-1]
        return [c.strip() for c in ln.split("|")]

    for line in lines:
        if not line.strip().startswith("|"):
            header_map = None
            continue
        cols = _split_row(line)
        if not cols:
            continue
        # Separator row
        if all(re.match(r"^:?-+:?$", c) or c == "" for c in cols):
            continue

        if header_map is None:
            candidates = [_normalize_hdr(c) for c in cols]
            if "time" in candidates and "voice" in candidates:
                header_map = {name: i for i, name in enumerate(candidates)}
            continue

        # Data row
        def col(name: str) -> str:
            idx = header_map.get(name)
            if idx is None or idx >= len(cols):
                return ""
            return cols[idx]

        time_cell = col("time")
        voice_cell = col("voice")

        if not time_cell or not voice_cell:
            continue

        start_sec, end_sec = _parse_time_range(time_cell)
        if start_sec is None:
            continue

        duration = (end_sec - start_sec) if end_sec else 3.0
        if duration <= 0:
            duration = 3.0

        # Parse voice cell — "Leon (audio only)" / "Chris" / "USER" / "song" / "silence"
        voice_cell_clean = re.sub(r"[*_`]", "", voice_cell).strip()
        voice_lower = voice_cell_clean.lower()
        # Skip non-dialogue rows
        # - "song" / "silence" / "song + SFX" / "song outro" / "song (CHOIR DROP)" etc
        # - "USER" / "USER VO"
        # - Rows that are ambient / SFX / music events
        skip_keywords = ("song", "silence", "sfx only", "music only")
        if any(voice_lower.startswith(k) or voice_lower == k for k in skip_keywords):
            continue
        if "song" in voice_lower and "(" in voice_cell_clean:
            # Covers "song (CHOIR DROP)", "song (MARINA DROP)" etc.
            continue
        if voice_lower in {"user", "user vo"} or voice_lower.startswith("user"):
            continue
        # Extract character name (everything before first paren)
        char_match = re.match(r"^([^(]+?)(?:\s*\(.*)?$", voice_cell_clean)
        character = char_match.group(1).strip() if char_match else voice_cell_clean
        # Skip non-character tokens
        char_lower = character.lower()
        if char_lower in {"song", "silence", "sfx", "beat", "drop", "music", ""}:
            continue
        # Skip "song + SFX" / "song + anything"
        if " + " in character.lower() and "song" in character.lower():
            continue
        # Skip entries that are really descriptions like "song outro winding down"
        if character.lower().split()[0] in {"song", "silence", "music"}:
            continue

        line_text = col("line")
        # Remove brackets with source info like "[RE6, ~05:00:00]"
        line_clean = re.sub(r"\[[^\]]*\]", "", line_text).strip()
        # Extract source citation: any content inside the first bracket pair.
        # Take everything up to the first comma/semicolon as the source name.
        src_match = re.search(r"\[([^\]]+)\]", line_text)
        if src_match:
            source_cite = re.split(r"[,;]", src_match.group(1))[0].strip()
        else:
            source_cite = ""

        # Determine if this is an audio-only cue vs on-screen sync vs layered
        voice_lower_parts = voice_lower
        is_audio_only = "audio only" in voice_lower_parts or "audio-only" in voice_lower_parts or "game audio" in voice_lower_parts

        # If the row is NOT clearly a dialogue cue (e.g. "song" or ambiguous), skip
        # But if we got a character name and a line, treat it as a dialogue cue
        if not line_clean and not character:
            continue

        # Generate an audio filename
        audio_name = _guess_audio_filename(character, line_clean or "line", used_names)
        used_names.add(audio_name)

        # Gain adjustments for layered moments
        gain_db = 0.0
        if "underlayer" in voice_lower_parts or "low under" in voice_lower_parts:
            gain_db = -6.0

        entries.append(
            DialogueEntry(
                audio_filename=audio_name,
                start_sec=start_sec,
                duration_sec=duration,
                gain_db=gain_db,
                character=character,
                line=line_clean,
                source=source_cite,
                visual_note=col("notes") or col("visual_content"),
            )
        )

    return entries


def entries_to_json(entries: list[DialogueEntry]) -> dict[str, Any]:
    return {"cues": [e.to_dict() for e in entries]}


def load_dialogue_json(path: str | Path) -> list[DialogueEntry]:
    """Load a compiled dialogue-script JSON file back into DialogueEntry objects."""
    with Path(path).open("r") as f:
        data = json.load(f)
    entries: list[DialogueEntry] = []
    for cue in data.get("cues", []):
        entries.append(
            DialogueEntry(
                audio_filename=cue["audio"],
                start_sec=float(cue["start"]),
                duration_sec=float(cue.get("duration", 3.0)),
                gain_db=float(cue.get("gain_db", 0.0)),
                duck_db=float(cue.get("duck_db", -6.0)),
                character=cue.get("character", ""),
                line=cue.get("line", ""),
                source=cue.get("source", ""),
                visual_note=cue.get("visual_note", ""),
            )
        )
    return entries
