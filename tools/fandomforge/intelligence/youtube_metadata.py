"""YouTube metadata generator for fan-edit tribute videos.

Builds a complete publishing package including title, description, tags,
category suggestion, and end-screen timing recommendation.

GPT-4o-mini drafts the text with full context about the edit.  When the
API key is not available the module falls back to template-based generation
so it always produces usable output.

Data structures
---------------
SongInfo            -- song title, artist, year, label
YouTubeMetadata     -- the complete output record

Public API
----------
    from fandomforge.intelligence.youtube_metadata import (
        build_youtube_metadata,
        SongInfo,
        YouTubeMetadata,
    )

    song = SongInfo(title="Centuries", artist="Fall Out Boy", year=2014)
    meta = build_youtube_metadata(
        edit_plan=plan,
        song_info=song,
        character="Leon Kennedy",
        style="cinematic",
    )
    print(meta.title)
    print(meta.description)
"""

from __future__ import annotations

import json
import logging
import os
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fandomforge.intelligence.shot_optimizer import EditPlan

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input / output dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SongInfo:
    """Song metadata for attribution and fair-use statements.

    Attributes:
        title: Song name.
        artist: Performing artist or band.
        year: Release year (int or string).
        label: Record label.  May be empty if unknown.
        duration_sec: Full song duration in seconds.  0 when unknown.
    """

    title: str
    artist: str
    year: int | str = ""
    label: str = ""
    duration_sec: float = 0.0


@dataclass
class YouTubeMetadata:
    """Complete YouTube publishing metadata for a fan edit.

    Attributes:
        title: Video title, 70 characters or fewer.
        description: Full video description (credits, timestamps, fair-use).
        tags: List of 15-20 keyword strings.
        category_id: YouTube category ID as a string (e.g. "10" for Music).
        category_name: Human-readable category name.
        end_screen_start_sec: Recommended timestamp (seconds from video end)
            for placing the YouTube end screen.  Typically ~20 s before the end.
        key_moment_timestamps: List of (timecode_str, label) tuples for the
            description timestamp chapter markers.
        raw_gpt_response: Raw GPT JSON string when GPT was used.  Empty otherwise.
    """

    title: str
    description: str
    tags: list[str]
    category_id: str
    category_name: str
    end_screen_start_sec: float
    key_moment_timestamps: list[tuple[str, str]] = field(default_factory=list)
    raw_gpt_response: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# YouTube category IDs (the ones relevant to fan edits)
_CATEGORIES: dict[str, tuple[str, str]] = {
    "gaming":      ("20", "Gaming"),
    "music":       ("10", "Music"),
    "film":        ("1",  "Film & Animation"),
    "entertainment": ("24", "Entertainment"),
    "default":     ("24", "Entertainment"),
}


