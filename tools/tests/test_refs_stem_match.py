"""qa.refs now accepts path-stem matches against catalog.path, so shot-list
generators that emit path stems (shot_proposer Phase 0.5.7) resolve against
catalogs that retain blake3 content-hash ids."""

from __future__ import annotations

from fandomforge.qa.rules.refs import _build_resolution_index


def test_resolution_index_includes_blake3_ids():
    catalog = {
        "sources": [
            {"id": "b2:abc123deadbeef", "path": "/tmp/raw/mad-max.mp4"},
            {"id": "b2:zzz456feed", "path": "/tmp/raw/john-wick-4.mkv"},
        ],
    }
    idx = _build_resolution_index(catalog)
    assert "b2:abc123deadbeef" in idx
    assert "b2:zzz456feed" in idx


def test_resolution_index_includes_path_stems():
    """Phase 0.5.7 alignment — shot_proposer emits path stems. The catalog
    may still carry blake3 hashes; the file stem on catalog.path bridges
    the gap."""
    catalog = {
        "sources": [
            {"id": "b2:abc123", "path": "/tmp/raw/mad-max-fury-road.mp4"},
            {"id": "b2:def456", "path": "/tmp/raw/the-raid-2.mkv"},
        ],
    }
    idx = _build_resolution_index(catalog)
    assert "mad-max-fury-road" in idx
    assert "the-raid-2" in idx


def test_resolution_index_includes_source_name_when_present():
    catalog = {"sources": [
        {"id": "b2:abc", "source_name": "Mad Max Fury Road Compilation",
         "path": "/tmp/raw/mmfr.mp4"},
    ]}
    idx = _build_resolution_index(catalog)
    assert "Mad Max Fury Road Compilation" in idx
    assert "mmfr" in idx  # stem also accepted


def test_missing_path_or_name_doesnt_crash():
    catalog = {"sources": [
        {"id": "only-id"},
    ]}
    idx = _build_resolution_index(catalog)
    assert idx == {"only-id"}


def test_empty_catalog_returns_empty_index():
    assert _build_resolution_index({"sources": []}) == set()
    assert _build_resolution_index({}) == set()
