"""SQLite-backed shot library for fast semantic queries over thousands of shots.

Replaces the loose-JSON scene_library.py approach with a structured relational
store that supports filtering by era, character, action, emotion, setting,
duration, and usage history.

Vision captioning is handled upstream by scene_library.py. This module only
persists what is already captioned and exposes a clean query API.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants -- attribute vocabularies used for tag extraction
# ---------------------------------------------------------------------------

_ERA_PATTERNS: list[tuple[str, str]] = [
    (r"re2r|resident[-_]evil[-_]2[-_]remake", "RE2R-1998"),
    (r"re4r|resident[-_]evil[-_]4[-_]remake", "RE4R-2004"),
    (r"re6|resident[-_]evil[-_]6", "RE6-2013"),
    (r"damnation", "Damnation-2011"),
    (r"infinite[-_]darkness|leon[-_]infinite|leon-id", "ID-2021"),
    (r"vendetta", "Vendetta-2017"),
    (r"^re9[-_]|[-_]re9[-_]|[-_]re9$", "RE9-2026"),
]

_CHARACTERS: set[str] = {"leon", "grace", "victor", "enemy", "ashley", "claire"}

_ACTIONS: set[str] = {
    "aiming", "walking", "shooting", "standing", "wounded", "holding_gun",
    "talking", "running", "fighting", "driving", "pointing", "reading",
    "listening", "dead", "unconscious", "none",
}

_EMOTIONS: set[str] = {
    "tense", "calm", "brutal", "warm", "grim", "chaotic", "vulnerable",
    "emotional", "quiet", "still",
}

_SETTINGS: set[str] = {
    "indoor", "indoors", "outdoor", "outdoors", "dark", "bright", "ruins",
    "lab", "corridor", "hospital", "snow", "forest", "interior", "chamber",
}

_LIGHTINGS: set[str] = {"dim", "bright", "noir", "daylight"}

_COLOR_PALETTES: set[str] = {
    "teal-orange", "warm", "cool", "desaturated", "noir",
}

# Tags that explicitly signal the character is speaking/talking
_SPEAKING_SIGNALS: set[str] = {"talking"}

# Separator patterns used in structured tags like "character:leon" or "action=talking"
_STRUCTURED_TAG_RE = re.compile(
    r"^(?:character|action|setting|mood|lighting|color)[\s:=]+(.+)$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS shots (
    id               INTEGER PRIMARY KEY,
    source           TEXT    NOT NULL,
    era              TEXT,
    start_sec        REAL    NOT NULL,
    end_sec          REAL    NOT NULL,
    duration_sec     REAL    NOT NULL,
    desc             TEXT,
    character_main   TEXT,
    character_speaks INTEGER DEFAULT 0,
    action           TEXT,
    emotion          TEXT,
    setting          TEXT,
    lighting         TEXT,
    color_palette    TEXT,
    use_rank         INTEGER DEFAULT 0,
    quality_score    REAL,
    corpus_id        TEXT
);
CREATE INDEX IF NOT EXISTS ix_source    ON shots(source);
CREATE INDEX IF NOT EXISTS ix_era       ON shots(era);
CREATE INDEX IF NOT EXISTS ix_character ON shots(character_main);
CREATE INDEX IF NOT EXISTS ix_action    ON shots(action);
CREATE INDEX IF NOT EXISTS ix_corpus    ON shots(corpus_id);
"""


def _migrate_add_corpus_id(conn: sqlite3.Connection) -> None:
    """Add the corpus_id column to existing databases that predate the library feature.

    Idempotent — skips when:
      - the `shots` table doesn't exist yet (fresh DB, DDL will create it)
      - the `corpus_id` column already exists (newer DB)
    """
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='shots'"
    ).fetchone()
    if not has_table:
        return
    cur = conn.execute("PRAGMA table_info(shots)")
    cols = {row[1] for row in cur.fetchall()}
    if "corpus_id" not in cols:
        conn.execute("ALTER TABLE shots ADD COLUMN corpus_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_corpus ON shots(corpus_id)")
        conn.commit()


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class Shot:
    """A single captioned shot record retrieved from the library."""

    id: int
    source: str
    era: str | None
    start_sec: float
    end_sec: float
    duration_sec: float
    desc: str | None
    character_main: str | None
    character_speaks: bool
    action: str | None
    emotion: str | None
    setting: str | None
    lighting: str | None
    color_palette: str | None
    use_rank: int
    quality_score: float | None

    # Convenience: timeline key used by scene_library.py
    @property
    def key(self) -> str:
        """Return the canonical key used in the old JSON library."""
        return f"{self.source}@{self.start_sec:.2f}"


