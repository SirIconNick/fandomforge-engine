"""Human-correction journal — the teacher-labeled dataset.

Every time the user corrects the engine's auto-classification via the
web UI, a correction lands here. Each entry is one row in a JSONL file
at ``~/.fandomforge/training/corrections.jsonl`` (or repo-local fallback
when the home dir is sandboxed).

Correction entries are consumed by ``forensic_craft_bias`` as a third
bias layer (priority over corpus-median bias, below hand-tuned table).
When the same video gets corrected more than once, the newest correction
wins — past entries stay in the journal for audit but the aggregator
keys on ``forensic_id``.

This is how the engine gets better with user input without retraining.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from fandomforge.intelligence.training_journal import (
    _can_write_to,
    _repo_local_journal,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CorrectionEntry",
    "append_correction",
    "delete_corrections_for",
    "iter_corrections",
    "corrections_path",
    "corrections_for_bucket",
    "latest_correction_for",
    "corrections_summary",
]


_DEFAULT_CORRECTIONS = Path.home() / ".fandomforge" / "training" / "corrections.jsonl"
_CORRECTIONS_ENV = "FF_CORRECTIONS_JOURNAL"


def _repo_local_corrections() -> Path | None:
    here = Path(__file__).resolve()
    # Prefer the outermost .git (the real repo root), only fall back to
    # pyproject.toml when no .git is found. Walking a single loop that
    # matches either would stop at tools/pyproject.toml and miss the
    # actual repo-level .cache directory.
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent / ".cache" / "ff" / "training" / "corrections.jsonl"
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent / ".cache" / "ff" / "training" / "corrections.jsonl"
    return None


def corrections_path() -> Path:
    override = os.environ.get(_CORRECTIONS_ENV)
    if override:
        return Path(override)
    if _can_write_to(_DEFAULT_CORRECTIONS):
        return _DEFAULT_CORRECTIONS
    local = _repo_local_corrections()
    if local is not None:
        return local
    return _DEFAULT_CORRECTIONS


@dataclass
class CorrectionEntry:
    """One user correction on a forensic analysis."""

    forensic_id: str
    url: str = ""
    title: str = ""
    original_bucket: str = ""
    corrected_bucket: str = ""
    original_craft_weights: dict[str, float] = field(default_factory=dict)
    corrected_craft_weights: dict[str, float] = field(default_factory=dict)
    tags_added: list[str] = field(default_factory=list)
    tags_removed: list[str] = field(default_factory=list)
    notes: str = ""
    user: str = "nick"
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def append_correction(entry: CorrectionEntry, *, path: Path | None = None) -> Path:
    p = Path(path) if path else corrections_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(payload)
        f.write("\n")
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    logger.info("correction recorded: %s -> %s", entry.forensic_id, entry.corrected_bucket)
    return p


def iter_corrections(path: Path | None = None) -> Iterator[CorrectionEntry]:
    p = Path(path) if path else corrections_path()
    if not p.exists():
        return
    with p.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            valid_keys = {f.name for f in CorrectionEntry.__dataclass_fields__.values()}
            data = {k: v for k, v in data.items() if k in valid_keys}
            try:
                yield CorrectionEntry(**data)
            except TypeError:
                continue


def latest_correction_for(forensic_id: str, path: Path | None = None) -> CorrectionEntry | None:
    """Return the most recent correction for ``forensic_id`` if any."""
    latest: CorrectionEntry | None = None
    for entry in iter_corrections(path=path):
        if entry.forensic_id != forensic_id:
            continue
        if latest is None or entry.timestamp > latest.timestamp:
            latest = entry
    return latest


def corrections_for_bucket(bucket: str, path: Path | None = None) -> list[CorrectionEntry]:
    """Return every correction targeting ``bucket`` ordered oldest → newest."""
    entries = [e for e in iter_corrections(path=path) if e.corrected_bucket == bucket]
    entries.sort(key=lambda e: e.timestamp)
    return entries


def delete_corrections_for(
    forensic_id: str,
    path: Path | None = None,
) -> int:
    """Rewrite the journal to drop every entry matching ``forensic_id``.

    Returns the number of deleted entries. This is an O(n) rewrite since
    the journal is JSONL — acceptable given the size (corrections are
    low-frequency user actions). Writes atomically via a temp file + rename
    so a crash mid-write can't corrupt the journal.
    """
    p = Path(path) if path else corrections_path()
    if not p.exists():
        return 0
    kept: list[dict[str, Any]] = []
    deleted = 0
    with p.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                kept.append({"__raw__": raw})
                continue
            if data.get("forensic_id") == forensic_id:
                deleted += 1
                continue
            kept.append(data)
    if deleted == 0:
        return 0
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in kept:
            if "__raw__" in row:
                f.write(row["__raw__"])
            else:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            f.write("\n")
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    tmp.replace(p)
    return deleted


def corrections_summary(path: Path | None = None) -> dict[str, Any]:
    entries = list(iter_corrections(path=path))
    if not entries:
        return {
            "total": 0,
            "path": str(path or corrections_path()),
            "message": "no corrections yet",
        }
    per_bucket: dict[str, int] = {}
    reclassifications: list[tuple[str, str]] = []
    for e in entries:
        per_bucket[e.corrected_bucket] = per_bucket.get(e.corrected_bucket, 0) + 1
        if e.original_bucket and e.corrected_bucket != e.original_bucket:
            reclassifications.append((e.original_bucket, e.corrected_bucket))
    return {
        "total": len(entries),
        "path": str(path or corrections_path()),
        "per_bucket": per_bucket,
        "reclassifications": [
            {"from": src, "to": dst}
            for src, dst in reclassifications
        ],
    }
