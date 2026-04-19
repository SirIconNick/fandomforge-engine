"""End-to-end test for Phase 4 NLE exporters.

Uses fabricated tiny-file media (no ffmpeg synth) so tests run in milliseconds
and never hang on subprocess. What we verify is the XML/JSON shape, bin
structure, and sidecar artifacts — not the ffmpeg pipeline itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest

from fandomforge.assembly.fcp_project import export_fcp_project
from fandomforge.assembly.premiere_project import export_premiere_project
from fandomforge.assembly.capcut_project import export_capcut_project
from fandomforge.assembly.vegas_project import export_vegas_project
from fandomforge.assembly.resolve_project import export_resolve_project


def _build_fake_project(tmp_path: Path) -> dict[str, Any]:
    """Create the on-disk files + in-memory artifact dicts the exporters need."""
    raw = tmp_path / "raw"
    raw.mkdir()
    a = raw / "alpha.mp4"
    b = raw / "beta.mp4"
    a.write_bytes(b"\x00" * 64)
    b.write_bytes(b"\x00" * 64)

    slug = tmp_path.name
    shot_list = {
        "schema_version": 1, "project_slug": slug, "fps": 24,
        "resolution": {"width": 1920, "height": 1080},
        "song_duration_sec": 4.0,
        "shots": [
            {"id": "s-1", "act": 1, "start_frame": 0, "duration_frames": 48,
             "source_id": "alpha", "source_timecode": "00:00:00.000",
             "role": "establishing", "mood_tags": ["hype"],
             "safe_area_ok": True, "fandom": "Test-A",
             "beat_sync": {"type": "downbeat", "index": 0, "time_sec": 0.0}},
            {"id": "s-2", "act": 2, "start_frame": 48, "duration_frames": 48,
             "source_id": "beta", "source_timecode": "00:00:02.000",
             "role": "hero", "mood_tags": ["emotional"],
             "safe_area_ok": True, "fandom": "Test-B",
             "beat_sync": {"type": "downbeat", "index": 1, "time_sec": 2.0}},
        ],
    }
    source_catalog = {
        "schema_version": 1, "project_slug": slug,
        "sources": [
            {"id": "alpha", "path": str(a), "fandom": "Test-A",
             "source_type": "movie", "title": "Alpha",
             "media": {"duration_sec": 6.0, "width": 320, "height": 180,
                       "fps": 24.0, "codec": "h264", "bitrate_kbps": 500,
                       "has_audio": True, "audio_codec": "aac",
                       "audio_channels": 2, "audio_sample_rate": 48000,
                       "variable_frame_rate": False}},
            {"id": "beta", "path": str(b), "fandom": "Test-B",
             "source_type": "movie", "title": "Beta",
             "media": {"duration_sec": 6.0, "width": 320, "height": 180,
                       "fps": 24.0, "codec": "h264", "bitrate_kbps": 500,
                       "has_audio": True, "audio_codec": "aac",
                       "audio_channels": 2, "audio_sample_rate": 48000,
                       "variable_frame_rate": False}},
        ],
    }
    edit_plan = {
        "schema_version": 1, "project_slug": slug,
        "concept": {"theme": "Smoke", "one_sentence": "Direct exporter smoke test"},
        "song": {"title": "S", "artist": "T", "file": str(a)},
        "fandoms": [{"name": "Test-A"}, {"name": "Test-B"}],
        "vibe": "cinematic", "length_seconds": 4, "platform_target": "youtube",
        "fps": 24, "resolution": {"width": 1920, "height": 1080},
        "acts": [
            {"number": 1, "name": "A", "start_sec": 0, "end_sec": 2,
             "energy_target": 30, "emotional_goal": "establish"},
            {"number": 2, "name": "B", "start_sec": 2, "end_sec": 4,
             "energy_target": 80, "emotional_goal": "payoff"},
        ],
    }
    audio_plan = {
        "schema_version": 1, "project_slug": slug,
        "target_lufs": -14, "true_peak_ceiling_dbtp": -1, "song_gain_db": -6,
        "layers": [{"name": "song", "role": "music", "gain_db": -6, "file": str(a)}],
    }
    return {
        "project_dir": tmp_path,
        "shot_list": shot_list,
        "source_catalog": source_catalog,
        "edit_plan": edit_plan,
        "audio_plan": audio_plan,
    }


def test_fcp_export_produces_valid_fcpxml(tmp_path: Path) -> None:
    inputs = _build_fake_project(tmp_path)
    r = export_fcp_project(**inputs)
    assert r.bundle_dir.exists()
    assert r.fcpxml_path.exists()
    assert r.manifest_path.exists()
    assert r.notes_path.exists()

    tree = ET.parse(r.fcpxml_path)
    root = tree.getroot()
    assert root.tag == "fcpxml"
    assert root.get("version") == "1.11"
    event = root.find("library/event")
    assert event is not None
    bin_names = [c.get("name") for c in event.findall("collection")]
    assert "01_Song" in bin_names
    assert "03_Sources" in bin_names
    spine = root.find("library/event/project/sequence/spine")
    assert spine is not None
    assert len(spine.findall("asset-clip")) >= 2

    manifest = json.loads(r.manifest_path.read_text())
    assert manifest["entry_count"] >= 1


def test_resolve_portable_path_always_works(tmp_path: Path) -> None:
    inputs = _build_fake_project(tmp_path)
    r = export_resolve_project(force_portable=True, **inputs)
    assert not r.native_project_created
    assert r.fcpxml_result.fcpxml_path.exists()


def test_premiere_exports_jsx_import_script(tmp_path: Path) -> None:
    inputs = _build_fake_project(tmp_path)
    r = export_premiere_project(**inputs)
    assert r.jsx_path.exists()
    text = r.jsx_path.read_text()
    assert "app.project.importFiles" in text
    assert r.readme_path.exists()


def test_capcut_draft_has_tracks_and_segments(tmp_path: Path) -> None:
    inputs = _build_fake_project(tmp_path)
    r = export_capcut_project(**inputs)
    draft = json.loads((r.draft_dir / "draft_content.json").read_text())
    assert draft["tracks"]
    assert draft["tracks"][0]["segments"]
    assert draft["duration"] > 0
    assert "canvas_config" in draft
    assert (r.draft_dir / "draft_meta_info.json").exists()


def test_vegas_exports_fcpxml_bundle(tmp_path: Path) -> None:
    inputs = _build_fake_project(tmp_path)
    r = export_vegas_project(**inputs)
    assert r.fcpxml_result.fcpxml_path.exists()
    assert r.readme_path.exists()


def test_render_notes_contain_concept_and_platform(tmp_path: Path) -> None:
    inputs = _build_fake_project(tmp_path)
    r = export_fcp_project(**inputs)
    text = r.notes_path.read_text()
    assert "Smoke" in text
    assert "youtube" in text.lower()


def test_media_manifest_flags_missing_files(tmp_path: Path) -> None:
    inputs = _build_fake_project(tmp_path)
    Path(inputs["source_catalog"]["sources"][0]["path"]).unlink()
    r = export_fcp_project(**inputs)
    manifest = json.loads(r.manifest_path.read_text())
    assert manifest["missing_count"] >= 1
