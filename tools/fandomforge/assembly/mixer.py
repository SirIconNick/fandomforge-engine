"""Audio mixer — combine song + dialogue rips + SFX + scene audio into a single track.

Layer order (loudest logical to softest):
    1. Song       (target, ducked under dialogue)
    2. Dialogue   (ducks the song when present)
    3. SFX        (beat-aligned punches, gunshots, impacts — not ducked)
    4. Scene audio (source-clip ambient bleed — heavily gained down)
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DialogueCue:
    """A dialogue audio clip placed at a specific time on the timeline."""

    audio_path: Path
    start_sec: float
    gain_db: float = 0.0
    duck_db: float = -6.0  # how much to duck the song while this plays


@dataclass
class SfxCue:
    """A single SFX placement. Parallel shape to DialogueCue but no duck logic —
    SFX are brief transients that layer on top, not bed elements that should
    displace the song."""

    audio_path: Path
    start_sec: float
    gain_db: float = 0.0


@dataclass
class MixResult:
    success: bool
    output_path: Path | None
    duration_sec: float = 0.0
    dialogue_count: int = 0
    sfx_count: int = 0
    scene_audio_applied: bool = False
    warnings: list[str] = field(default_factory=list)
    stderr: str = ""


def _check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found. Install with: brew install ffmpeg")


def _probe_duration(path: Path) -> float:
    """Get the duration of an audio file in seconds."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        return 0.0


