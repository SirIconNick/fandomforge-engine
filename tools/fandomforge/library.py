"""Global media library — link folders of movies once, query across every project.

One library lives at `~/.cache/fandomforge/library/` (or wherever
`$FANDOMFORGE_CACHE_DIR/library/` points). It holds two SQLite tables:

    roots    — every `ff library link <path>` call becomes one row
    sources  — every ingested file under every root becomes one row

All expensive ingestion artifacts (scenes.json, CLIP .npz, transcript.json) are
content-hashed via Blake2 and written under `$LIB/derived/<hash>/`. A file that
already exists in the library is recognized by hash and never re-ingested.

The library is symlink-friendly — `ff ingest` already resolves symlinks for
hashing, so dropping a link into a project's `raw/` dir reuses the library's
cached artifacts transparently.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


# File extensions we consider media. Upper/lowercase both handled.
MEDIA_EXTS: tuple[str, ...] = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi")


# ---------- Paths ----------


def _cache_root() -> Path:
    """Resolve the writable cache root — mirrors ingest._model_cache_root."""
    explicit = os.environ.get("FANDOMFORGE_CACHE_DIR")
    if explicit:
        p = Path(explicit)
        p.mkdir(parents=True, exist_ok=True)
        return p
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        p = Path(xdg) / "fandomforge"
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p
        except PermissionError:
            pass
    home = Path.home() / ".cache" / "fandomforge"
    try:
        home.mkdir(parents=True, exist_ok=True)
        return home
    except PermissionError:
        pass
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "fandomforge-cache"
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def library_root() -> Path:
    root = _cache_root() / "library"
    root.mkdir(parents=True, exist_ok=True)
    (root / "derived").mkdir(parents=True, exist_ok=True)
    return root


def library_db_path() -> Path:
    return library_root() / "index.db"


def derived_dir_for(source_id: str) -> Path:
    """Global derived path for a content-hash — shared across projects."""
    p = library_root() / "derived" / source_id
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------- DDL ----------


_DDL = """
CREATE TABLE IF NOT EXISTS roots (
    name              TEXT PRIMARY KEY,
    path              TEXT NOT NULL UNIQUE,
    auto_fandom_rule  TEXT NOT NULL DEFAULT 'dir1',
    added_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id                TEXT PRIMARY KEY,         -- blake2 content hash
    path              TEXT NOT NULL UNIQUE,     -- canonical absolute path
    root_name         TEXT,                     -- FK roots.name (nullable if unlinked)
    fandom            TEXT,                     -- e.g. 'Marvel'
    source_type       TEXT DEFAULT 'movie',
    title             TEXT,
    year              INTEGER,
    added_at          TEXT NOT NULL,
    ingest_status     TEXT NOT NULL DEFAULT 'pending',  -- pending/in_progress/done/failed
    ingest_error      TEXT,
    ingested_at       TEXT
);

