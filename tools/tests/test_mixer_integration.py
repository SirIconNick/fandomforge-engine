"""Integration-style tests for the extended mixer (SFX + scene audio).

Do NOT call real ffmpeg. Uses the SfxCue dataclass shape and confirms the
mixer gracefully handles missing SFX files (the common real case where the
user hasn't dropped variant WAVs yet).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fandomforge.assembly.mixer import DialogueCue, SfxCue, mix_audio


class TestSfxCueShape:
    def test_default_gain_is_zero(self) -> None:
        cue = SfxCue(audio_path=Path("x.wav"), start_sec=5.0)
        assert cue.gain_db == 0.0

    def test_custom_gain(self) -> None:
        cue = SfxCue(audio_path=Path("x.wav"), start_sec=5.0, gain_db=-3.0)
        assert cue.gain_db == -3.0


class TestMixAudioSkipsMissingSfx:
    """When an SFX file doesn't exist, the mixer silently skips it rather
    than crashing the render. This matches the real-world case where the
    sfx-plan references variants the user hasn't downloaded yet."""

    def test_missing_sfx_files_are_dropped(self, tmp_path: Path) -> None:
        # Confirm the mixer's filter-graph builder skips missing SFX paths.
        # We don't execute ffmpeg here — mock subprocess.run to short-circuit.
        song = tmp_path / "song.mp3"
        song.write_bytes(b"fake")

        missing_sfx = [
            SfxCue(audio_path=tmp_path / "nope1.wav", start_sec=1.0),
            SfxCue(audio_path=tmp_path / "nope2.wav", start_sec=2.0),
        ]

        from unittest.mock import MagicMock
        fake_run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        with patch("fandomforge.assembly.mixer.shutil.which",
                   return_value="/usr/bin/ffmpeg"), \
             patch("fandomforge.assembly.mixer._probe_duration", return_value=10.0), \
             patch("fandomforge.assembly.mixer.subprocess.run", fake_run):

            out = tmp_path / "mixed.wav"
            # Create a fake output so the success check doesn't fail.
            out.write_bytes(b"WAV")
            result = mix_audio(
                song_path=song,
                dialogue_cues=[],
                output_path=out,
                total_duration_sec=10.0,
                sfx_cues=missing_sfx,
            )
        assert result.success is True
        # Both SFX files were missing — sfx_count should be 0
        assert result.sfx_count == 0
