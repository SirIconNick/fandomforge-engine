"""Per-project credit block generator.

Builds a standardized `credits.md` that lists the song, every source
film/show/game with year, and a short fair-use statement. Written to
`projects/<slug>/data/credits.md` and required by `ff export project`
before any NLE export will succeed.

Source types map to rights-holder language:
    movie   -> "(<year>, rights holder)"
    tv      -> "(<year>, rights holder)"
    anime   -> "(<year>, rights holder)"
    game    -> "(<year>, publisher)"
    short / trailer / other -> generic
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


FAIR_USE_STATEMENT = (
    "This is a non-commercial transformative fan edit. No copyright "
    "infringement is intended. All source material is owned by the "
    "respective rights holders and appears here for the purpose of "
    "criticism, comment, and creative commentary. If you are a rights "
    "holder and want the work taken down, please reach out."
)


@dataclass
class CreditsResult:
    path: Path
    markdown: str
    song_line: str
    source_lines: list[str]


def _song_credit_line(song: dict[str, Any]) -> str:
    title = str(song.get("title", "")).strip()
    artist = str(song.get("artist", "")).strip()
    existing = str(song.get("credit_line", "")).strip()
    if existing:
        return existing
    if title and artist:
        return f"{artist} — {title}"
    return title or artist or "(unknown song)"


def _source_credit_line(source: dict[str, Any]) -> str:
    title = source.get("title") or source.get("id") or "Untitled"
    year = source.get("year")
    stype = source.get("source_type", "other")
    label = {
        "movie": "film",
        "tv": "TV",
        "anime": "anime",
        "game": "game",
        "short": "short",
        "trailer": "trailer",
    }.get(stype, stype)
    year_part = f"({year}, {label})" if year else f"({label})"
    return f"{title} {year_part}"


def generate_credits(
    *,
    edit_plan: dict[str, Any],
    source_catalog: dict[str, Any],
    output_path: Path,
) -> CreditsResult:
    """Produce credits.md for a project. Writes to disk and returns a result."""
    song = edit_plan.get("song") or {}
    song_line = _song_credit_line(song)

    source_lines: list[str] = []
    seen: set[str] = set()
    for src in source_catalog.get("sources", []):
        line = _source_credit_line(src)
        if line in seen:
            continue
        seen.add(line)
        source_lines.append(line)

    slug = edit_plan.get("project_slug", "unknown")
    lines: list[str] = []
    lines.append(f"# {slug} — credits")
    lines.append("")
    lines.append(f"_Generated {datetime.now().isoformat(timespec='seconds')}_")
    lines.append("")
    lines.append("## Song")
    lines.append(f"- {song_line}")
    lines.append("")
    lines.append("## Source material")
    for s in source_lines:
        lines.append(f"- {s}")
    lines.append("")
    lines.append("## Fair use")
    existing_statement = (edit_plan.get("credits") or {}).get("fair_use_statement")
    lines.append(existing_statement or FAIR_USE_STATEMENT)
    lines.append("")

    markdown = "\n".join(lines) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    return CreditsResult(
        path=output_path,
        markdown=markdown,
        song_line=song_line,
        source_lines=source_lines,
    )
