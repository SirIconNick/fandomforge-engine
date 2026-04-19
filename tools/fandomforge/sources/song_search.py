"""Song source quality ranker + YouTube search wrapper.

Why: yt-dlp can rip audio from any YouTube URL, but the *quality* of the
result varies by upload type. Music videos often have SFX edits, radio
versions, or truncated endings. Lyric / Official Audio / Visualizer uploads
carry the clean mastered mix.

This module probes metadata with `yt-dlp --dump-json --skip-download`,
ranks candidates by upload kind, and exposes two entry points:

    rank_song_source(url)  -> SongSourceQuality
    search_song(query, n)  -> list[SongCandidate]   (sorted best-first)

Nothing here downloads audio — that's the caller's job. We just pick the
best URL to hand to `download_source(audio_only=True)`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum


class SongKind(str, Enum):
    OFFICIAL_AUDIO = "official_audio"
    VISUALIZER = "visualizer"
    LYRIC_VIDEO = "lyric_video"
    MUSIC_VIDEO = "music_video"
    LIVE = "live"
    REMIX = "remix"
    COVER = "cover"
    UNKNOWN = "unknown"


# kind -> base score. Tuned so anything above 50 is acceptable, below 50 earns
# a warning, below 20 is explicit fan/low-quality territory.
_KIND_SCORES: dict[SongKind, int] = {
    SongKind.OFFICIAL_AUDIO: 100,
    SongKind.VISUALIZER: 85,
    SongKind.LYRIC_VIDEO: 75,
    SongKind.MUSIC_VIDEO: 40,
    SongKind.LIVE: 15,
    SongKind.REMIX: 25,
    SongKind.COVER: 20,
    SongKind.UNKNOWN: 30,
}


@dataclass
class SongSourceQuality:
    """Result of ranking a single URL."""

    kind: SongKind
    score: int  # 0..100, higher is better
    reason: str
    title: str = ""
    uploader: str = ""
    duration_sec: float = 0.0
    url: str = ""
    id: str = ""


@dataclass
class SongCandidate:
    """One result from a song search."""

    url: str
    id: str
    title: str
    uploader: str
    duration_sec: float
    quality: SongSourceQuality
    view_count: int = 0
    _raw: dict = field(default_factory=dict)


# ---------- Title classification ----------

# Patterns are checked in order; first match wins.
# Case-insensitive.
_KIND_PATTERNS: list[tuple[re.Pattern[str], SongKind]] = [
    (re.compile(r"\bofficial\s+audio\b", re.I),          SongKind.OFFICIAL_AUDIO),
    (re.compile(r"\bfull\s+song\b", re.I),                SongKind.OFFICIAL_AUDIO),
    (re.compile(r"\baudio\s+only\b", re.I),               SongKind.OFFICIAL_AUDIO),
    (re.compile(r"\(audio\)", re.I),                      SongKind.OFFICIAL_AUDIO),
    (re.compile(r"\[audio\]", re.I),                      SongKind.OFFICIAL_AUDIO),
    (re.compile(r"\bvisualizer\b", re.I),                 SongKind.VISUALIZER),
    (re.compile(r"\banimated\s+video\b", re.I),           SongKind.VISUALIZER),
    (re.compile(r"\blyric\s+video\b", re.I),              SongKind.LYRIC_VIDEO),
    (re.compile(r"\blyrics\b", re.I),                     SongKind.LYRIC_VIDEO),
    (re.compile(r"\bofficial\s+music\s+video\b", re.I),   SongKind.MUSIC_VIDEO),
    (re.compile(r"\bofficial\s+video\b", re.I),           SongKind.MUSIC_VIDEO),
    (re.compile(r"\b(music\s+video|MV)\b", re.I),         SongKind.MUSIC_VIDEO),
    (re.compile(r"\blive\b", re.I),                       SongKind.LIVE),
    (re.compile(r"\bacoustic\b", re.I),                   SongKind.LIVE),
    (re.compile(r"\bunplugged\b", re.I),                  SongKind.LIVE),
    (re.compile(r"\bremix\b", re.I),                      SongKind.REMIX),
    (re.compile(r"\bbootleg\b", re.I),                    SongKind.REMIX),
    (re.compile(r"\b(cover|covered by|performed by)\b", re.I), SongKind.COVER),
    (re.compile(r"\btribute\b", re.I),                    SongKind.COVER),
]

_DEMOTE_PATTERNS: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(r"\b(clean\s+version|radio\s+edit|edited)\b", re.I),
     -30, "flagged as clean/radio edit"),
    (re.compile(r"\bpreview\b", re.I),
     -25, "flagged as preview"),
    (re.compile(r"\bsnippet\b", re.I),
     -40, "flagged as snippet"),
    (re.compile(r"\breaction\b", re.I),
     -60, "reaction video, not the source"),
    (re.compile(r"\b(8d|bass\s*boosted|sped\s*up|slowed|nightcore)\b", re.I),
     -40, "modified mix (8D / bass boosted / sped / slowed)"),
    (re.compile(r"\bfan[\s-]?made\b", re.I),
     -25, "fan-made upload"),
]

_VEVO_RE = re.compile(r"vevo\b", re.I)
_OFFICIAL_CHANNEL_RE = re.compile(r"\bofficial\b", re.I)


def _classify_title(title: str) -> tuple[SongKind, str]:
    """Pick the best kind based on the title alone. Returns (kind, reason)."""
    for pat, kind in _KIND_PATTERNS:
        if pat.search(title):
            return kind, f"title matches {pat.pattern!r}"
    return SongKind.UNKNOWN, "no explicit kind marker in title"


def _uploader_bonus(uploader: str) -> tuple[int, str]:
    """Verified/official uploaders get a bump."""
    if not uploader:
        return 0, ""
    if _VEVO_RE.search(uploader):
        return 10, "VEVO uploader"
    if _OFFICIAL_CHANNEL_RE.search(uploader):
        return 5, "official-marked uploader"
    return 0, ""


def _apply_demotions(title: str, base_score: int) -> tuple[int, list[str]]:
    """Apply demotion patterns to the score. Returns (adjusted, notes)."""
    score = base_score
    notes: list[str] = []
    for pat, delta, note in _DEMOTE_PATTERNS:
        if pat.search(title):
            score += delta  # delta is negative
            notes.append(note)
    return max(0, min(100, score)), notes


# ---------- yt-dlp metadata probing ----------


def _probe_metadata(url: str, timeout: int = 30) -> dict | None:
    """Return yt-dlp's --dump-json payload for a single URL, or None on failure."""
    if shutil.which("yt-dlp") is None:
        return None
    try:
        proc = subprocess.run(
            [
                "yt-dlp",
                "--dump-json",
                "--skip-download",
                "--no-playlist",
                "--no-warnings",
                url,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    try:
        return json.loads(proc.stdout.splitlines()[0])
    except (json.JSONDecodeError, IndexError):
        return None


def _search_metadata(query: str, max_results: int, timeout: int = 60) -> list[dict]:
    """yt-dlp search. Returns up to max_results metadata dicts."""
    if shutil.which("yt-dlp") is None:
        return []
    try:
        proc = subprocess.run(
            [
                "yt-dlp",
                f"ytsearch{max_results}:{query}",
                "--dump-json",
                "--skip-download",
                "--no-playlist",
                "--no-warnings",
                "--flat-playlist",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    results: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return results


# ---------- Public entry points ----------


def rank_metadata(meta: dict) -> SongSourceQuality:
    """Rank a yt-dlp metadata dict. Pure function — no network."""
    title = (meta.get("title") or "").strip()
    uploader = (meta.get("uploader") or meta.get("channel") or "").strip()
    duration = float(meta.get("duration") or 0)
    video_id = meta.get("id") or ""
    url = meta.get("webpage_url") or meta.get("url") or ""

    kind, reason = _classify_title(title)
    base = _KIND_SCORES[kind]
    uploader_delta, uploader_reason = _uploader_bonus(uploader)
    adjusted, demote_notes = _apply_demotions(title, base + uploader_delta)

    reason_parts = [reason]
    if uploader_reason:
        reason_parts.append(uploader_reason)
    reason_parts.extend(demote_notes)

    return SongSourceQuality(
        kind=kind,
        score=adjusted,
        reason="; ".join(reason_parts),
        title=title,
        uploader=uploader,
        duration_sec=duration,
        url=url,
        id=video_id,
    )


def rank_song_source(url: str) -> SongSourceQuality:
    """Probe a URL's metadata and return a quality ranking.

    On yt-dlp failure / timeout / missing binary, returns a score-0
    SongSourceQuality with kind=UNKNOWN and the URL populated — caller can
    still choose to proceed.
    """
    meta = _probe_metadata(url)
    if meta is None:
        return SongSourceQuality(
            kind=SongKind.UNKNOWN,
            score=0,
            reason="yt-dlp metadata probe failed",
            url=url,
        )
    return rank_metadata(meta)


def search_song(
    query: str,
    max_results: int = 10,
    min_score: int = 0,
) -> list[SongCandidate]:
    """Search YouTube and rank all results by quality.

    Returns a list of SongCandidate sorted by score descending. Duplicate
    video ids are filtered. Candidates below min_score are dropped.
    """
    metas = _search_metadata(query, max_results)
    seen: set[str] = set()
    candidates: list[SongCandidate] = []
    for meta in metas:
        vid = meta.get("id")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        quality = rank_metadata(meta)
        if quality.score < min_score:
            continue
        candidates.append(
            SongCandidate(
                url=meta.get("webpage_url")
                    or f"https://www.youtube.com/watch?v={vid}",
                id=vid,
                title=quality.title,
                uploader=quality.uploader,
                duration_sec=quality.duration_sec,
                quality=quality,
                view_count=int(meta.get("view_count") or 0),
                _raw=meta,
            )
        )
    candidates.sort(key=lambda c: (c.quality.score, c.view_count), reverse=True)
    return candidates


__all__ = [
    "SongKind",
    "SongSourceQuality",
    "SongCandidate",
    "rank_metadata",
    "rank_song_source",
    "search_song",
]
