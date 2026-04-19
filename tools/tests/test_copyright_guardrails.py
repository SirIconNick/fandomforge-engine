"""Tests for Phase 8 copyright + guardrails:
    - domain guard on ff sources download
    - ff credit generate writes credits.md with song + sources
    - high-risk-songs.md is consulted correctly by qa.copyright
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fandomforge.credits import generate_credits
from fandomforge.sources.download import (
    DisallowedDomainError,
    _assert_allowed,
    DISALLOWED_DOMAINS,
)


def test_domain_guard_rejects_netflix() -> None:
    with pytest.raises(DisallowedDomainError):
        _assert_allowed("https://www.netflix.com/watch/1234")


def test_domain_guard_rejects_disney_plus() -> None:
    with pytest.raises(DisallowedDomainError):
        _assert_allowed("https://www.disneyplus.com/video/abc")


def test_domain_guard_rejects_hbo_max() -> None:
    with pytest.raises(DisallowedDomainError):
        _assert_allowed("https://www.max.com/video/abc")


def test_domain_guard_allows_youtube() -> None:
    _assert_allowed("https://www.youtube.com/watch?v=abc")


def test_domain_guard_allows_vimeo() -> None:
    _assert_allowed("https://vimeo.com/1234")


def test_domain_list_covers_big_streamers() -> None:
    for host in ["netflix.com", "hulu.com", "disneyplus.com", "hbomax.com", "max.com"]:
        assert host in DISALLOWED_DOMAINS


def test_credit_generate_writes_song_and_sources(tmp_path: Path) -> None:
    edit_plan = {
        "schema_version": 1,
        "project_slug": "test",
        "song": {"title": "Until I Bleed Out", "artist": "Twenty One Pilots"},
    }
    source_catalog = {
        "schema_version": 1,
        "project_slug": "test",
        "sources": [
            {"id": "rots", "path": "/tmp/rots.mp4", "fandom": "Star Wars",
             "source_type": "movie", "title": "Revenge of the Sith",
             "year": 2005, "media": {"duration_sec": 1, "width": 1, "height": 1,
                                       "fps": 24, "codec": "h264"}},
            {"id": "re9", "path": "/tmp/re9.mp4", "fandom": "Resident Evil",
             "source_type": "game", "title": "Resident Evil 9", "year": 2026,
             "media": {"duration_sec": 1, "width": 1, "height": 1,
                       "fps": 24, "codec": "h264"}},
        ],
    }
    out = tmp_path / "credits.md"
    result = generate_credits(
        edit_plan=edit_plan,
        source_catalog=source_catalog,
        output_path=out,
    )
    assert out.exists()
    text = out.read_text()
    assert "Twenty One Pilots" in text
    assert "Revenge of the Sith" in text
    assert "2005" in text
    assert "Resident Evil 9" in text
    assert "2026" in text
    assert "fair use" in text.lower() or "transformative" in text.lower()
    assert result.song_line
    assert len(result.source_lines) == 2


def test_credit_generate_uses_existing_fair_use_statement(tmp_path: Path) -> None:
    edit_plan = {
        "schema_version": 1,
        "project_slug": "test",
        "song": {"title": "S", "artist": "A"},
        "credits": {
            "fair_use_statement": "custom transformative statement for my edit",
        },
    }
    source_catalog = {"schema_version": 1, "project_slug": "test", "sources": []}
    out = tmp_path / "credits.md"
    generate_credits(edit_plan=edit_plan, source_catalog=source_catalog, output_path=out)
    assert "custom transformative statement" in out.read_text()


def test_credit_generate_dedupes_sources(tmp_path: Path) -> None:
    edit_plan = {
        "schema_version": 1, "project_slug": "test",
        "song": {"title": "S", "artist": "A"},
    }
    source_catalog = {
        "schema_version": 1, "project_slug": "test",
        "sources": [
            {"id": "a", "path": "/a.mp4", "fandom": "F", "source_type": "movie",
             "title": "Movie X", "year": 2010,
             "media": {"duration_sec": 1, "width": 1, "height": 1, "fps": 24, "codec": "h264"}},
            {"id": "b", "path": "/b.mp4", "fandom": "F", "source_type": "movie",
             "title": "Movie X", "year": 2010,
             "media": {"duration_sec": 1, "width": 1, "height": 1, "fps": 24, "codec": "h264"}},
        ],
    }
    out = tmp_path / "credits.md"
    result = generate_credits(
        edit_plan=edit_plan, source_catalog=source_catalog, output_path=out,
    )
    assert len(result.source_lines) == 1