CREATE INDEX IF NOT EXISTS ix_sources_root   ON sources(root_name);
CREATE INDEX IF NOT EXISTS ix_sources_fandom ON sources(fandom);
CREATE INDEX IF NOT EXISTS ix_sources_status ON sources(ingest_status);
"""


# ---------- Types ----------


@dataclass
class LibraryRoot:
    name: str
    path: Path
    auto_fandom_rule: str
    added_at: str


@dataclass
class LibrarySource:
    id: str                          # blake2 hash
    path: Path
    root_name: str | None
    fandom: str | None
    source_type: str
    title: str | None
    year: int | None
    added_at: str
    ingest_status: str               # pending/in_progress/done/failed
    ingest_error: str | None
    ingested_at: str | None


@dataclass
class ScanResult:
    discovered: int = 0              # files walked under the root
    added: int = 0                   # new sources added to index
    already_indexed: int = 0         # already present, skipped
    ingested: int = 0                # ingest succeeded during this scan
    reingest_skipped: int = 0        # derived/ cache hit, no re-ingest
    failed: int = 0
    errors: list[str] = field(default_factory=list)


# ---------- Connection ----------


def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(library_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_DDL)
    conn.commit()
    return conn


# ---------- Fandom inference ----------


_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_TITLE_CLEANUP_RE = re.compile(r"[._\-]+")


def _titleize(name: str) -> str:
    # Drop a trailing file extension, but safely — don't let Path() reinterpret
    # separators like '/' in a freeform label such as "Marvel / MCU".
    if "." in name and "/" not in name:
        stem = name.rsplit(".", 1)[0]
    else:
        stem = name
    stem = _TITLE_CLEANUP_RE.sub(" ", stem).strip()
    # Drop trailing year if present
    m = _YEAR_RE.search(stem)
    if m and m.end() > len(stem) - 6:
        stem = stem[: m.start()].strip()
    return " ".join(word.capitalize() for word in stem.split())


def _extract_year(name: str) -> int | None:
    m = _YEAR_RE.search(name)
    if not m:
        return None
    try:
        y = int(m.group(0))
    except ValueError:
        return None
    if 1900 <= y <= 2100:
        return y
    return None


def infer_fandom(
    path: Path,
    root_path: Path,
    rule: str = "dir1",
) -> str:
    """Derive a fandom label from a file path using a configurable rule.

    Rules:
      dir1                  — first directory component under root (default)
      dir2                  — two levels deep (joined with ' / ')
      filename-before-year  — everything before the year marker in the filename
      manual                — always returns 'Unknown'
    """
    try:
        rel = path.resolve().relative_to(root_path.resolve())
    except ValueError:
        rel = Path(path.name)

    parts = rel.parts[:-1]  # drop the filename

    if rule == "manual":
        return "Unknown"

    if rule == "dir2" and len(parts) >= 2:
        return _titleize(f"{parts[0]} / {parts[1]}")

    if rule == "filename-before-year":
        m = _YEAR_RE.search(path.name)
        if m:
            return _titleize(path.name[: m.start()])

    # dir1 (default)
    if parts:
        return _titleize(parts[0])

    # No parent dir — fall back to filename prefix.
    return _titleize(path.name.split(".")[0])


# ---------- Walker ----------


def walk_media(root: Path) -> Iterator[Path]:
    """Recursively yield media files under root. Hidden dirs skipped."""
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        if p.suffix.lower() in MEDIA_EXTS:
            yield p


# ---------- CRUD — roots ----------


def link_root(
    path: Path | str,
    name: str | None = None,
    auto_fandom_rule: str = "dir1",
) -> LibraryRoot:
    path = Path(path).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"not a directory: {path}")
    if name is None:
        name = path.name or f"root-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    now = datetime.now(timezone.utc).isoformat()
    with open_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO roots (name, path, auto_fandom_rule, added_at) VALUES (?, ?, ?, ?)",
            (name, str(path), auto_fandom_rule, now),
        )
        conn.commit()
    return LibraryRoot(name=name, path=path, auto_fandom_rule=auto_fandom_rule, added_at=now)


def list_roots() -> list[LibraryRoot]:
    with open_db() as conn:
        rows = conn.execute(
            "SELECT name, path, auto_fandom_rule, added_at FROM roots ORDER BY added_at"
        ).fetchall()
    return [
        LibraryRoot(
            name=r["name"],
            path=Path(r["path"]),
            auto_fandom_rule=r["auto_fandom_rule"],
            added_at=r["added_at"],
        )
        for r in rows
    ]


def get_root(name: str) -> LibraryRoot | None:
    with open_db() as conn:
        row = conn.execute(
            "SELECT name, path, auto_fandom_rule, added_at FROM roots WHERE name=?",
            (name,),
        ).fetchone()
    if not row:
        return None
    return LibraryRoot(
        name=row["name"],
        path=Path(row["path"]),
        auto_fandom_rule=row["auto_fandom_rule"],
        added_at=row["added_at"],
    )


def unlink_root(name: str, *, delete_sources: bool = False) -> int:
    """Forget a linked folder. By default keeps the ingested source rows
    (they still resolve to real files). Pass delete_sources=True to also
    drop the source rows — derived/ artifacts stay on disk either way."""
    with open_db() as conn:
        if delete_sources:
            cur = conn.execute("DELETE FROM sources WHERE root_name=?", (name,))
            deleted = cur.rowcount
        else:
            cur = conn.execute(
                "UPDATE sources SET root_name=NULL WHERE root_name=?",
                (name,),
            )
            deleted = cur.rowcount
        conn.execute("DELETE FROM roots WHERE name=?", (name,))
        conn.commit()
    return deleted


# ---------- CRUD — sources ----------


def register_source(
    *,
    source_id: str,
    path: Path,
    root_name: str | None,
    fandom: str | None,
    source_type: str = "movie",
    title: str | None = None,
    year: int | None = None,
) -> LibrarySource:
    now = datetime.now(timezone.utc).isoformat()
    path_str = str(Path(path).resolve())
    with open_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO sources
               (id, path, root_name, fandom, source_type, title, year, added_at, ingest_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (source_id, path_str, root_name, fandom, source_type, title, year, now),
        )
        # If the row already existed, still refresh the mutable metadata.
        conn.execute(
            """UPDATE sources
               SET root_name = COALESCE(?, root_name),
                   fandom    = COALESCE(?, fandom),
                   title     = COALESCE(?, title),
                   year      = COALESCE(?, year)
             WHERE id = ?""",
            (root_name, fandom, title, year, source_id),
        )
        conn.commit()
    return _fetch_source(source_id)


def _fetch_source(source_id: str) -> LibrarySource:
    with open_db() as conn:
        row = conn.execute(
            """SELECT id, path, root_name, fandom, source_type, title, year,
                      added_at, ingest_status, ingest_error, ingested_at
                 FROM sources WHERE id=?""",
            (source_id,),
        ).fetchone()
    if not row:
        raise KeyError(source_id)
    return _row_to_source(row)


def _row_to_source(row: sqlite3.Row) -> LibrarySource:
    return LibrarySource(
        id=row["id"],
        path=Path(row["path"]),
        root_name=row["root_name"],
        fandom=row["fandom"],
        source_type=row["source_type"] or "movie",
        title=row["title"],
        year=row["year"],
        added_at=row["added_at"],
        ingest_status=row["ingest_status"],
        ingest_error=row["ingest_error"],
        ingested_at=row["ingested_at"],
    )


def list_sources(
    *,
    root_name: str | None = None,
    fandom: str | None = None,
    status: str | None = None,
) -> list[LibrarySource]:
    q = "SELECT * FROM sources WHERE 1=1"
    params: list = []
    if root_name:
        q += " AND root_name=?"
        params.append(root_name)
    if fandom:
        q += " AND fandom=?"
        params.append(fandom)
    if status:
        q += " AND ingest_status=?"
        params.append(status)
    q += " ORDER BY added_at DESC"
    with open_db() as conn:
        rows = conn.execute(q, params).fetchall()
    return [_row_to_source(r) for r in rows]


def tag_source(path: Path | str, *, fandom: str) -> LibrarySource:
    """Override the fandom label for one file. Matches by canonical path."""
    path_str = str(Path(path).expanduser().resolve())
    with open_db() as conn:
        cur = conn.execute(
            "UPDATE sources SET fandom=? WHERE path=? RETURNING id",
            (fandom, path_str),
        )
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise KeyError(f"no library source at {path_str}")
    return _fetch_source(row["id"])


def fandom_counts() -> dict[str, int]:
    with open_db() as conn:
        rows = conn.execute(
            """SELECT fandom, COUNT(*) AS n
                 FROM sources
                GROUP BY fandom
                ORDER BY n DESC"""
        ).fetchall()
    return {(r["fandom"] or "(none)"): r["n"] for r in rows}


def set_ingest_status(source_id: str, status: str, error: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat() if status == "done" else None
    with open_db() as conn:
        conn.execute(
            """UPDATE sources
                  SET ingest_status=?, ingest_error=?, ingested_at=COALESCE(?, ingested_at)
                WHERE id=?""",
            (status, error, now, source_id),
        )
        conn.commit()


# ---------- Scan (link + walk + register) ----------


def scan_root(root_name: str) -> ScanResult:
    """Walk a linked root and register every media file found. Returns counters.

    Does NOT trigger ingestion itself — a separate caller (CLI) runs
    `ff ingest` per source after scanning. This separation keeps the walker
    cheap and lets callers parallelise / rate-limit ingestion.
    """
    root = get_root(root_name)
    if root is None:
        raise KeyError(f"no library root named {root_name}")

    result = ScanResult()
    # Lazy import so library.py stays import-light for tests that stub ingest.
    from fandomforge.ingest import _blake2_hash

    seen_paths: set[str] = set()
    with open_db() as conn:
        for row in conn.execute(
            "SELECT path FROM sources WHERE root_name=?", (root_name,)
        ):
            seen_paths.add(row["path"])

    for file_path in walk_media(root.path):
        result.discovered += 1
        canonical = str(file_path.resolve())
        if canonical in seen_paths:
            result.already_indexed += 1
            continue

        try:
            source_id = _blake2_hash(file_path)
        except OSError as exc:
            result.failed += 1
            result.errors.append(f"hash failed: {file_path.name}: {exc}")
            continue

        fandom = infer_fandom(file_path, root.path, rule=root.auto_fandom_rule)
        title = _titleize(file_path.name)
        year = _extract_year(file_path.name)

        register_source(
            source_id=source_id,
            path=file_path,
            root_name=root.name,
            fandom=fandom,
            title=title,
            year=year,
        )
        result.added += 1

    return result


def scan_all() -> dict[str, ScanResult]:
    return {r.name: scan_root(r.name) for r in list_roots()}


# ---------- Query helper for --from-library ----------


def candidate_sources(
    *,
    fandoms: dict[str, float] | None = None,
    min_status: str = "done",
) -> list[LibrarySource]:
    """Return sources that match the fandom filter. Keys in `fandoms` are
    case-insensitive; the values (weights) aren't applied here — sampling
    downstream uses them."""
    q = "SELECT * FROM sources WHERE ingest_status=?"
    params: list = [min_status]
    if fandoms:
        names = list(fandoms.keys())
        placeholders = ",".join("?" for _ in names)
        q += f" AND lower(fandom) IN ({placeholders})"
        params.extend([n.lower() for n in names])
    with open_db() as conn:
        rows = conn.execute(q, params).fetchall()
    return [_row_to_source(r) for r in rows]


__all__ = [
    "MEDIA_EXTS",
    "LibraryRoot",
    "LibrarySource",
    "ScanResult",
    "library_root",
    "library_db_path",
    "derived_dir_for",
    "infer_fandom",
    "walk_media",
    "link_root",
    "list_roots",
    "get_root",
    "unlink_root",
    "register_source",
    "list_sources",
    "tag_source",
    "fandom_counts",
    "set_ingest_status",
    "scan_root",
    "scan_all",
    "candidate_sources",
]
