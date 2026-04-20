"""JSON Schemas for every FandomForge artifact.

Every pipeline handoff is typed. Loading a schema returns a parsed dict; use
`fandomforge.validation.validate(data, schema_id)` to enforce it.

Schema IDs (canonical stems, no `.schema.json`):

    beat-map
    project-config
    catalog
    shot-list
    color-plan
    transition-plan
    audio-plan
    title-plan
    edit-plan
    source-catalog
    transcript
    scenes
    qa-report
    fandoms
    emotion-arc
    share-config
    webhooks
    post-render-review
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

__all__ = ["SCHEMA_IDS", "load_schema", "list_schemas"]


SCHEMA_IDS: tuple[str, ...] = (
    "beat-map",
    "project-config",
    "catalog",
    "shot-list",
    "color-plan",
    "transition-plan",
    "audio-plan",
    "title-plan",
    "edit-plan",
    "source-catalog",
    "transcript",
    "scenes",
    "qa-report",
    "fandoms",
    "emotion-arc",
    "share-config",
    "webhooks",
    "post-render-review",
    "sync-plan",
    "sfx-plan",
    "complement-plan",
    "reference-priors",
    "energy-zones",
    "dialogue-windows",
    "dialogue-placement-plan",
    "clip-category",
    "intent",
)


@lru_cache(maxsize=None)
def load_schema(schema_id: str) -> dict[str, Any]:
    """Load a schema dict by its canonical id (without `.schema.json`).

    Raises:
        KeyError: if `schema_id` isn't a known schema
    """
    if schema_id not in SCHEMA_IDS:
        raise KeyError(
            f"Unknown schema id '{schema_id}'. "
            f"Known ids: {', '.join(SCHEMA_IDS)}"
        )
    text = resources.files("fandomforge.schemas").joinpath(
        f"{schema_id}.schema.json"
    ).read_text(encoding="utf-8")
    return json.loads(text)


def list_schemas() -> list[str]:
    """Return the list of known schema ids (alphabetical)."""
    return sorted(SCHEMA_IDS)
