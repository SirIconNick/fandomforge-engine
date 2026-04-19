"""Source catalog — JSON-backed registry of external video sources."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class Source:
    """A single external video source (YouTube, rental, Blu-ray, etc.)."""

    id: str
    game: str
    title: str
    url: str
    duration: str = ""
    channel: str = ""
    views: int = 0
    quality: str = ""
    priority: str = "secondary"  # primary / secondary / backup / archive / optional
    contains: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Source:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class SourceCatalog:
    """File-backed source catalog.

    A catalog is a JSON file with a list of `Source` entries, plus project metadata
    and sourcing notes. Each project can have its own catalog at
    `projects/<slug>/sources.json`.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._sources: dict[str, Source] = {}
        self._meta: dict[str, Any] = {}
        self._loaded = False

    def load(self) -> None:
        if not self.path.exists():
            self._loaded = True
            return
        with self.path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        self._meta = {k: v for k, v in raw.items() if k != "sources"}
        self._sources = {
            s["id"]: Source.from_dict(s) for s in raw.get("sources", [])
        }
        self._loaded = True

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            **self._meta,
            "sources": [s.to_dict() for s in self._sources.values()],
        }
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    def add(self, source: Source) -> None:
        if not self._loaded:
            self.load()
        self._sources[source.id] = source
        self.save()

    def remove(self, source_id: str) -> bool:
        if not self._loaded:
            self.load()
        if source_id not in self._sources:
            return False
        del self._sources[source_id]
        self.save()
        return True

    def get(self, source_id: str) -> Source | None:
        if not self._loaded:
            self.load()
        return self._sources.get(source_id)

    def all(self) -> list[Source]:
        if not self._loaded:
            self.load()
        return list(self._sources.values())

    def by_priority(self, priority: str) -> list[Source]:
        return [s for s in self.all() if s.priority == priority]

    def containing(self, character_or_tag: str) -> list[Source]:
        q = character_or_tag.lower()
        return [
            s
            for s in self.all()
            if any(q in c.lower() for c in s.contains)
        ]
