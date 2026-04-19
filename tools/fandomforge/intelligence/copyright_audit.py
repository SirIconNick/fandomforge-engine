"""Fair-use copyright audit for fan tribute edits.

Analyzes an edit plan and produces a structured audit report covering:
- Song attribution (MusicBrainz lookup by artist + title)
- Source footage attribution (studio/publisher for each source)
- Song-use ratio vs total song duration (transformation factor)
- Per-source footage duration totals
- Clip-length warnings (clips > 30 s are weaker fair-use claims)
- DMCA sensitivity flags (commercial potential, market harm, transformation)
- Risk score: low / medium / high with specific flag reasons
- Pre-formatted YouTube description fair-use statement

Public API
----------
    from fandomforge.intelligence.copyright_audit import (
        audit,
        CopyrightAudit,
        SongMetadata,
        SourceMetadata,
    )

    song = SongMetadata(title="Centuries", artist="Fall Out Boy", year=2014)
    sources = [
        SourceMetadata(source_id="re4r", title="Resident Evil 4 Remake",
                       publisher="Capcom", year=2023),
    ]
    report = audit(edit_plan, song, sources)
    print(report.risk_score)       # "low" | "medium" | "high"
    print(report.fair_use_statement)
    print(report.to_markdown())
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
import urllib.parse
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fandomforge.intelligence.shot_optimizer import EditPlan

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Input metadata dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SongMetadata:
    """Attribution data for the song used in the edit.

    Attributes:
        title: Song name.
        artist: Artist or band name.
        year: Release year.
        label: Record label.  MusicBrainz lookup will attempt to fill this.
        duration_sec: Total song duration in seconds.  0 if unknown.
        musicbrainz_id: MusicBrainz recording MBID, if already known.
    """

    title: str
    artist: str
    year: int | str = ""
    label: str = ""
    duration_sec: float = 0.0
    musicbrainz_id: str = ""


@dataclass
class SourceMetadata:
    """Attribution data for a single footage source.

    Attributes:
        source_id: Machine ID matching ShotRecord.source, e.g. "re4r".
        title: Human-readable title, e.g. "Resident Evil 4 Remake".
        publisher: Studio or publisher, e.g. "Capcom".
        year: Release year.
        type: "game" | "film" | "series" | "other".
    """

    source_id: str
    title: str
    publisher: str = ""
    year: int | str = ""
    type: str = "other"  # game | film | series | other


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ClipWarning:
    """A specific long-clip fair-use concern.

    Attributes:
        source_id: Source where the long clip appears.
        shot_number: Shot number in the edit plan.
        duration_sec: Clip duration in seconds.
        message: Human-readable warning text.
    """

    source_id: str
    shot_number: int
    duration_sec: float
    message: str


@dataclass
class DmcaFlag:
    """A DMCA sensitivity flag.

    Attributes:
        category: One of "commercial", "transformation", "market_harm",
            "substitution", "song_ratio", "excessive_clip_length".
        severity: "warning" | "info".
        detail: Explanation.
    """

    category: str
    severity: str  # "warning" | "info"
    detail: str


@dataclass
class CopyrightAudit:
    """Complete fair-use audit report.

    Attributes:
        song_attribution: Resolved song attribution line.
        source_attributions: List of source attribution strings.
        song_use_duration_sec: How many seconds of the song are used in the edit.
        song_total_duration_sec: Full song duration.
        song_use_ratio: song_use_duration_sec / song_total_duration_sec.
        seconds_per_source: Dict mapping source_id to total footage used (seconds).
        clip_warnings: Clips that are individually longer than 30 s.
        dmca_flags: Specific DMCA sensitivity flags.
        risk_score: "low" | "medium" | "high".
        risk_reasons: List of strings explaining the risk score.
        fair_use_statement: Pre-formatted YouTube description text.
        musicbrainz_data: Raw MusicBrainz response dict (may be empty).
    """

    song_attribution: str
    source_attributions: list[str]
    song_use_duration_sec: float
    song_total_duration_sec: float
    song_use_ratio: float
    seconds_per_source: dict[str, float]
    clip_warnings: list[ClipWarning]
    dmca_flags: list[DmcaFlag]
    risk_score: str  # "low" | "medium" | "high"
    risk_reasons: list[str]
    fair_use_statement: str
    musicbrainz_data: dict = field(default_factory=dict)

    # -----------------------------------------------------------------------
    # Serialization helpers
    # -----------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize audit to a plain dict (JSON-safe)."""
        return {
            "song_attribution": self.song_attribution,
            "source_attributions": self.source_attributions,
            "song_use_duration_sec": round(self.song_use_duration_sec, 2),
            "song_total_duration_sec": round(self.song_total_duration_sec, 2),
            "song_use_ratio": round(self.song_use_ratio, 4),
            "seconds_per_source": {
                k: round(v, 2) for k, v in self.seconds_per_source.items()
            },
            "clip_warnings": [
                {
                    "source_id": cw.source_id,
                    "shot_number": cw.shot_number,
                    "duration_sec": round(cw.duration_sec, 2),
                    "message": cw.message,
                }
                for cw in self.clip_warnings
            ],
            "dmca_flags": [
                {
                    "category": f.category,
                    "severity": f.severity,
                    "detail": f.detail,
                }
                for f in self.dmca_flags
            ],
            "risk_score": self.risk_score,
            "risk_reasons": self.risk_reasons,
            "fair_use_statement": self.fair_use_statement,
        }

    def to_markdown(self) -> str:
        """Render the audit as a human-readable Markdown report."""
        lines: list[str] = []
        lines.append("# Copyright Fair-Use Audit Report")
        lines.append("")
        lines.append(f"**Risk Score:** {self.risk_score.upper()}")
        lines.append("")

        lines.append("## Song Attribution")
        lines.append(self.song_attribution)
        lines.append("")

        if self.song_total_duration_sec > 0:
            pct = self.song_use_ratio * 100
            lines.append("## Song Usage")
            lines.append(
                f"- Used: {self.song_use_duration_sec:.1f} s of "
                f"{self.song_total_duration_sec:.1f} s total ({pct:.1f}%)"
            )
            lines.append("")

        lines.append("## Source Footage")
        for attr in self.source_attributions:
            lines.append(f"- {attr}")
        lines.append("")

        lines.append("## Footage Duration Per Source")
        for src_id, secs in sorted(self.seconds_per_source.items(), key=lambda x: -x[1]):
            lines.append(f"- {src_id}: {secs:.1f} s")
        lines.append("")

        if self.clip_warnings:
            lines.append("## Long-Clip Warnings")
            for cw in self.clip_warnings:
                lines.append(f"- Shot {cw.shot_number} ({cw.source_id}): {cw.message}")
            lines.append("")

        if self.dmca_flags:
            lines.append("## DMCA Sensitivity Flags")
            for flag in self.dmca_flags:
                icon = "WARNING" if flag.severity == "warning" else "INFO"
                lines.append(f"- [{icon}] {flag.category}: {flag.detail}")
            lines.append("")

        if self.risk_reasons:
            lines.append("## Risk Factors")
            for reason in self.risk_reasons:
                lines.append(f"- {reason}")
            lines.append("")

        lines.append("## Fair Use Statement")
        lines.append(self.fair_use_statement)

        return "\n".join(lines)

    def save_markdown(self, output_path: str | Path) -> None:
        """Write the Markdown report to disk."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.to_markdown(), encoding="utf-8")

    def save_json(self, output_path: str | Path) -> None:
        """Write the audit data as a JSON file."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# MusicBrainz lookup