def _row_to_shot(row: tuple[Any, ...]) -> Shot:
    """Convert a raw sqlite3 row (SELECT * FROM shots) to a Shot dataclass."""
    (
        shot_id, source, era, start_sec, end_sec, duration_sec,
        desc, character_main, character_speaks, action, emotion,
        setting, lighting, color_palette, use_rank, quality_score,
    ) = row
    return Shot(
        id=shot_id,
        source=source,
        era=era,
        start_sec=start_sec,
        end_sec=end_sec,
        duration_sec=duration_sec,
        desc=desc,
        character_main=character_main,
        character_speaks=bool(character_speaks),
        action=action,
        emotion=emotion,
        setting=setting,
        lighting=lighting,
        color_palette=color_palette,
        use_rank=use_rank or 0,
        quality_score=quality_score,
    )


# ---------------------------------------------------------------------------
# Attribute extraction helpers
# ---------------------------------------------------------------------------

def detect_era(
    source: str,
    era_patterns: list[tuple[str, str]] | dict[str, str] | None = None,
) -> str | None:
    """Return era string for a known source name, or None if unrecognised.

    Matching is case-insensitive.

    Args:
        source: Source-stem to classify (e.g. 'leon-re2r-cutscenes').
        era_patterns: Optional custom era map. Accepts either the legacy
            list-of-tuples format OR a dict built from
            `fandomforge.config.build_era_patterns(cfg)`. If None, falls back
            to the module-level `_ERA_PATTERNS` (Leon/RE defaults).

    Examples:
        detect_era('leon-re2r-cutscenes')  -> 'RE2R-1998'
        detect_era('re9-leon-scenepack')   -> 'RE9-2026'
        detect_era('leon-infinite-darkness') -> 'ID-2021'

        # Per-project pattern override:
        detect_era('claire-cvx-cutscenes',
                   era_patterns={'CVX': r'claire[-_]cvx'}) -> 'CVX'
    """
    lowered = source.lower()
    if era_patterns is None:
        patterns: list[tuple[str, str]] = _ERA_PATTERNS
    elif isinstance(era_patterns, dict):
        patterns = [(pat, era) for era, pat in era_patterns.items()]
    else:
        patterns = list(era_patterns)
    for pattern, era in patterns:
        if re.search(pattern, lowered):
            return era
    return None


def _normalise_tag(raw: str) -> str:
    """Strip prefix noise from structured tags like 'character:leon' -> 'leon'."""
    m = _STRUCTURED_TAG_RE.match(raw.strip())
    if m:
        return m.group(1).strip().lower()
    return raw.strip().lower()


