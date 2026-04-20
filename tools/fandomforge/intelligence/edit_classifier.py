"""Edit-type auto-detection from prompt / edit-plan text.

When a project doesn't declare `edit_type` in its config, this module picks
one from the edit-plan's theme/concept text. Keyword-based heuristic —
fast, deterministic, no ML. Falls back to 'action' when nothing matches,
since that's the default multifandom style in our corpus.

The matcher looks for strong and weak signal keywords per type. Strong
signals (like 'tribute', 'shipping', 'AMV') short-circuit the decision;
weak signals vote for a type and the highest-voted wins.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# --- strong signals: single-word matches that short-circuit classification ---
_STRONG_SIGNALS: dict[str, tuple[str, ...]] = {
    "tribute": ("tribute", "memoriam", "memorial", "legacy"),
    "shipping": ("shipping", "shipper", "pairing", "ship edit", "x ", "otp"),
    "speed_amv": ("amv", "speed edit", "speedpaint"),
    "cinematic": ("cinematic", "short film", "slow burn", "slowburn"),
    "comedy": ("meme", "comedy edit", "funny moments", "crack"),
    "hype_trailer": ("trailer", "fan trailer", "teaser"),
    "emotional": ("grief", "mourning", "eulogy", "farewell"),
    "action": ("hype edit", "action edit", "combat", "fight edit"),
}


# --- weak signals: thematic keywords that vote for types ---
_WEAK_SIGNALS: dict[str, tuple[str, ...]] = {
    "action": (
        "fight", "battle", "chase", "combat", "war", "weapon", "punch",
        "kick", "gun", "explosion", "violence", "rage", "fire", "storm",
        "hero", "villain", "revenge",
    ),
    "emotional": (
        "loss", "grief", "sad", "melancholy", "tears", "goodbye", "lonely",
        "alone", "broken", "regret", "remember", "tender", "vulnerable",
        "quiet", "reflection",
    ),
    "tribute": (
        "character", "arc", "journey", "story", "evolution",
        "growth", "transformation", "through the years", "retrospective",
    ),
    "shipping": (
        "romance", "love", "romantic", "couple", "together", "kiss",
        "embrace", "relationship", "devotion", "connection",
    ),
    "speed_amv": (
        "fast", "rapid", "beat drop", "hard-hitting", "phonk",
        "hyperpop", "speed", "impact",
    ),
    "cinematic": (
        "narrative", "story-driven", "motivated", "performance",
        "arthouse", "subtle", "atmospheric", "contemplative",
    ),
    "comedy": (
        "funny", "humor", "joke", "laugh", "absurd", "ridiculous",
        "awkward", "satire", "parody",
    ),
    "hype_trailer": (
        "epic", "rise", "dawn", "becoming", "origin", "build",
        "stakes", "announce", "reveal", "coming", "teaser",
    ),
}


DEFAULT_TYPE = "action"


@lru_cache(maxsize=1)
def load_type_registry() -> dict[str, Any]:
    """Load the edit-types priors file once per process."""
    here = Path(__file__).resolve()
    path = here.parent.parent / "data" / "edit-types.json"
    return json.loads(path.read_text(encoding="utf-8"))


def available_types() -> list[str]:
    return list(load_type_registry().get("types", {}).keys())


def classify_edit_type(text: str | None) -> str:
    """Return the best-matched edit_type for the given text.

    Empty text → DEFAULT_TYPE. Strong signals beat weak signals. Ties break
    to the first type in the iteration order of _STRONG_SIGNALS / _WEAK_SIGNALS.
    """
    if not text:
        return DEFAULT_TYPE

    normalized = text.lower()

    # Strong signals — first match wins
    for t, needles in _STRONG_SIGNALS.items():
        for n in needles:
            if n in normalized:
                return t

    # Weak signal voting
    votes: dict[str, int] = {t: 0 for t in _WEAK_SIGNALS}
    tokens = set(re.findall(r"[a-zA-Z']+", normalized))
    for t, needles in _WEAK_SIGNALS.items():
        for n in needles:
            # Multi-word phrases need substring match; single words use token set
            if " " in n or "-" in n:
                if n in normalized:
                    votes[t] += 1
            else:
                if n in tokens:
                    votes[t] += 1

    best_t = max(votes, key=lambda t: votes[t])
    return best_t if votes[best_t] > 0 else DEFAULT_TYPE


def load_type_priors(edit_type: str) -> dict[str, Any] | None:
    """Return the priors block for a given edit_type, or None if unknown."""
    registry = load_type_registry()
    return (registry.get("types") or {}).get(edit_type)


def resolve_edit_type(
    project_config: dict[str, Any] | None,
    edit_plan: dict[str, Any] | None,
) -> tuple[str, str]:
    """Decide which edit_type to apply for a project.

    Order: explicit project_config.edit_type > classify(edit_plan.concept) > DEFAULT.
    Returns (edit_type, source) where source explains how the decision was
    made: 'config', 'classified', or 'default'.
    """
    if project_config and isinstance(project_config.get("edit_type"), str):
        t = project_config["edit_type"]
        if t in available_types():
            return t, "config"
        logger.warning("project_config.edit_type=%r not in registry; ignoring", t)

    if edit_plan:
        concept = edit_plan.get("concept") or {}
        if isinstance(concept, dict):
            bag = " ".join(str(v) for v in concept.values() if isinstance(v, str))
            theme = concept.get("theme") or ""
            one_sentence = concept.get("one_sentence") or ""
            signal = " ".join([str(theme), str(one_sentence), bag])
            if signal.strip():
                t = classify_edit_type(signal)
                return t, "classified"

    return DEFAULT_TYPE, "default"


__all__ = [
    "DEFAULT_TYPE",
    "available_types",
    "classify_edit_type",
    "load_type_priors",
    "load_type_registry",
    "resolve_edit_type",
]
