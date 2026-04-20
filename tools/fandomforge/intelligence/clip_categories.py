"""Loader + helpers for the canonical clip-category taxonomy.

Other modules import the enum from here rather than redeclaring it. The
data itself lives in `data/clip-categories.json` so it's editable without
changing code, and the schema enforcement happens at validation time.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

CLIP_CATEGORY_IDS: tuple[str, ...] = (
    "establishing",
    "action-high",
    "action-mid",
    "reaction-quiet",
    "reaction-emotional",
    "dialogue-primary",
    "dialogue-reaction",
    "transitional",
    "climactic",
    "resolution",
    "texture",
)


@lru_cache(maxsize=1)
def load_clip_categories() -> dict[str, Any]:
    """Load + validate the canonical clip-category data file.

    Cached for the lifetime of the process. Modules can call this freely;
    the JSON is parsed once.
    """
    from fandomforge.validation import validate

    p = Path(__file__).resolve().parent.parent / "data" / "clip-categories.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    validate(data, "clip-category")
    return data


def categories() -> list[dict[str, Any]]:
    """Return the list of category records (id + label + description + biases)."""
    return list(load_clip_categories()["categories"])


def category(category_id: str) -> dict[str, Any]:
    """Look up a single category record by id. Raises KeyError if unknown."""
    for c in categories():
        if c["id"] == category_id:
            return c
    raise KeyError(f"Unknown clip category id: {category_id}. "
                   f"Known: {', '.join(CLIP_CATEGORY_IDS)}")


def edit_type_bias(category_id: str, edit_type: str) -> float:
    """Per-edit-type bias multiplier for a category. 1.0 if not specified
    (neutral). Used by the slot-fit scorer to weight a candidate clip's
    category fit for the active edit type."""
    rec = category(category_id)
    biases = rec.get("edit_type_bias") or {}
    return float(biases.get(edit_type, 1.0))


def categories_for_zone(zone_label: str) -> list[str]:
    """Return the category ids whose energy_zone_affinity includes the
    given zone_label. Convenience for slot-fit + planner code that asks
    'what kinds of shots fit a low-energy slot?'"""
    out: list[str] = []
    for c in categories():
        if zone_label in (c.get("energy_zone_affinity") or []):
            out.append(c["id"])
    return out
