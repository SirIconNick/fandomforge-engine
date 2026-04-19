"""Allowlist of legal source domains and helpers for classifying URLs.

FandomForge has an established denylist for streaming services (Netflix, Disney+,
etc.) in download.py. This module adds the positive side: a curated list of
known-safe sources (CC0, Creative Commons, public domain) plus an "allowed
with explicit license note" tier for cases like official studio YouTube trailers
where fair use applies but requires per-URL documentation.

Usage:
    from fandomforge.sources.legal_sources import classify_url, ClassificationResult

    result = classify_url("https://www.pexels.com/video/12345/")
    # result.tier == "allowlist"

    result = classify_url("https://www.youtube.com/watch?v=officialTrailerId")
    # result.tier == "requires_license_note"

    result = classify_url("https://netflix.com/...")
    # result.tier == "denied"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse


# Domains where content is typically legally reusable (CC0, CC-licensed, or
# public domain). Always OK for test fixtures and demo projects.
ALLOWLIST_DOMAINS: dict[str, str] = {
    "archive.org": "Internet Archive (public domain / CC)",
    "pexels.com": "Pexels License (free for commercial use, no attribution required)",
    "videos.pexels.com": "Pexels License",
    "pixabay.com": "Pixabay License (free for commercial use)",
    "videvo.net": "Videvo — mixed, check per-clip license",
    "mixkit.co": "Mixkit License (free music + stock video)",
    "freemusicarchive.org": "Free Music Archive (Creative Commons)",
    "freepd.com": "FreePD (public domain music)",
    "incompetech.com": "Kevin MacLeod / Incompetech (CC-BY)",
    "incompetech.filmmusic.io": "Kevin MacLeod / Incompetech (CC-BY)",
    "uppbeat.io": "Uppbeat — free tier CC-BY",
    "openbeelden.nl": "Open Beelden (NL public broadcast CC archive)",
    "ccmixter.org": "ccMixter (Creative Commons remixes)",
}

# Domains that CAN be used but require a per-URL `license_note` documenting why
# the specific content is legally usable. Mostly YouTube (for official studio
# trailers covered by fair use) and Vimeo (where the uploader may or may not
# hold rights).
REQUIRES_LICENSE_NOTE_DOMAINS: set[str] = {
    "youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com",
    "vimeo.com", "www.vimeo.com", "player.vimeo.com",
    "dailymotion.com", "www.dailymotion.com",
}

# Domains we flat-out refuse. Mirrors the download.py DISALLOWED_DOMAINS but
# kept here as well so classify_url has the full picture.
DENIED_DOMAINS: set[str] = {
    "netflix.com", "www.netflix.com",
    "hulu.com", "www.hulu.com",
    "disneyplus.com", "www.disneyplus.com", "disney.com",
    "hbomax.com", "max.com", "www.max.com",
    "primevideo.com", "amazon.com/video", "amazon.com/gp/video",
    "appletv.com", "tv.apple.com",
    "paramountplus.com", "www.paramountplus.com",
    "peacocktv.com", "www.peacocktv.com",
    "crunchyroll.com", "beta.crunchyroll.com",
    "funimation.com", "www.funimation.com",
}


Tier = Literal["allowlist", "requires_license_note", "denied", "unknown"]


@dataclass
class ClassificationResult:
    """Result of classifying a URL against legal-source rules."""

    tier: Tier
    host: str
    license_description: str | None = None
    reason: str | None = None


def _host_of(url: str) -> str:
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    return (parsed.netloc or "").lower()


def _host_matches(host: str, candidates: dict[str, str] | set[str]) -> str | None:
    """Return the matching domain key if `host` matches, else None."""
    if not host:
        return None
    if isinstance(candidates, dict):
        if host in candidates:
            return host
        for domain in candidates:
            if host.endswith("." + domain):
                return domain
        return None
    # set
    if host in candidates:
        return host
    for domain in candidates:
        if host.endswith("." + domain):
            return domain
    return None


def classify_url(url: str) -> ClassificationResult:
    """Classify a URL into one of: allowlist, requires_license_note, denied, unknown.

    `allowlist` — freely usable for tests and demos.
    `requires_license_note` — OK with an explicit per-URL license justification
        (fair use for official studio trailers, uploader-owned Vimeo content, etc).
    `denied` — streaming services we refuse to scrape.
    `unknown` — nothing matched. Caller should default to the strictest handling
        (treat as `requires_license_note` at minimum).
    """
    host = _host_of(url)

    denied_match = _host_matches(host, DENIED_DOMAINS)
    if denied_match:
        return ClassificationResult(
            tier="denied",
            host=host,
            reason=(
                f"'{denied_match}' is on the denylist — streaming services are "
                f"not a legitimate source for FandomForge edits."
            ),
        )

    allow_match = _host_matches(host, ALLOWLIST_DOMAINS)
    if allow_match:
        return ClassificationResult(
            tier="allowlist",
            host=host,
            license_description=ALLOWLIST_DOMAINS[allow_match],
        )

    note_match = _host_matches(host, REQUIRES_LICENSE_NOTE_DOMAINS)
    if note_match:
        return ClassificationResult(
            tier="requires_license_note",
            host=host,
            reason=(
                f"'{note_match}' is user-generated — OK only with an explicit "
                f"per-URL license_note documenting why the specific content is "
                f"legally usable (e.g. fair use for transformative editing of an "
                f"official studio trailer)."
            ),
        )

    return ClassificationResult(
        tier="unknown",
        host=host,
        reason=(
            f"'{host}' is not in any allowlist, denylist, or fair-use tier. "
            f"Add it to legal_sources.py or require a license_note before fetching."
        ),
    )


__all__ = [
    "ALLOWLIST_DOMAINS",
    "REQUIRES_LICENSE_NOTE_DOMAINS",
    "DENIED_DOMAINS",
    "ClassificationResult",
    "Tier",
    "classify_url",
]
