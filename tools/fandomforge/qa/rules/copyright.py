"""qa.copyright — platform-specific copyright risk flags.

Blocks if:
- platform = youtube and the song appears in docs/knowledge/high-risk-songs.md
  without an explicit override.
- length > 60s without a transformative-use marker on YouTube.

The high-risk-songs file is consulted as a soft reference; missing file is
treated as info-only, not a block (it's Phase 8 that ships the canonical list).
"""

from __future__ import annotations

import re
from pathlib import Path

from fandomforge.qa.gate import GateContext, RuleResult, rule


def _load_high_risk_songs() -> list[dict[str, str]]:
    """Parse docs/knowledge/high-risk-songs.md — one entry per bullet
    `- Artist — Title` under any heading."""
    here = Path(__file__).resolve()
    for ancestor in [here, *here.parents]:
        candidate = ancestor / "docs" / "knowledge" / "high-risk-songs.md"
        if candidate.exists():
            break
    else:
        return []

    entries: list[dict[str, str]] = []
    bullet = re.compile(r"^\s*-\s+(.+?)\s*$")
    for line in candidate.read_text(encoding="utf-8").splitlines():
        m = bullet.match(line)
        if not m:
            continue
        text = m.group(1).strip()
        # Allow either "Artist - Title" or "Artist — Title" or "Artist: Title".
        if "—" in text:
            artist, title = text.split("—", 1)
        elif " - " in text:
            artist, title = text.split(" - ", 1)
        elif ":" in text:
            artist, title = text.split(":", 1)
        else:
            continue
        entries.append({
            "artist": artist.strip().lower(),
            "title": title.strip().lower(),
        })
    return entries


@rule("qa.copyright", "Copyright risk", level="block")
def rule_copyright(ctx: GateContext) -> RuleResult:
    if not ctx.edit_plan:
        return RuleResult(
            id="qa.copyright", name="Copyright risk", level="block",
            status="skipped", message="no edit-plan.json",
        )

    platform = str(ctx.edit_plan.get("platform_target", "")).lower()
    song = ctx.edit_plan.get("song") or {}
    song_title = str(song.get("title", "")).lower()
    song_artist = str(song.get("artist", "")).lower()
    length_sec = float(ctx.edit_plan.get("length_seconds", 0))

    findings: list[str] = []

    high_risk = _load_high_risk_songs()
    hit: dict[str, str] | None = None
    for entry in high_risk:
        if entry["artist"] in song_artist and entry["title"] in song_title:
            hit = entry
            break

    if hit and platform == "youtube":
        findings.append(
            f"Song '{song_artist} — {song_title}' is on the high-takedown list for YouTube."
        )

    if platform == "youtube" and length_sec > 60 and not _has_transformative_marker(ctx.edit_plan):
        findings.append(
            "YouTube edit longer than 60s without a documented transformative-use marker."
        )

    if findings:
        return RuleResult(
            id="qa.copyright", name="Copyright risk", level="block",
            status="fail",
            message=" ".join(findings),
            evidence={
                "platform": platform,
                "song_artist": song_artist,
                "song_title": song_title,
                "length_sec": length_sec,
                "high_risk_hit": hit,
            },
        )

    return RuleResult(
        id="qa.copyright", name="Copyright risk", level="block",
        status="pass", message="no copyright red flags for current platform",
    )


def _has_transformative_marker(edit_plan: dict) -> bool:
    """Look for any `fair_use_statement` in credits OR a 'transformative' key
    in copyright_overrides."""
    credits = edit_plan.get("credits") or {}
    if credits.get("fair_use_statement"):
        return True
    for override in edit_plan.get("copyright_overrides") or []:
        if "transformative" in override.get("rule", "").lower():
            return True
    return False