def mix_audio(
    song_path: Path | str,
    dialogue_cues: list[DialogueCue],
    output_path: Path | str,
    *,
    total_duration_sec: float | None = None,
    song_gain_db: float = -4.0,
    target_lufs: float = -14.0,
    sample_rate: int = 48000,
    song_start_offset_sec: float = 0.0,
    sfx_cues: list[SfxCue] | None = None,
    scene_audio_path: Path | None = None,
    scene_audio_gain_db: float = -20.0,
    scene_audio_duck_db: float | None = None,
    duck_fade_sec: float = 0.5,
) -> MixResult:
    """Mix a song with dialogue cues into a single audio file.

    Uses ffmpeg's complex filter graph:
    1. Apply per-cue gain + delay to each dialogue audio
    2. Mix all dialogue into a single "dialogue bus"
    3. Apply deterministic volume automation to the song — duck each cue
       window by cue.duck_db with `duck_fade_sec` linear fades on each edge.
    4. If scene-audio bed is present, apply the same automation. By default
       (scene_audio_duck_db=None) the scene mirrors the per-cue duck depth so
       it sounds like one unified, natural duck rather than "scene goes
       silent and back". Pass an explicit value (e.g. -10) to force a uniform
       depth, or -60 to nearly mute the bed.
    5. Amix song + dialogue + sfx + scene_bed → alimiter for peak protection.

    Args:
        song_path: Path to the song audio file (will be trimmed to total_duration)
        dialogue_cues: list of DialogueCue objects with start times
        output_path: Where to write the mixed audio (WAV)
        total_duration_sec: trim output to this length (default: full song)
        song_gain_db: baseline song level
        target_lufs: integrated LUFS target (-14 = YouTube)
        sample_rate: output sample rate
        song_start_offset_sec: offset into the song if you want to skip the intro
        scene_audio_path: optional WAV/AAC bed of source-clip ambient audio
        scene_audio_gain_db: baseline scene-audio level (typical -18 to -24 dB)
        scene_audio_duck_db: optional override for scene-audio duck depth
            during dialogue. None (default) = mirror per-cue duck_db so the
            scene drops by the same proportion as the song — feels like one
            natural duck. Set to a number to force uniform behavior.
        duck_fade_sec: linear fade in/out time at each cue edge for both
            song and scene ducks. Default 0.5s. Shorter values (0.1-0.2s)
            sound abrupt; longer values (0.7-1.0s) feel like a slow swell.
    """
    _check_ffmpeg()

    song_path = Path(song_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not song_path.exists():
        return MixResult(
            success=False,
            output_path=None,
            stderr=f"Song not found: {song_path}",
        )

    song_duration = _probe_duration(song_path)
    if total_duration_sec is None:
        total_duration_sec = song_duration - song_start_offset_sec

    # Build ffmpeg command
    cmd: list[str] = ["ffmpeg", "-y"]

    # Input 0: the song
    cmd += ["-i", str(song_path)]
    next_input = 1

    # Dialogue cue inputs
    valid_cues: list[tuple[int, DialogueCue]] = []
    for cue in dialogue_cues:
        if not cue.audio_path.exists():
            continue
        cmd += ["-i", str(cue.audio_path)]
        valid_cues.append((next_input, cue))
        next_input += 1

    # SFX cue inputs — filtered to only those whose audio file resolves
    valid_sfx: list[tuple[int, SfxCue]] = []
    for sfx in sfx_cues or []:
        if not sfx.audio_path.exists():
            continue
        cmd += ["-i", str(sfx.audio_path)]
        valid_sfx.append((next_input, sfx))
        next_input += 1

    # Scene audio: optional, single input. Skip when missing or path absent.
    scene_input_idx: int | None = None
    if scene_audio_path is not None and scene_audio_path.exists():
        cmd += ["-i", str(scene_audio_path)]
        scene_input_idx = next_input
        next_input += 1

    # Build filter graph
    filter_parts: list[str] = []

    # Song: offset + trim + gain
    song_filter = (
        f"[0:a]atrim=start={song_start_offset_sec}:"
        f"end={song_start_offset_sec + total_duration_sec},"
        f"asetpts=PTS-STARTPTS,"
        f"volume={song_gain_db}dB"
        f"[song_pre]"
    )
    filter_parts.append(song_filter)

    # Each dialogue cue: gain + delay to position on timeline
    dialogue_labels: list[str] = []
    for idx, (input_i, cue) in enumerate(valid_cues, start=1):
        delay_ms = int(cue.start_sec * 1000)
        gain = cue.gain_db
        # input_i is the actual ffmpeg input index (song is 0, first cue is 1, etc.)
        label = f"[dcue{idx}]"
        filter_parts.append(
            f"[{input_i}:a]volume={gain}dB,"
            f"adelay={delay_ms}|{delay_ms},"
            f"apad=pad_dur={total_duration_sec}"
            f"{label}"
        )
        dialogue_labels.append(label)

    # SFX events — no duck, just gain + delay + pad to timeline length.
    sfx_labels: list[str] = []
    for i, (input_i, sfx) in enumerate(valid_sfx, start=1):
        delay_ms = int(sfx.start_sec * 1000)
        label = f"[sfx{i}]"
        filter_parts.append(
            f"[{input_i}:a]volume={sfx.gain_db}dB,"
            f"adelay={delay_ms}|{delay_ms},"
            f"apad=pad_dur={total_duration_sec}"
            f"{label}"
        )
        sfx_labels.append(label)

    # Scene audio bed — trim to total duration, heavy gain reduction.
    scene_label: str | None = None
    if scene_input_idx is not None:
        scene_label = "[scene_bed]"
        filter_parts.append(
            f"[{scene_input_idx}:a]volume={scene_audio_gain_db}dB,"
            f"atrim=end={total_duration_sec},"
            f"asetpts=PTS-STARTPTS,"
            f"apad=pad_dur={total_duration_sec}"
            f"{scene_label}"
        )

    # Probe each cue's duration once — used by both song-duck and scene-duck
    # volume expressions. A cue with unreadable duration is dropped from the
    # ducking automation but still plays (its delay/gain/pad path is intact).
    duck_windows: list[tuple[float, float, float]] = []  # (start, end, per_cue_duck_db)
    for _idx, cue in valid_cues:
        cue_dur = _probe_duration(cue.audio_path)
        if cue_dur <= 0:
            continue
        duck_windows.append((cue.start_sec, cue.start_sec + cue_dur, cue.duck_db))

    fade_pad = max(0.05, float(duck_fade_sec))

    def _build_duck_expr(windows: list[tuple[float, float, float]], uniform_duck_db: float | None) -> str:
        """Build an ffmpeg volume= expression that ducks the carrier at each
        window. If uniform_duck_db is None, each window uses its own per-cue
        duck_db (the natural-blend default). If uniform_duck_db is provided,
        every window uses that depth (e.g. -10 dB for moderate uniform duck,
        -60 dB to nearly mute)."""
        parts: list[str] = []
        for start, end, per_cue_duck_db in windows:
            duck_db = uniform_duck_db if uniform_duck_db is not None else per_cue_duck_db
            duck_ratio = max(0.001, 10 ** (duck_db / 20))
            rise = 1 - duck_ratio
            parts.append(
                f"if(between(t,{start - fade_pad:.3f},{start:.3f}),"
                f"1-({rise:.4f})*(t-{start - fade_pad:.3f})/{fade_pad:.3f},"
                f"if(between(t,{start:.3f},{end:.3f}),{duck_ratio:.4f},"
                f"if(between(t,{end:.3f},{end + fade_pad:.3f}),"
                f"{duck_ratio:.4f}+({rise:.4f})*(t-{end:.3f})/{fade_pad:.3f},"
            )
        return "".join(parts) + "1" + ")))" * len(parts)

    if dialogue_labels:
        # Mix dialogue cues together into one bus (normalize=0 so each stays hot)
        mix_inputs = "".join(dialogue_labels)
        filter_parts.append(
            f"{mix_inputs}amix=inputs={len(dialogue_labels)}:"
            f"duration=longest:dropout_transition=0:normalize=0,"
            f"atrim=end={total_duration_sec}"
            f"[dialogue_out]"
        )

        # Deterministic volume automation on the song: per-cue duck depth.
        # 200ms linear fade in/out at each edge.
        if duck_windows:
            song_vol_expr = _build_duck_expr(duck_windows, uniform_duck_db=None)
            filter_parts.append(
                f"[song_pre]volume='{song_vol_expr}':eval=frame[song_ducked]"
            )
            song_bus_label = "[song_ducked]"
        else:
            song_bus_label = "[song_pre]"

        # Scene audio gets the same automation but uniformly ducked to silence
        # (default -60 dB) so injected dialogue isn't fighting source bleed.
        # Scene bleed comes back at full scene_audio_gain_db outside dialogue.
        if scene_label is not None and duck_windows:
            scene_vol_expr = _build_duck_expr(duck_windows, uniform_duck_db=scene_audio_duck_db)
            filter_parts.append(
                f"{scene_label}volume='{scene_vol_expr}':eval=frame[scene_ducked]"
            )
            scene_label = "[scene_ducked]"
    else:
        song_bus_label = "[song_pre]"

    # Build the final mix list. Start with song bus, then add dialogue / sfx /
    # scene layers that are present. Keep normalize=0 so layered gains stay
    # authoritative; close with alimiter for peak protection.
    final_layers: list[str] = [song_bus_label]
    if dialogue_labels:
        final_layers.append("[dialogue_out]")
    final_layers.extend(sfx_labels)
    if scene_label:
        final_layers.append(scene_label)

    if len(final_layers) == 1:
        filter_parts.append(
            f"{song_bus_label}alimiter=level_in=0.9:level_out=0.82:limit=0.82[master]"
        )
    else:
        filter_parts.append(
            f"{''.join(final_layers)}amix=inputs={len(final_layers)}:"
            f"duration=first:dropout_transition=0:normalize=0,"
            f"atrim=end={total_duration_sec},"
            f"alimiter=level_in=0.9:level_out=0.82:limit=0.82:"
            f"attack=5:release=50[master]"
        )

    filter_complex = ";".join(filter_parts)

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[master]",
        "-c:a", "pcm_s16le",
        "-ar", str(sample_rate),
        "-ac", "2",
        str(output_path),
    ]

    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=600)
    except subprocess.CalledProcessError as exc:
        return MixResult(
            success=False,
            output_path=None,
            stderr=(exc.stderr or str(exc))[-2000:],
        )
    except subprocess.TimeoutExpired:
        return MixResult(
            success=False,
            output_path=None,
            stderr="ffmpeg mix timed out after 600s",
        )

    return MixResult(
        success=True,
        output_path=output_path,
        duration_sec=total_duration_sec,
        dialogue_count=len(valid_cues),
        sfx_count=len(valid_sfx),
        scene_audio_applied=scene_input_idx is not None,
    )
