"""Unit tests for the rough-cut orchestrator.

Integration tests that spawn real ffmpeg live in test_leon_smoke.py under
@pytest.mark.requires_fixtures. These tests cover pure-logic helpers and
the early-exit paths that don't need real media.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from fandomforge.assembly.orchestrator import (
    _load_dialogue_cues,
    _validate_song_stream,
    build_rough_cut,
)


class TestValidateSongStream:
    def test_missing_ffprobe_assumes_ok(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("shutil.which", lambda _x: None)
        # Even without ffprobe, the function returns True (can't check, trust).
        assert _validate_song_stream(tmp_path / "anything.mp3") is True

    def test_video_only_file_returns_false(self, tmp_path: Path) -> None:
        fake = tmp_path / "x.mp4"
        fake.write_bytes(b"")
        fake_proc = MagicMock(returncode=0, stdout="", stderr="")
        with patch("fandomforge.assembly.orchestrator.shutil.which", return_value="/usr/bin/ffprobe"), \
             patch("fandomforge.assembly.orchestrator.subprocess.run", return_value=fake_proc):
            assert _validate_song_stream(fake) is False

    def test_valid_audio_returns_true(self, tmp_path: Path) -> None:
        fake = tmp_path / "song.mp3"
        fake.write_bytes(b"")
        # ffprobe emits 3 non-empty lines: codec_name, channels, sample_rate
        fake_proc = MagicMock(returncode=0, stdout="mp3\n2\n44100\n", stderr="")
        with patch("fandomforge.assembly.orchestrator.shutil.which", return_value="/usr/bin/ffprobe"), \
             patch("fandomforge.assembly.orchestrator.subprocess.run", return_value=fake_proc):
            assert _validate_song_stream(fake) is True

    def test_incomplete_probe_output_returns_false(self, tmp_path: Path) -> None:
        fake = tmp_path / "song.mp3"
        fake.write_bytes(b"")
        # Only 2 non-empty lines — missing sample_rate
        fake_proc = MagicMock(returncode=0, stdout="mp3\n2\n\n", stderr="")
        with patch("fandomforge.assembly.orchestrator.shutil.which", return_value="/usr/bin/ffprobe"), \
             patch("fandomforge.assembly.orchestrator.subprocess.run", return_value=fake_proc):
            assert _validate_song_stream(fake) is False


class TestLoadDialogueCues:
    def test_returns_empty_when_no_script(self, tmp_path: Path) -> None:
        cues, warnings = _load_dialogue_cues(None, tmp_path)
        assert cues == []
        assert warnings == []

    def test_returns_empty_when_script_missing(self, tmp_path: Path) -> None:
        cues, warnings = _load_dialogue_cues(tmp_path / "nope.json", tmp_path)
        assert cues == []
        assert warnings == []


class TestBuildRoughCutEarlyExits:
    def test_missing_shot_list_fails_fast(self, tmp_path: Path) -> None:
        proj = tmp_path / "proj"
        proj.mkdir()
        result = build_rough_cut(project_dir=proj, shot_list_name="nonexistent.md")
        assert result.success is False
        assert "Shot list not found" in result.stderr
