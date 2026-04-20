"""Integration-style tests for the extended mixer (SFX + scene audio).

Do NOT call real ffmpeg. Uses the SfxCue dataclass shape and confirms the
mixer gracefully handles missing SFX files (the common real case where the
user hasn't dropped variant WAVs yet).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from fandomforge.assembly.mixer import DialogueCue, SfxCue, mix_audio


def _capture_filter_complex(call_args) -> str:
    """Pull the -filter_complex argument out of a mocked subprocess.run call."""
    cmd = call_args[0][0]
    idx = cmd.index("-filter_complex")
    return cmd[idx + 1]


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


class TestSceneAudioDialogueDucking:
    """Phase 0.2: when scene audio AND dialogue cues are both present, the
    mixer must duck the scene_audio bed during each cue window so injected
    narrative dialogue lands clean. Mirrors the song-duck logic but with a
    uniform duck depth (default -60 dB ≈ silent)."""

    def _build_files(self, tmp_path: Path) -> tuple[Path, Path, Path, Path]:
        song = tmp_path / "song.mp3"
        song.write_bytes(b"fake-song")
        scene = tmp_path / "scene_bed.wav"
        scene.write_bytes(b"fake-scene")
        cue1 = tmp_path / "cue1.wav"
        cue1.write_bytes(b"fake-cue")
        out = tmp_path / "mixed.wav"
        return song, scene, cue1, out

    def test_scene_audio_ducked_when_dialogue_present(self, tmp_path: Path) -> None:
        song, scene, cue1, out = self._build_files(tmp_path)
        cues = [DialogueCue(audio_path=cue1, start_sec=2.0, duck_db=-10.0)]
        fake_run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        with patch("fandomforge.assembly.mixer.shutil.which",
                   return_value="/usr/bin/ffmpeg"), \
             patch("fandomforge.assembly.mixer._probe_duration", return_value=3.0), \
             patch("fandomforge.assembly.mixer.subprocess.run", fake_run):
            out.write_bytes(b"WAV")
            result = mix_audio(
                song_path=song,
                dialogue_cues=cues,
                output_path=out,
                total_duration_sec=10.0,
                scene_audio_path=scene,
            )

        assert result.success is True
        assert result.scene_audio_applied is True
        graph = _capture_filter_complex(fake_run.call_args)
        # Scene bed should be created and then ducked.
        assert "[scene_bed]" in graph
        assert "[scene_ducked]" in graph
        # The ducked label should be the one that flows into the final amix,
        # not the bare bed.
        assert "scene_ducked]amix" in graph or "[scene_ducked]amix" in graph

    def test_no_dialogue_no_scene_duck(self, tmp_path: Path) -> None:
        """Without dialogue cues, scene audio should not be ducked — the bed
        plays at full scene_audio_gain_db throughout."""
        song, scene, _cue1, out = self._build_files(tmp_path)
        fake_run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        with patch("fandomforge.assembly.mixer.shutil.which",
                   return_value="/usr/bin/ffmpeg"), \
             patch("fandomforge.assembly.mixer._probe_duration", return_value=10.0), \
             patch("fandomforge.assembly.mixer.subprocess.run", fake_run):
            out.write_bytes(b"WAV")
            result = mix_audio(
                song_path=song,
                dialogue_cues=[],
                output_path=out,
                total_duration_sec=10.0,
                scene_audio_path=scene,
            )

        assert result.success is True
        graph = _capture_filter_complex(fake_run.call_args)
        assert "[scene_bed]" in graph
        assert "[scene_ducked]" not in graph

    def test_no_scene_no_scene_filter(self, tmp_path: Path) -> None:
        """Without scene audio, no scene_bed / scene_ducked label should
        appear in the graph at all — backwards compatible with old behavior."""
        song, _scene, cue1, out = self._build_files(tmp_path)
        cues = [DialogueCue(audio_path=cue1, start_sec=2.0, duck_db=-10.0)]
        fake_run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        with patch("fandomforge.assembly.mixer.shutil.which",
                   return_value="/usr/bin/ffmpeg"), \
             patch("fandomforge.assembly.mixer._probe_duration", return_value=3.0), \
             patch("fandomforge.assembly.mixer.subprocess.run", fake_run):
            out.write_bytes(b"WAV")
            result = mix_audio(
                song_path=song,
                dialogue_cues=cues,
                output_path=out,
                total_duration_sec=10.0,
            )

        assert result.success is True
        graph = _capture_filter_complex(fake_run.call_args)
        assert "[scene_bed]" not in graph
        assert "[scene_ducked]" not in graph
        # Song duck still happens because dialogue is present.
        assert "[song_ducked]" in graph

    def test_scene_audio_duck_db_param_changes_ratio(self, tmp_path: Path) -> None:
        """Passing scene_audio_duck_db=-20 should produce a different duck
        ratio in the volume expression than the default -60."""
        song, scene, cue1, out = self._build_files(tmp_path)
        cues = [DialogueCue(audio_path=cue1, start_sec=1.0, duck_db=-10.0)]

        def _run_with_duck(duck_db: float) -> str:
            fake_run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
            with patch("fandomforge.assembly.mixer.shutil.which",
                       return_value="/usr/bin/ffmpeg"), \
                 patch("fandomforge.assembly.mixer._probe_duration", return_value=2.0), \
                 patch("fandomforge.assembly.mixer.subprocess.run", fake_run):
                out.write_bytes(b"WAV")
                mix_audio(
                    song_path=song,
                    dialogue_cues=cues,
                    output_path=out,
                    total_duration_sec=10.0,
                    scene_audio_path=scene,
                    scene_audio_duck_db=duck_db,
                )
            return _capture_filter_complex(fake_run.call_args)

        graph_default = _run_with_duck(-60.0)
        graph_softer = _run_with_duck(-20.0)

        # Both contain a scene_ducked filter, but the embedded duck_ratio differs.
        # 10 ** (-60 / 20) = 0.001  (clamped at 0.001 floor in the builder)
        # 10 ** (-20 / 20) = 0.1
        # Look for the distinctive ratios in the scene_ducked stretch.
        assert "[scene_ducked]" in graph_default
        assert "[scene_ducked]" in graph_softer
        # The default-60 case should hit the 0.001 clamp; the -20 case should show 0.1
        assert "0.0010" in graph_default
        assert "0.1000" in graph_softer

    def test_scene_duck_window_aligns_with_cue(self, tmp_path: Path) -> None:
        """The scene-duck window for a cue at start=2.0s with 3.0s duration
        should run roughly 2.0–5.0s with a 200ms fade pad on each side."""
        song, scene, cue1, out = self._build_files(tmp_path)
        cues = [DialogueCue(audio_path=cue1, start_sec=2.0, duck_db=-12.0)]
        fake_run = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))

        with patch("fandomforge.assembly.mixer.shutil.which",
                   return_value="/usr/bin/ffmpeg"), \
             patch("fandomforge.assembly.mixer._probe_duration", return_value=3.0), \
             patch("fandomforge.assembly.mixer.subprocess.run", fake_run):
            out.write_bytes(b"WAV")
            mix_audio(
                song_path=song,
                dialogue_cues=cues,
                output_path=out,
                total_duration_sec=10.0,
                scene_audio_path=scene,
            )

        graph = _capture_filter_complex(fake_run.call_args)
        # Window edges: fade-in starts at 1.800, hold at 2.000–5.000, fade-out ends 5.200.
        # The expression contains literal between(t,1.800,2.000) and between(t,2.000,5.000).
        assert "between(t,1.800,2.000)" in graph
        assert "between(t,2.000,5.000)" in graph
        assert "between(t,5.000,5.200)" in graph
