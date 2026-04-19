"""Library module tests — no network, no expensive ingest, just the index + walker."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Redirect the library cache into a tmp dir for every test in this file."""
    monkeypatch.setenv("FANDOMFORGE_CACHE_DIR", str(tmp_path / "cache"))
    # library caches MEDIA_EXTS at import-time; re-import to pick up the env
    import importlib, fandomforge.library as mod
    importlib.reload(mod)
    yield mod


def _touch(p: Path, content: bytes = b"fake") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


class TestInferFandom:
    def test_dir1_default(self, _isolated_cache) -> None:
        lib = _isolated_cache
        root = Path("/movies")
        file = Path("/movies/Marvel/Avengers.mp4")
        assert lib.infer_fandom(file, root, rule="dir1") == "Marvel"

    def test_dir1_with_hyphens(self, _isolated_cache) -> None:
        lib = _isolated_cache
        root = Path("/m")
        file = Path("/m/star-wars/A_New_Hope.mp4")
        assert lib.infer_fandom(file, root, rule="dir1") == "Star Wars"

    def test_dir2(self, _isolated_cache) -> None:
        lib = _isolated_cache
        root = Path("/m")
        file = Path("/m/Marvel/MCU/Avengers.mp4")
        assert lib.infer_fandom(file, root, rule="dir2") == "Marvel / Mcu"

    def test_filename_before_year(self, _isolated_cache) -> None:
        lib = _isolated_cache
        root = Path("/m")
        file = Path("/m/John.Wick.2014.1080p.mkv")
        assert lib.infer_fandom(file, root, rule="filename-before-year") == "John Wick"

    def test_manual_returns_unknown(self, _isolated_cache) -> None:
        lib = _isolated_cache
        root = Path("/m")
        file = Path("/m/Marvel/Avengers.mp4")
        assert lib.infer_fandom(file, root, rule="manual") == "Unknown"


class TestWalker:
    def test_walks_media_files_recursively(self, tmp_path, _isolated_cache) -> None:
        lib = _isolated_cache
        root = tmp_path / "movies"
        _touch(root / "marvel" / "a.mp4")
        _touch(root / "star-wars" / "b.mkv")
        _touch(root / "nested" / "deeper" / "c.mov")
        _touch(root / "readme.txt")  # not media
        _touch(root / ".hidden" / "d.mp4")  # dotted dir — skipped
        found = set(p.name for p in lib.walk_media(root))
        assert found == {"a.mp4", "b.mkv", "c.mov"}


class TestRootsCrud:
    def test_link_persists(self, tmp_path, _isolated_cache) -> None:
        lib = _isolated_cache
        folder = tmp_path / "movies"
        folder.mkdir()
        root = lib.link_root(folder, name="home")
        assert root.name == "home"
        assert root.path == folder.resolve()
        assert [r.name for r in lib.list_roots()] == ["home"]

    def test_link_nonexistent_raises(self, tmp_path, _isolated_cache) -> None:
        lib = _isolated_cache
        with pytest.raises(FileNotFoundError):
            lib.link_root(tmp_path / "does-not-exist", name="x")

    def test_link_replaces_on_same_name(self, tmp_path, _isolated_cache) -> None:
        lib = _isolated_cache
        a = tmp_path / "a"; a.mkdir()
        b = tmp_path / "b"; b.mkdir()
        lib.link_root(a, name="shared")
        lib.link_root(b, name="shared")
        roots = lib.list_roots()
        assert len(roots) == 1
        assert roots[0].path == b.resolve()

    def test_unlink_keeps_sources_by_default(self, tmp_path, _isolated_cache) -> None:
        lib = _isolated_cache
        folder = tmp_path / "m"
        _touch(folder / "marvel" / "a.mp4", b"film-a")
        _touch(folder / "marvel" / "b.mp4", b"film-b")
        lib.link_root(folder, name="home")
        lib.scan_root("home")
        assert len(lib.list_sources()) == 2
        lib.unlink_root("home", delete_sources=False)
        remaining = lib.list_sources()
        assert len(remaining) == 2  # kept
        assert all(s.root_name is None for s in remaining)  # detached from root

    def test_unlink_with_delete_sources(self, tmp_path, _isolated_cache) -> None:
        lib = _isolated_cache
        folder = tmp_path / "m"
        _touch(folder / "marvel" / "a.mp4")
        lib.link_root(folder, name="home")
        lib.scan_root("home")
        assert len(lib.list_sources()) == 1
        lib.unlink_root("home", delete_sources=True)
        assert lib.list_sources() == []


