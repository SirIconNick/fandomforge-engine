"""Per-export media manifest: every asset the project references with a hash
and absolute path, so the user can verify nothing is missing before opening
the project in their NLE.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ManifestEntry:
    role: str
    path: str
    exists: bool
    size_bytes: int
    blake2b: str


def _hash_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.blake2b(digest_size=16)
    with path.open("rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def write_media_manifest(
    *,
    output_path: Path,
    layout: Any,  # BinLayout; import-lazy to avoid cycles
    project_slug: str,
) -> dict[str, Any]:
    entries: list[ManifestEntry] = []
    missing: list[str] = []
    for entry in layout.all_entries():
        p = entry.path
        exists = p.exists() and p.is_file()
        size = p.stat().st_size if exists else 0
        digest = _hash_file(p) if exists else ""
        entries.append(ManifestEntry(
            role=entry.role,
            path=str(p.resolve() if exists else p),
            exists=exists,
            size_bytes=size,
            blake2b=digest,
        ))
        if not exists:
            missing.append(str(p))

    manifest = {
        "project_slug": project_slug,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entry_count": len(entries),
        "missing_count": len(missing),
        "entries": [asdict(e) for e in entries],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