def _sec_to_tc(seconds: float) -> str:
    """Convert float seconds to a ``MM:SS`` or ``H:MM:SS`` string."""
    seconds = max(0.0, seconds)
    total_s = int(seconds)
    s = total_s % 60
    m = (total_s // 60) % 60
    h = total_s // 3600
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _load_env_key(project_root: str | Path = ".") -> None:
    """Load OPENAI_API_KEY from .env if not already in the environment."""
    if os.environ.get("OPENAI_API_KEY"):
        return
    for env_name in (".env", ".env.local"):
        env_file = Path(project_root) / env_name
        if not env_file.exists():
            continue
        for raw_line in env_file.read_text().splitlines():
            raw_line = raw_line.strip()
            if raw_line.startswith("#") or "=" not in raw_line:
                continue
            k, v = raw_line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k == "OPENAI_API_KEY" and v:
                os.environ["OPENAI_API_KEY"] = v
                return


def _extract_sources_from_plan(edit_plan: "EditPlan") -> list[str]:
    """Return a deduplicated list of source IDs from an edit plan."""
    sources: list[str] = []
    seen: set[str] = set()

    # EditPlan.shots is a list of ShotRecord
    if hasattr(edit_plan, "shots"):
        for shot in edit_plan.shots:
            src = getattr(shot, "source", "") or getattr(shot, "source_id", "") or ""
            src = str(src).strip()
            if src and src not in seen and src not in {"—", "-"}:
                seen.add(src)
                sources.append(src)

    # EditPlan.metadata.shots_per_source is a dict
    if hasattr(edit_plan, "metadata") and hasattr(edit_plan.metadata, "shots_per_source"):
        for src in edit_plan.metadata.shots_per_source:
            src = str(src).strip()
            if src and src not in seen:
                seen.add(src)
                sources.append(src)

    return sources


def _extract_characters_from_plan(edit_plan: "EditPlan") -> list[str]:
    """Return a deduplicated list of character names from an edit plan."""
    chars: list[str] = []
    seen: set[str] = set()
    if hasattr(edit_plan, "shots"):
        for shot in edit_plan.shots:
            c = getattr(shot, "character_main", "") or ""
            c = str(c).strip()
            if c and c not in seen:
                seen.add(c)
                chars.append(c)
    return chars


def _extract_key_moments(edit_plan: "EditPlan") -> list[tuple[float, str]]:
    """Return a list of (timestamp_sec, label) for major editorial moments.

    Pulls from VOPlacements (dialogue cues) and the big-hit shot time.
    """
    moments: list[tuple[float, str]] = [(0.0, "Intro")]

    if hasattr(edit_plan, "metadata") and hasattr(edit_plan.metadata, "big_hit_time"):
        bh = float(edit_plan.metadata.big_hit_time)
        if bh > 2.0:
            moments.append((bh, "Drop"))

    if hasattr(edit_plan, "dialogue_placements"):
        for vop in edit_plan.dialogue_placements:
            start = float(vop.start_time)
            line = (vop.expected_line or "").strip()[:40]
            if line:
                moments.append((start, f'"{line}"'))

    moments.sort(key=lambda x: x[0])
    # Deduplicate moments that are within 1 s of each other
    deduped: list[tuple[float, str]] = []
    prev_t = -999.0
    for t, label in moments:
        if t - prev_t > 1.0:
            deduped.append((t, label))
            prev_t = t

    return deduped[:10]


def _build_description_template(
    edit_plan: "EditPlan | None",
    song_info: SongInfo,
    character: str,
    sources: list[str],
    key_moments: list[tuple[float, str]],
    editor_credit: str = "[YOUR NAME]",
) -> str:
    """Build a description string without GPT.

    Used as fallback when OpenAI is not available.
    """
    lines: list[str] = []

    # Opening hook
    lines.append(
        f"A tribute edit celebrating {character}. "
        f'Set to "{song_info.title}" by {song_info.artist}.'
    )
    lines.append("")

    # Timestamps (chapter markers)
    if key_moments:
        lines.append("CHAPTERS")
        for t, label in key_moments:
            lines.append(f"{_sec_to_tc(t)} {label}")
        lines.append("")

    # Song credit
    lines.append("SONG")
    credit_parts = [f'"{song_info.title}" by {song_info.artist}']
    if song_info.year:
        credit_parts.append(str(song_info.year))
    if song_info.label:
        credit_parts.append(song_info.label)
    lines.append(" | ".join(credit_parts))
    lines.append("")

    # Source footage
    if sources:
        lines.append("SOURCE FOOTAGE")
        for src in sources:
            lines.append(f"- {src}")
        lines.append("")

    # Editor credit
    lines.append("EDIT")
    lines.append(f"Edited by {editor_credit}")
    lines.append("")

    # Fair-use statement
    lines.append("FAIR USE / COPYRIGHT")
    lines.append(
        "This video is a non-commercial fan tribute created for transformative, "
        "commentary, and educational purposes under the principles of fair use "
        "(17 U.S.C. § 107). No copyright infringement is intended. All rights "
        "belong to their respective owners. If you are a rights holder and have "
        "concerns, please contact me before filing a claim."
    )
    lines.append("")
    lines.append("#fanedits #tributevideo #videoedit")

    return "\n".join(lines)


def _gpt_build_metadata(
    character: str,
    song_info: SongInfo,
    sources: list[str],
    key_moments: list[tuple[float, str]],
    style: str,
    total_duration_sec: float,
) -> dict[str, Any] | None:
    """Use GPT-4o-mini to draft the full metadata package.

    Returns a dict with keys: title, description, tags, category_id,
    end_screen_start_sec.  Returns None on any failure.
    """
    try:
        from openai import OpenAI  # type: ignore[import-untyped]
    except ImportError:
        return None

    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        return None

    chapters_block = ""
    if key_moments:
        chapter_lines = [f"{_sec_to_tc(t)} {label}" for t, label in key_moments]
        chapters_block = "\n".join(chapter_lines)

    end_screen_sec = max(0.0, total_duration_sec - 20.0)

    sources_str = ", ".join(sources) if sources else "various"

    prompt = textwrap.dedent(
        f"""
        You are a YouTube channel manager specialising in fan tribute videos.
        Generate publishing metadata for this fan edit. Return ONLY valid JSON.

        === Edit details ===
        Character / subject: {character}
        Style: {style}
        Song: "{song_info.title}" by {song_info.artist} ({song_info.year})
        Label: {song_info.label or "unknown"}
        Source footage: {sources_str}
        Video duration: {_sec_to_tc(total_duration_sec)}
        Suggested end screen start: {_sec_to_tc(end_screen_sec)}
        Chapter markers (pre-built):
        {chapters_block or "(none)"}

        === Output JSON schema ===
        {{
          "title": "<70-char hook+subject+song>",
          "description": "<full YouTube description with CHAPTERS block using the chapter markers above, SONG credit, SOURCE FOOTAGE list, EDIT credit placeholder [YOUR NAME], FAIR USE statement, hashtags>",
          "tags": ["<tag1>", ..., "<tag15-20>"],
          "category_id": "<YouTube category ID string>",
          "category_name": "<human readable>",
          "end_screen_start_sec": <float>
        }}

        Title guidelines:
        - Under 70 characters
        - Formula: [powerful adjective] [character] | [song name] (fan edit)
        - Examples: "Ruthless Leon Kennedy | Centuries Fan Edit" (44 chars)

        Description guidelines:
        - Open with a single gripping sentence about the character/theme
        - Include the CHAPTERS block exactly as given (timecodes first)
        - Song credit: full line with artist, year, label
        - Source footage: bullet list of each source
        - Editor: "Edited by [YOUR NAME]"
        - Fair use statement: 2-3 sentences, non-commercial, transformative
        - End with 3-5 relevant hashtags

        Tags: include character name, song name, artist name, fandom names,
        "fan edit", "tribute", "AMV", emotion tags (e.g. "badass", "emotional"),
        and any relevant game/show/movie titles. 15-20 tags total.

        Category: if the edit is music-driven choose "10" (Music), if heavily
        gameplay footage choose "20" (Gaming), otherwise "24" (Entertainment).
        """
    ).strip()

    try:
        client = OpenAI()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1500,
            temperature=0.7,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or ""
        data: dict[str, Any] = json.loads(raw)
        data["_raw"] = raw
        return data
    except Exception as exc:
        logger.debug("GPT metadata generation failed: %s", exc)
        return None


def _tags_from_context(
    character: str,
    song_info: SongInfo,
    sources: list[str],
    style: str,
) -> list[str]:
    """Build a fallback tag list without GPT."""
    tags: list[str] = []

    # Character and style tags
    if character:
        tags.append(character)
        for part in character.split():
            if len(part) > 2 and part.lower() not in {"the", "and", "von", "van"}:
                tags.append(part)

    # Song tags
    if song_info.title:
        tags.append(song_info.title)
    if song_info.artist:
        tags.append(song_info.artist)

    # Source tags
    for src in sources[:8]:
        clean = re.sub(r"[-_]", " ", src).title()
        tags.append(clean)

    # Style/genre tags
    style_tags: dict[str, list[str]] = {
        "cinematic": ["cinematic", "fan edit", "tribute", "4k edit"],
        "action":    ["action edit", "amv", "tribute", "badass", "fan edit"],
        "emotional": ["emotional", "tribute", "sad edit", "fan edit", "feels"],
        "hype":      ["hype", "amv", "badass", "tribute", "fan edit"],
    }
    tags.extend(style_tags.get(style.lower(), ["fan edit", "tribute", "amv"]))
    tags.extend(["fan edit", "tribute video", "video edit", "multifandom"])

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for t in tags:
        t = t.strip()
        tl = t.lower()
        if tl and tl not in seen:
            seen.add(tl)
            unique.append(t)

    return unique[:20]


def _detect_category(sources: list[str], style: str) -> tuple[str, str]:
    """Heuristically pick a YouTube category based on source IDs and style."""
    src_str = " ".join(sources).lower()
    gaming_keywords = {"re", "re4", "re6", "re2", "game", "cutscene", "gameplay"}
    if any(kw in src_str for kw in gaming_keywords):
        return _CATEGORIES["gaming"]
    if style.lower() in {"cinematic", "emotional"}:
        return _CATEGORIES["film"]
    return _CATEGORIES["entertainment"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_youtube_metadata(
    edit_plan: "EditPlan | None",
    song_info: SongInfo,
    character: str,
    *,
    style: str = "cinematic",
    editor_credit: str = "[YOUR NAME]",
    use_gpt: bool = True,
    project_root: str | Path = ".",
) -> YouTubeMetadata:
    """Generate a complete YouTube publishing metadata package for a fan edit.

    Args:
        edit_plan: Completed EditPlan.  May be None; metadata will still be
            generated using the other arguments.
        song_info: Song attribution data.
        character: Primary character or subject of the edit, e.g. "Leon Kennedy".
        style: Tonal style -- "cinematic", "action", "emotional", or "hype".
            Influences tag selection and GPT prompt tone.
        editor_credit: Name to place in the "Edited by" line.  Defaults to
            "[YOUR NAME]" as a visible placeholder.
        use_gpt: If True and OPENAI_API_KEY is available, use GPT-4o-mini.
        project_root: Directory to search for .env files.

    Returns:
        YouTubeMetadata with all fields populated.
    """
    _load_env_key(project_root)

    sources = _extract_sources_from_plan(edit_plan) if edit_plan else []
    key_moments = _extract_key_moments(edit_plan) if edit_plan else [(0.0, "Intro")]

    total_duration_sec = 0.0
    if edit_plan and hasattr(edit_plan, "metadata"):
        total_duration_sec = float(
            getattr(edit_plan.metadata, "total_duration_sec", 0.0)
        )

    end_screen_start_sec = max(0.0, total_duration_sec - 20.0)

    # Convert key_moments to formatted timestamp list
    formatted_moments = [(_sec_to_tc(t), label) for t, label in key_moments]

    # Attempt GPT generation
    gpt_data: dict[str, Any] | None = None
    if use_gpt and os.environ.get("OPENAI_API_KEY"):
        gpt_data = _gpt_build_metadata(
            character=character,
            song_info=song_info,
            sources=sources,
            key_moments=key_moments,
            style=style,
            total_duration_sec=total_duration_sec,
        )

    if gpt_data:
        # Parse GPT output
        title = str(gpt_data.get("title", ""))[:70]
        description = str(gpt_data.get("description", ""))
        tags = [str(t) for t in gpt_data.get("tags", [])][:20]
        cat_id = str(gpt_data.get("category_id", _CATEGORIES["default"][0]))
        cat_name = str(gpt_data.get("category_name", _CATEGORIES["default"][1]))
        esc = float(gpt_data.get("end_screen_start_sec", end_screen_start_sec))
        raw = gpt_data.get("_raw", "")

        # Sanitize -- ensure title is under 70 chars
        if len(title) > 70:
            title = title[:67] + "..."

        # Ensure tags are non-empty
        if not tags:
            tags = _tags_from_context(character, song_info, sources, style)

        return YouTubeMetadata(
            title=title,
            description=description,
            tags=tags,
            category_id=cat_id,
            category_name=cat_name,
            end_screen_start_sec=esc,
            key_moment_timestamps=formatted_moments,
            raw_gpt_response=raw,
        )

    # Fallback: template-based generation
    logger.info("GPT unavailable or disabled, using template-based metadata generation.")

    # Build title
    style_adjective = {
        "cinematic": "Cinematic",
        "action":    "Ruthless",
        "emotional": "Emotional",
        "hype":      "Epic",
    }.get(style.lower(), "Fan")

    raw_title = f"{style_adjective} {character} | {song_info.title} (Fan Edit)"
    title = raw_title[:70]

    description = _build_description_template(
        edit_plan=edit_plan,
        song_info=song_info,
        character=character,
        sources=sources,
        key_moments=key_moments,
        editor_credit=editor_credit,
    )

    tags = _tags_from_context(character, song_info, sources, style)
    cat_id, cat_name = _detect_category(sources, style)

    return YouTubeMetadata(
        title=title,
        description=description,
        tags=tags,
        category_id=cat_id,
        category_name=cat_name,
        end_screen_start_sec=end_screen_start_sec,
        key_moment_timestamps=formatted_moments,
        raw_gpt_response="",
    )
