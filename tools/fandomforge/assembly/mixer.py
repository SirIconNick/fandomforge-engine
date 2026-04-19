"""Audio mixer — combine song + dialogue rips + SFX with ducking into a single audio track."""

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
class MixResult:
    success: bool
    output_path: Path | None
    duration_sec: float = 0.0
    dialogue_count: int = 0
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
) -> MixResult:
    """Mix a song with dialogue cues into a single audio file.

    Uses ffmpeg's complex filter graph:
    1. Apply per-cue gain + delay to each dialogue audio
    2. Mix all dialogue into a single "dialogue bus"
    3. Run sidechain compressor on song (triggered by dialogue bus) — auto-duck
    4. Amix song + dialogue
    5. Loudnorm to target LUFS

    Args:
        song_path: Path to the song audio file (will be trimmed to total_duration)
        dialogue_cues: list of DialogueCue objects with start times
        output_path: Where to write the mixed audio (WAV)
        total_duration_sec: trim output to this length (default: full song)
        song_gain_db: baseline song level
        target_lufs: integrated LUFS target (-14 = YouTube)
        sample_rate: output sample rate
        song_start_offset_sec: offset into the song if you want to skip the intro
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

    # Inputs 1..N: each dialogue cue
    valid_cues: list[tuple[int, DialogueCue]] = []
    for i, cue in enumerate(dialogue_cues):
        if not cue.audio_path.exists():
            continue
        cmd += ["-i", str(cue.audio_path)]
        valid_cues.append((i, cue))

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
        # Input index is based on position in cmd's -i list: song is input 0, first cue is 1
        label = f"[dcue{idx}]"
        filter_parts.append(
            f"[{input_i + 1}:a]volume={gain}dB,"
            f"adelay={delay_ms}|{delay_ms},"
            f"apad=pad_dur={total_duration_sec}"
            f"{label}"
        )
        dialogue_labels.append(label)

    if dialogue_labels:
        # Mix dialogue cues together into one bus (normalize=0 so each stays hot)
        mix_inputs = "".join(dialogue_labels)
        filter_parts.append(
            f"{mix_inputs}amix=inputs={len(dialogue_labels)}:"
            f"duration=longest:dropout_transition=0:normalize=0,"
            f"atrim=end={total_duration_sec}"
            f"[dialogue_out]"
        )

        # Deterministic volume automation on the song: duck each cue window.
        # Duck amount is per-cue (from DialogueCue.duck_db); default -12 dB.
        # 200ms linear fade in/out at each edge.
        fade_pad = 0.20
        vol_expr_parts: list[str] = []
        for _idx, cue in valid_cues:
            cue_dur = _probe_duration(cue.audio_path)
            if cue_dur <= 0:
                continue
            start = cue.start_sec
            end = start + cue_dur
            # Per-cue duck ratio from duck_db (dB -> linear)
            duck_ratio = max(0.01, 10 ** (cue.duck_db / 20))
            rise = 1 - duck_ratio
            vol_expr_parts.append(
                f"if(between(t,{start - fade_pad:.3f},{start:.3f}),"
                f"1-({rise:.4f})*(t-{start - fade_pad:.3f})/{fade_pad:.3f},"
                f"if(between(t,{start:.3f},{end:.3f}),{duck_ratio:.4f},"
                f"if(between(t,{end:.3f},{end + fade_pad:.3f}),"
                f"{duck_ratio:.4f}+({rise:.4f})*(t-{end:.3f})/{fade_pad:.3f},"
            )
        # Default: full volume (1.0). Close all the nested if() parens.
        vol_expr = "".join(vol_expr_parts) + "1" + ")))" * len(vol_expr_parts)

        filter_parts.append(
            f"[song_pre]volume='{vol_expr}':eval=frame[song_ducked]"
        )

        # Final mix: ducked song + dialogue.
        # No loudnorm — it dynamically compresses and would undo our duck.
        # Use alimiter for peak protection only (keeps dynamic range).
        filter_parts.append(
            "[song_ducked][dialogue_out]amix=inputs=2:duration=first:"
            f"dropout_transition=0:normalize=0,atrim=end={total_duration_sec},"
            f"alimiter=level_in=0.9:level_out=0.86:limit=0.86:attack=5:release=50[master]"
        )
    else:
        # No dialogue — just the song with alimiter
        filter_parts.append(
            f"[song_pre]alimiter=level_in=0.9:level_out=0.86:limit=0.86[master]"
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
    )