def _extract_attributes(
    desc: str,
    tags: list[str],
    *,
    character_vocab: set[str] | None = None,
) -> dict[str, Any]:
    """Parse desc text and tag list into structured attribute dict.

    Only fills fields where there is reasonable confidence. Ambiguous or
    unrecognised values are left as None.

    character_vocab: explicit set of character names to search for. When None
    falls back to the built-in Leon/RE cast (backward compatible).

    Returns keys: character_main, character_speaks, action, emotion, setting,
                  lighting, color_palette.
    """
    normalised = [_normalise_tag(t) for t in tags]
    tag_set = set(normalised)
    desc_lower = desc.lower() if desc else ""

    # ---- character_main ------------------------------------------------
    character_main: str | None = None
    vocab = character_vocab if character_vocab is not None else _CHARACTERS
    # Priority: explicit character tags first, then desc keywords
    for char in vocab:
        if char in tag_set or char in desc_lower:
            character_main = char
            break
    # Fallback: if tags include "none" or "other" and no character found
    if character_main is None:
        if "none" in tag_set and "character" not in desc_lower:
            character_main = "none"
        elif "other" in tag_set:
            character_main = "other"

    # ---- character_speaks ----------------------------------------------
    character_speaks = int(any(s in tag_set for s in _SPEAKING_SIGNALS))
    if not character_speaks and "talking" in desc_lower:
        character_speaks = 1

    # ---- action --------------------------------------------------------
    action: str | None = None
    for act in _ACTIONS:
        if act in tag_set:
            action = act
            break
    if action is None:
        # Scan desc for action verbs
        for act in _ACTIONS - {"none"}:
            verb = act.replace("_", " ")
            if verb in desc_lower or act in desc_lower:
                action = act
                break

    # ---- emotion -------------------------------------------------------
    emotion: str | None = None
    for emo in _EMOTIONS:
        if emo in tag_set or emo in desc_lower:
            emotion = emo
            break

    # ---- setting -------------------------------------------------------
    setting: str | None = None
    for s in _SETTINGS:
        if s in tag_set:
            setting = "indoor" if s == "indoors" else ("outdoor" if s == "outdoors" else s)
            break
    if setting is None:
        for s in _SETTINGS - {"indoors", "outdoors"}:
            if s in desc_lower:
                setting = s
                break

    # ---- lighting ------------------------------------------------------
    lighting: str | None = None
    for l in _LIGHTINGS:
        if l in tag_set or l in desc_lower:
            lighting = l
            break

    # ---- color_palette -------------------------------------------------
    color_palette: str | None = None
    for cp in _COLOR_PALETTES:
        if cp in tag_set or cp in desc_lower:
            color_palette = cp
            break

    return {
        "character_main": character_main,
        "character_speaks": character_speaks,
        "action": action,
        "emotion": emotion,
        "setting": setting,
        "lighting": lighting,
        "color_palette": color_palette,
    }


# ---------------------------------------------------------------------------
# Main library class
# ---------------------------------------------------------------------------

