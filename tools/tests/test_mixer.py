"""Smoke tests for the audio mixer's cue handling."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from fandomforge.assembly.mixer import DialogueCue, mix_audio


class TestMixAudioNoSong:
    def test_returns_failure_when_song_missing(self, tmp_path: Path) -> None:
        # Need to mock ffmpeg being present so the early check() doesn't raise
        with patch("fandomforge.assembly.mixer.shutil.which", return_value="/usr/bin/ffmpeg"):
            out = tmp_path / "mixed.wav"
            result = mix_audio(
                song_path=tmp_path / "nope.mp3",
                dialogue_cues=[],
                output_path=out,
                total_duration_sec=10.0,
            )
        assert result.success is False
        assert "Song not found" in result.stderr


class TestDialogueCueDataclass:
    def test_defaults(self) -> None:
        cue = DialogueCue(audio_path=Path("a.wav"), start_sec=1.0)
        assert cue.gain_db == 0.0
        assert cue.duck_db == -6.0

    def test_custom_values(self) -> None:
        cue = DialogueCue(
            audio_path=Path("a.wav"), start_sec=2.5,
            gain_db=3.0, duck_db=-12.0,
        )
        assert cue.gain_db == 3.0
        assert cue.duck_db == -12.0
