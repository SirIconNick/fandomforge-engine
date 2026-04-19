"""Cliche shot detector.

Parses docs/knowledge/overused-shots-to-avoid.md at import time into a
per-fandom dictionary of known-overused shot patterns, then provides
`is_cliche(description, fandom=None)` that returns a match hit with the
specific phrase and the fandom it came from.

QA gate (Phase 3) uses this to reject shots flagged as cliche unless the
shot has an `override_reason` field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

__all__ = [
    "ClicheHit",
    "load_cliche_patterns",
    "is_cliche",
    "matches_for_fandom",
]


@dataclass(frozen=True)
class ClicheHit:
    fandom: str
    phrase: str
    score: float


def _default_doc_path() -> Path:
    """Locate the cliche doc whether we're running from a source checkout or an
    installed package. Walks up from this file looking for docs/knowledge/…."""
    here = Path(__file__).resolve()
    for ancestor in [here, *here.parents]:
        candidate = ancestor / "docs" / "knowledge" / "overused-shots-to-avoid.md"
        if candidate.exists():
            return candidate
    # Last-ditch: repo root / docs / knowledge / ...
    return here.parents[3] / "docs" / "knowledge" / "overused-shots-to-avoid.md"


_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^-\s+(.+?)\s*$")
_OVERUSED_HEADER_RE = re.compile(r"^\*\*Overused:?\*\*\s*$", re.IGNORECASE)
_OTHER_BOLD_RE = re.compile(r"^\*\*([^*]+)\*\*\s*$")


@lru_cache(maxsize=1)
def load_cliche_patterns(doc_path: str | None = None) -> dict[str, list[str]]:
    """Parse the overused-shots doc into {fandom: [phrase, ...]}.

    Only bullets under the `**Overused:**` section of each `## Fandom` heading
    are collected. 'Still works if' / 'Use instead' lists are skipped — those
    are inspirational, not exclusion rules.
    """
    path = Path(doc_path) if doc_path else _default_doc_path()
    if not path.exists():
        return {}

    patterns: dict[str, list[str]] = {}
    current_fandom: str | None = None
    in_overused = False

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line:
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            current_fandom = heading.group(1).strip()
            patterns.setdefault(current_fandom, [])
            in_overused = False
            continue

        if _OVERUSED_HEADER_RE.match(line):
            in_overused = True
            continue

        if _OTHER_BOLD_RE.match(line):
            # Any other `**Heading**` ends the overused section.
            in_overused = False
            continue

        if current_fandom and in_overused:
            bullet = _BULLET_RE.match(line)
            if bullet:
                text = bullet.group(1)
                # Keep only the part before an em-dash-ish note or parenthetical
                text = re.sub(r"\s+\(.*?\)\s*", " ", text)
                text = re.sub(r"\s+-\s+.*$", "", text)
                text = text.strip()
                if text:
                    patterns[current_fandom].append(text)

    # Drop empty fandoms (headings with no overused items).
    return {k: v for k, v in patterns.items() if v}


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def _phrase_score(query: str, phrase: str) -> float:
    """Bag-of-tokens overlap between a shot description and a cliche phrase.

    1.0 = every word in the shorter token list appears in the other.
    0.0 = no overlap. We boost exact substring matches.
    """
    q_tokens = set(_normalize(query).split())
    p_tokens = set(_normalize(phrase).split())
    if not q_tokens or not p_tokens:
        return 0.0
    shorter = q_tokens if len(q_tokens) <= len(p_tokens) else p_tokens
    longer = p_tokens if shorter is q_tokens else q_tokens
    hits = sum(1 for t in shorter if t in longer)
    base = hits / max(1, len(shorter))
    if _normalize(phrase) in _normalize(query):
        base = min(1.0, base + 0.3)
    return base


def matches_for_fandom(fandom: str) -> list[str]:
    """Return the list of overused phrases for a given fandom, or [] if unknown."""
    patterns = load_cliche_patterns()
    # Case-insensitive fandom match — the doc uses "Star Wars" but edits may
    # write "star wars" or "SW".
    norm = _normalize(fandom)
    for k, v in patterns.items():
        if _normalize(k) == norm:
            return list(v)
    # Partial match fallback (e.g. "MCU" should hit "Marvel / MCU").
    for k, v in patterns.items():
        if norm and norm in _normalize(k):
            return list(v)
    return []


def is_cliche(
    description: str,
    fandom: str | None = None,
    *,
    threshold: float = 0.75,
) -> ClicheHit | None:
    """Return the best cliche hit for the given description, or None.

    Args:
        description: Shot description or combined description+mood_tags.
        fandom: Optional fandom name. If given, only phrases from that fandom
            are considered. Otherwise every fandom is searched.
        threshold: Score above which a match is considered a hit (0..1).
    """
    patterns = load_cliche_patterns()
    if not patterns:
        return None

    best: ClicheHit | None = None
    targets = {fandom: matches_for_fandom(fandom)} if fandom else patterns
    for fname, phrases in targets.items():
        for phrase in phrases:
            s = _phrase_score(description, phrase)
            if s >= threshold and (best is None or s > best.score):
                best = ClicheHit(fandom=fname, phrase=phrase, score=round(s, 3))
    return best