class ShotLibrary:
    """SQLite-backed shot library with ingestion and query APIs.

    Usage:
        lib = ShotLibrary(Path('/path/to/.shot-library.db'))
        lib.ingest_from_scene_cache(cache_json, lib_json)
        shots = lib.search(era='RE9-2026', character='leon', emotion='tense')
    """

    def __init__(self, db_path: Path) -> None:
        """Open (or create) the SQLite database at db_path."""
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        # Migrate BEFORE running DDL — the DDL includes `CREATE INDEX ix_corpus
        # ON shots(corpus_id)` which fails on legacy tables missing the column.
        _migrate_add_corpus_id(self._conn)
        self._conn.executescript(_DDL)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest_from_scene_cache(
        self,
        cache_json: dict[str, list[list[float]]],
        lib_json: dict[str, dict[str, Any]],
        *,
        era_patterns: list[tuple[str, str]] | dict[str, str] | None = None,
        character_vocab: set[str] | None = None,
    ) -> int:
        """Migrate existing JSON library into the SQLite database.

        cache_json: the .scene-cache.json mapping source -> [[start, end, dur], ...]
        lib_json:   the .scene-library.json mapping "source@start" -> {source, start_sec,
                    end_sec, duration_sec, desc, tags}

        Skips entries already present (identified by source + start_sec).
        Returns the number of new rows inserted.

        Era is auto-detected from the source name via detect_era(). Pass
        era_patterns to override the default Leon/RE patterns — the dict form
        from `fandomforge.config.build_era_patterns(cfg)` works directly.
        Attributes (character, action, emotion, ...) are extracted from desc and tags.
        """
        # Build a lookup: (source, rounded_start) -> True for existing rows
        cur = self._conn.execute("SELECT source, start_sec FROM shots")
        existing: set[tuple[str, str]] = {
            (row[0], f"{row[1]:.2f}") for row in cur
        }

        records: list[dict[str, Any]] = []
        for key, entry in lib_json.items():
            source = entry.get("source", "")
            start = float(entry.get("start_sec", 0.0))
            lookup_key = (source, f"{start:.2f}")
            if lookup_key in existing:
                continue

            end = float(entry.get("end_sec", 0.0))
            dur = float(entry.get("duration_sec", end - start))
            desc = entry.get("desc", "")
            tags = entry.get("tags", [])
            attrs = _extract_attributes(
                desc, tags, character_vocab=character_vocab,
            )

            records.append({
                "source": source,
                "era": detect_era(source, era_patterns=era_patterns),
                "start_sec": start,
                "end_sec": end,
                "duration_sec": dur,
                "desc": desc,
                **attrs,
                "use_rank": 0,
                "quality_score": None,
            })

        if records:
            self._bulk_insert(records)

        return len(records)

    def bulk_add(self, shot_records: list[dict[str, Any]]) -> None:
        """Add new shot records in a single transaction.

        Each record is a dict with keys matching the shots table columns
        (excluding id, which is auto-assigned). Missing optional fields
        default to None / 0.

        Duplicate (source, start_sec) pairs are skipped silently.
        """
        cur = self._conn.execute("SELECT source, start_sec FROM shots")
        existing: set[tuple[str, str]] = {
            (row[0], f"{row[1]:.2f}") for row in cur
        }
        filtered = [
            r for r in shot_records
            if (r.get("source", ""), f"{float(r.get('start_sec', 0)):.2f}") not in existing
        ]
        if filtered:
            self._bulk_insert(filtered)

    def _bulk_insert(self, records: list[dict[str, Any]]) -> None:
        """Insert records in one transaction. Does not check for duplicates."""
        sql = """
            INSERT INTO shots
                (source, era, start_sec, end_sec, duration_sec, desc,
                 character_main, character_speaks, action, emotion,
                 setting, lighting, color_palette, use_rank, quality_score)
            VALUES
                (:source, :era, :start_sec, :end_sec, :duration_sec, :desc,
                 :character_main, :character_speaks, :action, :emotion,
                 :setting, :lighting, :color_palette, :use_rank, :quality_score)
        """
        normalised = []
        for r in records:
            normalised.append({
                "source": r.get("source", ""),
                "era": r.get("era"),
                "start_sec": float(r.get("start_sec", 0)),
                "end_sec": float(r.get("end_sec", 0)),
                "duration_sec": float(r.get("duration_sec", 0)),
                "desc": r.get("desc"),
                "character_main": r.get("character_main"),
                "character_speaks": int(r.get("character_speaks", 0)),
                "action": r.get("action"),
                "emotion": r.get("emotion"),
                "setting": r.get("setting"),
                "lighting": r.get("lighting"),
                "color_palette": r.get("color_palette"),
                "use_rank": int(r.get("use_rank", 0)),
                "quality_score": r.get("quality_score"),
            })
        with self._conn:
            self._conn.executemany(sql, normalised)

    def update_caption(
        self,
        shot_id: int,
        desc: str,
        tags: list[str],
    ) -> None:
        """Re-extract attributes from a new caption and update the row.

        Use this when scene_library.py has produced a fresh vision caption
        for an existing shot and you want the structured fields re-derived.
        """
        attrs = _extract_attributes(desc, tags)
        with self._conn:
            self._conn.execute(
                """
                UPDATE shots SET
                    desc=:desc,
                    character_main=:character_main,
                    character_speaks=:character_speaks,
                    action=:action,
                    emotion=:emotion,
                    setting=:setting,
                    lighting=:lighting,
                    color_palette=:color_palette
                WHERE id=:id
                """,
                {"id": shot_id, "desc": desc, **attrs},
            )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def search(
        self,
        *,
        era: str | None = None,
        character: str | None = None,
        action: str | None = None,
        emotion: str | None = None,
        setting: str | None = None,
        not_speaking: bool = False,
        min_dur: float | None = None,
        max_dur: float | None = None,
        exclude_sources: list[str] | None = None,
        exclude_hud_watermark: bool = True,
        limit: int = 20,
    ) -> list[Shot]:
        """Query shots by any combination of structured attributes.

        Parameters
        ----------
        era:
            Filter to a specific game era, e.g. 'RE9-2026', 'RE4R-2004'.
        character:
            Filter by main character, e.g. 'leon', 'grace', 'enemy'.
        action:
            Filter by action verb, e.g. 'aiming', 'walking', 'wounded'.
        emotion:
            Filter by mood, e.g. 'tense', 'calm', 'chaotic'.
        setting:
            Filter by environment, e.g. 'indoor', 'dark', 'ruins'.
        not_speaking:
            If True, exclude shots where character_speaks=1.
        min_dur:
            Minimum shot duration in seconds.
        max_dur:
            Maximum shot duration in seconds.
        exclude_sources:
            List of source names to exclude from results.
        limit:
            Maximum rows to return. Ordered by use_rank ASC (least-used first).

        Returns
        -------
        list[Shot]
            Matching Shot dataclass instances.
        """
        conditions: list[str] = []
        params: list[Any] = []

        if era is not None:
            conditions.append("era = ?")
            params.append(era)
        if character is not None:
            conditions.append("character_main = ?")
            params.append(character)
        if action is not None:
            conditions.append("action = ?")
            params.append(action)
        if emotion is not None:
            conditions.append("emotion = ?")
            params.append(emotion)
        if setting is not None:
            conditions.append("setting = ?")
            params.append(setting)
        if not_speaking:
            conditions.append("character_speaks = 0")
        if min_dur is not None:
            conditions.append("duration_sec >= ?")
            params.append(min_dur)
        if max_dur is not None:
            conditions.append("duration_sec <= ?")
            params.append(max_dur)
        if exclude_sources:
            placeholders = ",".join("?" for _ in exclude_sources)
            conditions.append(f"source NOT IN ({placeholders})")
            params.extend(exclude_sources)
        if exclude_hud_watermark:
            # Filter shots whose caption contains signs of gameplay HUD or
            # third-party compilation watermarks. Keeps tribute edits from
            # including "Press A to Dash" prompts, ammo counters, or
            # FILMISNOW / GMDEPTV watermarked clips.
            hud_terms = [
                "hud", "button prompt", "press a", "press x", "press y",
                "press b", "dash", "reload", "health bar", "ammo counter",
                "xbox controller", "ps5 controller", "playstation",
                "filmisnow", "gmdeptv", "gameplay overlay", "ui overlay",
                "game ui", "quick time event", "qte",
            ]
            for term in hud_terms:
                conditions.append(
                    "LOWER(COALESCE(desc,'')) NOT LIKE ?"
                )
                params.append(f"%{term}%")

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"""
            SELECT id, source, era, start_sec, end_sec, duration_sec, desc,
                   character_main, character_speaks, action, emotion,
                   setting, lighting, color_palette, use_rank, quality_score
            FROM shots
            {where}
            ORDER BY use_rank ASC, id ASC
            LIMIT ?
        """
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_shot(tuple(r)) for r in rows]

    def get_by_id(self, shot_id: int) -> Shot | None:
        """Retrieve a single Shot by its integer primary key.

        Returns None if no shot with that id exists.
        """
        row = self._conn.execute(
            """
            SELECT id, source, era, start_sec, end_sec, duration_sec, desc,
                   character_main, character_speaks, action, emotion,
                   setting, lighting, color_palette, use_rank, quality_score
            FROM shots WHERE id = ?
            """,
            (shot_id,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_shot(tuple(row))

    def mark_used(self, shot_id: int) -> None:
        """Increment use_rank by 1 for the given shot id.

        Tracks how many times a shot has been placed in an edit, so
        search() can surface least-used shots first.
        """
        with self._conn:
            self._conn.execute(
                "UPDATE shots SET use_rank = use_rank + 1 WHERE id = ?",
                (shot_id,),
            )

    # ------------------------------------------------------------------
    # Stats / diagnostics
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Return a summary dict with per-era, per-character, and speaking counts.

        Useful for verifying ingestion completeness.
        """
        total = self._conn.execute("SELECT COUNT(*) FROM shots").fetchone()[0]

        era_rows = self._conn.execute(
            "SELECT era, COUNT(*) FROM shots GROUP BY era ORDER BY COUNT(*) DESC"
        ).fetchall()

        char_rows = self._conn.execute(
            "SELECT character_main, COUNT(*) FROM shots GROUP BY character_main ORDER BY COUNT(*) DESC"
        ).fetchall()

        speaking = self._conn.execute(
            "SELECT character_speaks, COUNT(*) FROM shots GROUP BY character_speaks"
        ).fetchall()
        speaking_dict = {bool(r[0]): r[1] for r in speaking}

        action_rows = self._conn.execute(
            "SELECT action, COUNT(*) FROM shots GROUP BY action ORDER BY COUNT(*) DESC LIMIT 15"
        ).fetchall()

        source_rows = self._conn.execute(
            "SELECT source, COUNT(*) FROM shots GROUP BY source ORDER BY COUNT(*) DESC"
        ).fetchall()

        return {
            "total": total,
            "by_era": {r[0] or "unknown": r[1] for r in era_rows},
            "by_character": {r[0] or "unknown": r[1] for r in char_rows},
            "speaking": speaking_dict.get(True, 0),
            "silent": speaking_dict.get(False, 0),
            "top_actions": {r[0] or "unknown": r[1] for r in action_rows},
            "by_source": {r[0]: r[1] for r in source_rows},
        }

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    def __enter__(self) -> "ShotLibrary":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# CLI / verification entrypoint
# ---------------------------------------------------------------------------

def _print_stats(s: dict[str, Any]) -> None:
    """Pretty-print ingestion stats."""
    print(f"\n{'='*60}")
    print(f"  SHOT LIBRARY -- INGESTION STATS")
    print(f"{'='*60}")
    print(f"  Total shots   : {s['total']}")
    print(f"  Speaking      : {s['speaking']}")
    print(f"  Silent        : {s['silent']}")
    print()
    print("  Shots per era:")
    for era, count in s["by_era"].items():
        print(f"    {era:<20} {count:>5}")
    print()
    print("  Shots per character:")
    for char, count in s["by_character"].items():
        print(f"    {char:<20} {count:>5}")
    print()
    print("  Top actions:")
    for act, count in s["top_actions"].items():
        print(f"    {act:<20} {count:>5}")
    print()
    print("  Shots per source:")
    for src, count in s["by_source"].items():
        print(f"    {src:<35} {count:>5}")
    print(f"{'='*60}\n")


def ingest_and_verify(
    db_path: Path,
    cache_path: Path,
    lib_path: Path,
    *,
    era_patterns: list[tuple[str, str]] | dict[str, str] | None = None,
    character_vocab: set[str] | None = None,
) -> dict[str, Any]:
    """Run a full ingest from disk files and print verification stats.

    Parameters
    ----------
    db_path:
        Target SQLite file path.
    cache_path:
        Path to .scene-cache.json (source -> [[start, end, dur], ...]).
    lib_path:
        Path to .scene-library.json (key -> captioned entry).

    Returns
    -------
    dict
        Stats dict from ShotLibrary.stats().
    """
    import json as _json

    cache_json = _json.loads(cache_path.read_text())
    lib_json = _json.loads(lib_path.read_text())

    with ShotLibrary(db_path) as lib:
        inserted = lib.ingest_from_scene_cache(
            cache_json, lib_json,
            era_patterns=era_patterns,
            character_vocab=character_vocab,
        )
        print(f"\nInserted {inserted} new shots into {db_path}")
        s = lib.stats()

    _print_stats(s)
    return s


if __name__ == "__main__":
    import os as _os
    _PROJECT = Path(_os.environ.get(
        "FF_PROJECT",
        "/Users/damato/Video Project/projects/leon-badass-monologue",
    ))
    _DB = _PROJECT / ".shot-library.db"
    _CACHE = _PROJECT / ".scene-cache.json"
    _LIB = _PROJECT / ".scene-library.json"

    ingest_and_verify(_DB, _CACHE, _LIB)