# ---------------------------------------------------------------------------

_MB_API = "https://musicbrainz.org/ws/2"
_MB_USER_AGENT = "FandomForge/0.1 ( nickdamatoit@gmail.com )"


def _lookup_song_musicbrainz(artist: str, title: str) -> dict:
    """Query the MusicBrainz API for recording metadata.

    Returns a dict with at minimum:
        - label (str)
        - duration_sec (float)
        - mbid (str)
        - year (str)
    Returns an empty dict on any failure.
    """
    query = f'recording:"{title}" AND artist:"{artist}"'
    encoded = urllib.parse.quote(query)
    url = (
        f"{_MB_API}/recording"
        f"?query={encoded}"
        f"&inc=releases+artist-credits+labels"
        f"&fmt=json"
        f"&limit=5"
    )

    try:
        req = urllib.request.Request(url, headers={"User-Agent": _MB_USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.debug("MusicBrainz lookup failed: %s", exc)
        return {}

    recordings = data.get("recordings", [])
    if not recordings:
        return {}

    # Use the first result
    rec = recordings[0]
    mbid = rec.get("id", "")
    duration_ms = rec.get("length", 0) or 0
    duration_sec = duration_ms / 1000.0 if duration_ms else 0.0

    # Extract year from first release
    release_year = ""
    label_name = ""
    releases = rec.get("releases", [])
    if releases:
        first_release = releases[0]
        date_str = first_release.get("date", "")
        if date_str:
            release_year = date_str[:4]
        # Label info is nested under label-info inside release
        label_infos = first_release.get("label-info", [])
        if label_infos:
            label_obj = label_infos[0].get("label", {})
            label_name = label_obj.get("name", "")

    return {
        "mbid": mbid,
        "duration_sec": duration_sec,
        "year": release_year,
        "label": label_name,
        "raw": rec,
    }


# ---------------------------------------------------------------------------
# Edit-plan analysis helpers
# ---------------------------------------------------------------------------


def _total_edit_duration(edit_plan: "EditPlan") -> float:
    """Return total edit duration from the edit plan metadata or shot list."""
    if hasattr(edit_plan, "metadata"):
        return float(getattr(edit_plan.metadata, "total_duration_sec", 0.0))
    if hasattr(edit_plan, "shots") and edit_plan.shots:
        last = edit_plan.shots[-1]
        return float(last.start_time) + float(last.duration)
    return 0.0


def _seconds_per_source(edit_plan: "EditPlan") -> dict[str, float]:
    """Accumulate total footage seconds for each source in the edit plan."""
    totals: dict[str, float] = {}
    if hasattr(edit_plan, "shots"):
        for shot in edit_plan.shots:
            src = str(getattr(shot, "source", "") or getattr(shot, "source_id", "") or "unknown")
            totals[src] = totals.get(src, 0.0) + float(getattr(shot, "duration", 0.0))
    return totals


def _find_long_clips(edit_plan: "EditPlan", threshold_sec: float = 30.0) -> list[ClipWarning]:
    """Identify individual shots that exceed threshold_sec in duration."""
    warnings: list[ClipWarning] = []
    if not hasattr(edit_plan, "shots"):
        return warnings
    for shot in edit_plan.shots:
        dur = float(getattr(shot, "duration", 0.0))
        if dur > threshold_sec:
            src = str(getattr(shot, "source", "") or "unknown")
            num = int(getattr(shot, "cut_index", 0))
            warnings.append(
                ClipWarning(
                    source_id=src,
                    shot_number=num,
                    duration_sec=dur,
                    message=(
                        f"{dur:.1f} s clip from {src} (shot {num}) exceeds 30 s. "
                        "Longer unbroken clips weaken the transformativeness argument."
                    ),
                )
            )
    return warnings


# ---------------------------------------------------------------------------
# DMCA flag analysis
# ---------------------------------------------------------------------------


def _analyze_dmca_flags(
    song_use_ratio: float,
    seconds_per_source: dict[str, float],
    clip_warnings: list[ClipWarning],
    edit_duration_sec: float,
    is_monetized: bool = False,
) -> list[DmcaFlag]:
    """Build the list of DMCA sensitivity flags.

    Four factors from the four-factor fair use test inform the flags.
    """
    flags: list[DmcaFlag] = []

    # Factor 1: Purpose and character of the use (commercial vs non-commercial)
    if is_monetized:
        flags.append(
            DmcaFlag(
                category="commercial",
                severity="warning",
                detail=(
                    "Monetized fan edits weaken the fair-use 'non-commercial' argument. "
                    "Consider turning off monetization for this video."
                ),
            )
        )
    else:
        flags.append(
            DmcaFlag(
                category="commercial",
                severity="info",
                detail="Non-monetized; supports fair-use non-commercial claim.",
            )
        )

    # Factor 2: Nature of the copyrighted work (creative works = harder to claim fair use)
    flags.append(
        DmcaFlag(
            category="transformation",
            severity="info",
            detail=(
                "Fan tribute edits are transformative in purpose (commentary/celebration) "
                "but use highly creative source material. "
                "Transformative editing (cuts, sync, colour grade) strengthens the claim."
            ),
        )
    )

    # Factor 3: Amount of the work used (song ratio)
    if song_use_ratio >= 0.98:
        flags.append(
            DmcaFlag(
                category="song_ratio",
                severity="warning",
                detail=(
                    f"The full song is used ({song_use_ratio:.0%}). "
                    "Using only the most relevant portion strengthens fair-use claims."
                ),
            )
        )
    elif song_use_ratio >= 0.80:
        flags.append(
            DmcaFlag(
                category="song_ratio",
                severity="info",
                detail=(
                    f"{song_use_ratio:.0%} of the song is used. "
                    "This is common for full-length edits and unlikely to trigger claims alone."
                ),
            )
        )

    # Individual long clips
    if clip_warnings:
        flags.append(
            DmcaFlag(
                category="excessive_clip_length",
                severity="warning",
                detail=(
                    f"{len(clip_warnings)} shot(s) exceed 30 s each. "
                    "Each long unbroken clip from a single source reduces the "
                    "argument that the use is transformative."
                ),
            )
        )

    # Factor 4: Effect on the market
    flags.append(
        DmcaFlag(
            category="market_harm",
            severity="info",
            detail=(
                "Tribute edits typically promote rather than substitute for the original work, "
                "reducing the market harm factor. This is the strongest fair-use argument "
                "for fan content."
            ),
        )
    )

    return flags


# ---------------------------------------------------------------------------
# Risk scoring
# ---------------------------------------------------------------------------


def _compute_risk_score(
    flags: list[DmcaFlag],
    clip_warnings: list[ClipWarning],
    song_use_ratio: float,
) -> tuple[str, list[str]]:
    """Return (risk_score, risk_reasons) based on flags and metrics.

    Levels:
        low    -- non-commercial, no long clips, no full-song use
        medium -- one moderate concern (full song use or a few long clips)
        high   -- monetized or multiple serious concerns
    """
    high_triggers: list[str] = []
    medium_triggers: list[str] = []

    for flag in flags:
        if flag.severity == "warning":
            if flag.category == "commercial":
                high_triggers.append("Video is set to monetize (weakens fair-use claim)")
            elif flag.category == "excessive_clip_length":
                medium_triggers.append(
                    f"{len(clip_warnings)} clip(s) over 30 s detected"
                )
            elif flag.category == "song_ratio" and song_use_ratio >= 0.98:
                medium_triggers.append("Full song used without modification")

    if high_triggers:
        return ("high", high_triggers + medium_triggers)
    if medium_triggers:
        return ("medium", medium_triggers)
    return ("low", ["Non-commercial use, no excessively long clips, transformative editing."])


# ---------------------------------------------------------------------------
# Attribution builders
# ---------------------------------------------------------------------------


def _build_song_attribution(song: SongMetadata, mb_data: dict) -> str:
    """Build a single attribution line for the song."""
    label = mb_data.get("label") or song.label or "unknown label"
    year = mb_data.get("year") or str(song.year) or "unknown year"
    mbid = mb_data.get("mbid") or song.musicbrainz_id

    parts = [f'"{song.title}" by {song.artist}', f"{year}", label]
    line = " | ".join(p for p in parts if p and p not in {"unknown year", "unknown label"})
    if mbid:
        line += f" (MusicBrainz: {mbid})"
    return line


def _build_source_attributions(sources_meta: list[SourceMetadata]) -> list[str]:
    """Build a list of attribution strings for source footage."""
    lines: list[str] = []
    for src in sources_meta:
        parts = [src.title or src.source_id]
        if src.publisher:
            parts.append(src.publisher)
        if src.year:
            parts.append(str(src.year))
        if src.type and src.type != "other":
            parts.append(src.type.title())
        lines.append(" | ".join(parts))
    return lines


# ---------------------------------------------------------------------------
# Fair-use statement builder
# ---------------------------------------------------------------------------


def _build_fair_use_statement(
    song: SongMetadata,
    source_attributions: list[str],
    risk_score: str,
) -> str:
    """Build a YouTube-ready fair-use statement paragraph."""
    song_line = f'"{song.title}" by {song.artist}'
    if song.year:
        song_line += f" ({song.year})"
    if song.label:
        song_line += f", {song.label}"

    source_block = ""
    if source_attributions:
        items = "; ".join(source_attributions[:5])
        source_block = f" Source footage from: {items}."

    disclaimer = (
        "This video is a non-commercial fan tribute created for transformative, "
        "commentary, and celebratory purposes consistent with fair use principles "
        "(17 U.S.C. § 107). No copyright infringement is intended. "
        f"Music: {song_line}.{source_block} "
        "All rights belong to their respective owners. "
        "If you are a rights holder with concerns, please contact me directly "
        "before submitting a claim."
    )

    if risk_score == "high":
        disclaimer += (
            " Note: this video has been assessed as higher-risk for copyright claims. "
            "Consider removing monetization and using a Content ID dispute process "
            "rather than a formal DMCA notice."
        )

    return disclaimer


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def audit(
    edit_plan: "EditPlan",
    song_metadata: SongMetadata,
    sources_metadata: list[SourceMetadata],
    *,
    is_monetized: bool = False,
    long_clip_threshold_sec: float = 30.0,
    lookup_musicbrainz: bool = True,
) -> CopyrightAudit:
    """Run a fair-use audit on an edit plan.

    Args:
        edit_plan: Completed EditPlan from shot_optimizer.
        song_metadata: Song attribution data.  MusicBrainz lookup will attempt
            to fill in label, year, and duration if they are empty.
        sources_metadata: List of source footage attribution records.  Include
            one entry per unique source_id used in the edit.
        is_monetized: Set True if the YouTube video will have ads.  Raises risk.
        long_clip_threshold_sec: Clips individually longer than this are flagged.
        lookup_musicbrainz: If True, query MusicBrainz for song metadata.

    Returns:
        CopyrightAudit with full report, risk score, and fair-use statement.
    """
    # 1. MusicBrainz lookup
    mb_data: dict = {}
    if lookup_musicbrainz and song_metadata.title and song_metadata.artist:
        mb_data = _lookup_song_musicbrainz(song_metadata.artist, song_metadata.title)
        if mb_data:
            logger.info(
                "MusicBrainz resolved '%s' by %s: label=%s",
                song_metadata.title,
                song_metadata.artist,
                mb_data.get("label", "unknown"),
            )
            # Fill in missing metadata from MusicBrainz
            if not song_metadata.label and mb_data.get("label"):
                song_metadata.label = mb_data["label"]
            if not song_metadata.year and mb_data.get("year"):
                song_metadata.year = mb_data["year"]
            if not song_metadata.duration_sec and mb_data.get("duration_sec"):
                song_metadata.duration_sec = mb_data["duration_sec"]
            if not song_metadata.musicbrainz_id and mb_data.get("mbid"):
                song_metadata.musicbrainz_id = mb_data["mbid"]

    # 2. Edit duration = song use duration (the whole edit runs the song)
    edit_duration = _total_edit_duration(edit_plan)
    song_use_sec = edit_duration

    song_total_sec = song_metadata.duration_sec
    if song_total_sec <= 0:
        # Cannot compute ratio; assume full song used
        song_total_sec = song_use_sec
    song_use_ratio = min(1.0, song_use_sec / song_total_sec) if song_total_sec > 0 else 1.0

    # 3. Per-source footage totals
    sps = _seconds_per_source(edit_plan)

    # 4. Long-clip warnings
    clip_warnings = _find_long_clips(edit_plan, long_clip_threshold_sec)

    # 5. DMCA flags
    flags = _analyze_dmca_flags(
        song_use_ratio=song_use_ratio,
        seconds_per_source=sps,
        clip_warnings=clip_warnings,
        edit_duration_sec=edit_duration,
        is_monetized=is_monetized,
    )

    # 6. Risk score
    risk_score, risk_reasons = _compute_risk_score(flags, clip_warnings, song_use_ratio)

    # 7. Attribution strings
    song_attr = _build_song_attribution(song_metadata, mb_data)
    src_attrs = _build_source_attributions(sources_metadata)

    # 8. Fair-use statement
    statement = _build_fair_use_statement(song_metadata, src_attrs, risk_score)

    return CopyrightAudit(
        song_attribution=song_attr,
        source_attributions=src_attrs,
        song_use_duration_sec=song_use_sec,
        song_total_duration_sec=song_total_sec,
        song_use_ratio=song_use_ratio,
        seconds_per_source=sps,
        clip_warnings=clip_warnings,
        dmca_flags=flags,
        risk_score=risk_score,
        risk_reasons=risk_reasons,
        fair_use_statement=statement,
        musicbrainz_data=mb_data,
    )
