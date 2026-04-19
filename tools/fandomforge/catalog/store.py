"""Clip catalog — a persistent JSON store of clips the user has referenced or used."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Clip:
    """A single clip reference in the catalog."""

    id: str
    source_title: str
    source_type: str  # "movie", "tv", "anime", "game", "other"
    fandom: str  # free-text fandom label ("Marvel", "LOTR", "Naruto")
    timestamp: str  # HH:MM:SS format
    duration_sec: float
    description: str
    mood_tags: list[str] = field(default_factory=list)
    framing: str = ""  # "wide", "medium", "CU", etc.
    motion: str = ""  # "static", "push-in", "whip pan", etc.
    color_notes: str = ""
    notes: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Clip:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class Catalog:
    """File-backed clip catalog.

    Stores clips in a JSON file. Keeps a single source of truth for what
    clips the user has referenced across projects.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._clips: dict[str, Clip] = {}
        self._loaded = False

    def load(self) -> None:
        if not self.path.exists():
            self._loaded = True
            return
        with self.path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        self._clips = {c["id"]: Clip.from_dict(c) for c in raw.get("clips", [])}
        self._loaded = True

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "clips": [c.to_dict() for c in self._clips.values()],
        }
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    def add(self, clip: Clip) -> None:
        if not self._loaded:
            self.load()
        if not clip.created_at:
            clip.created_at = datetime.now(timezone.utc).isoformat()
        if clip.id in self._clips:
            raise ValueError(f"Clip id already exists: {clip.id}")
        self._clips[clip.id] = clip
        self.save()

    def remove(self, clip_id: str) -> bool:
        if not self._loaded:
            self.load()
        if clip_id not in self._clips:
            return False
        del self._clips[clip_id]
        self.save()
        return True

    def get(self, clip_id: str) -> Clip | None:
        if not self._loaded:
            self.load()
        return self._clips.get(clip_id)

    def all(self) -> list[Clip]:
        if not self._loaded:
            self.load()
        return list(self._clips.values())

    def find_by_fandom(self, fandom: str) -> list[Clip]:
        return [c for c in self.all() if c.fandom.lower() == fandom.lower()]

    def find_by_mood(self, mood: str) -> list[Clip]:
        mood_lower = mood.lower()
        return [c for c in self.all() if mood_lower in [m.lower() for m in c.mood_tags]]

    def search(self, query: str) -> list[Clip]:
        q = query.lower()
        results: list[Clip] = []
        for c in self.all():
            hay = " ".join(
                [
                    c.source_title,
                    c.fandom,
                    c.description,
                    c.notes,
                    c.color_notes,
                    " ".join(c.mood_tags),
                ]
            ).lower()
            if q in hay:
                results.append(c)
        return results