class TestScan:
    def test_scan_registers_sources_with_inferred_fandom(self, tmp_path, _isolated_cache) -> None:
        lib = _isolated_cache
        folder = tmp_path / "movies"
        _touch(folder / "Marvel" / "Avengers.2012.mp4", b"a")
        _touch(folder / "Marvel" / "IronMan.mp4", b"b")
        _touch(folder / "StarWars" / "ANewHope.mkv", b"c")
        lib.link_root(folder, name="home")
        result = lib.scan_root("home")
        assert result.discovered == 3
        assert result.added == 3
        assert result.already_indexed == 0
        sources = lib.list_sources()
        fandoms = {s.fandom for s in sources}
        assert fandoms == {"Marvel", "Starwars"}  # titleized, close enough
        counts = lib.fandom_counts()
        assert counts["Marvel"] == 2

    def test_scan_is_idempotent(self, tmp_path, _isolated_cache) -> None:
        lib = _isolated_cache
        folder = tmp_path / "m"
        _touch(folder / "Marvel" / "a.mp4")
        lib.link_root(folder, name="home")
        first = lib.scan_root("home")
        second = lib.scan_root("home")
        assert first.added == 1
        assert second.added == 0
        assert second.already_indexed == 1

    def test_scan_skips_non_media_and_dotfiles(self, tmp_path, _isolated_cache) -> None:
        lib = _isolated_cache
        folder = tmp_path / "m"
        _touch(folder / "Marvel" / "valid.mp4")
        _touch(folder / "Marvel" / "notes.txt")  # wrong ext
        _touch(folder / "Marvel" / ".hidden" / "deep.mp4")
        lib.link_root(folder, name="home")
        result = lib.scan_root("home")
        assert result.added == 1


class TestTagAndQuery:
    def test_tag_overrides_fandom(self, tmp_path, _isolated_cache) -> None:
        lib = _isolated_cache
        folder = tmp_path / "m"
        f = folder / "WrongDir" / "JohnWick2014.mp4"
        _touch(f)
        lib.link_root(folder, name="home")
        lib.scan_root("home")
        updated = lib.tag_source(f, fandom="John Wick")
        assert updated.fandom == "John Wick"
        assert lib.list_sources(fandom="John Wick")[0].path.name == "JohnWick2014.mp4"

    def test_tag_unknown_path_raises(self, tmp_path, _isolated_cache) -> None:
        lib = _isolated_cache
        bogus = tmp_path / "nope.mp4"
        _touch(bogus)
        with pytest.raises(KeyError):
            lib.tag_source(bogus, fandom="Anything")

    def test_candidate_sources_filters_by_fandom(self, tmp_path, _isolated_cache) -> None:
        lib = _isolated_cache
        folder = tmp_path / "m"
        _touch(folder / "Marvel" / "a.mp4", b"a")
        _touch(folder / "StarWars" / "b.mp4", b"b")
        lib.link_root(folder, name="home")
        lib.scan_root("home")
        # Mark both as ingested so the query returns them.
        for s in lib.list_sources():
            lib.set_ingest_status(s.id, "done")
        marvel_only = lib.candidate_sources(fandoms={"Marvel": 1.0})
        assert len(marvel_only) == 1
        assert marvel_only[0].fandom == "Marvel"


class TestShotLibraryMigration:
    """The shot_library DB schema must absorb corpus_id without breaking existing DBs."""

    def test_corpus_id_column_present_on_new_db(self, tmp_path) -> None:
        from fandomforge.intelligence.shot_library import ShotLibrary

        db = tmp_path / "new.db"
        ShotLibrary(db)  # creates + migrates
        import sqlite3
        conn = sqlite3.connect(str(db))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(shots)")}
        assert "corpus_id" in cols

    def test_migration_adds_column_to_pre_library_db(self, tmp_path) -> None:
        """Simulate a pre-library-feature DB (same columns as today minus
        corpus_id) and confirm migration adds the column without losing data."""
        import sqlite3
        from fandomforge.intelligence.shot_library import ShotLibrary

        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db))
        # All current columns except corpus_id — matches what existing projects have.
        conn.execute("""
            CREATE TABLE shots (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                era TEXT,
                start_sec REAL NOT NULL,
                end_sec REAL NOT NULL,
                duration_sec REAL NOT NULL,
                desc TEXT,
                character_main TEXT,
                character_speaks INTEGER DEFAULT 0,
                action TEXT,
                emotion TEXT,
                setting TEXT,
                lighting TEXT,
                color_palette TEXT,
                use_rank INTEGER DEFAULT 0,
                quality_score REAL
            )
        """)
        conn.execute(
            "INSERT INTO shots (source, start_sec, end_sec, duration_sec) VALUES (?,?,?,?)",
            ("foo", 1.0, 3.0, 2.0),
        )
        conn.commit()
        conn.close()

        ShotLibrary(db)  # open triggers migration

        conn = sqlite3.connect(str(db))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(shots)")}
        assert "corpus_id" in cols
        # Existing data preserved
        (n,) = conn.execute("SELECT COUNT(*) FROM shots").fetchone()
        assert n == 1
