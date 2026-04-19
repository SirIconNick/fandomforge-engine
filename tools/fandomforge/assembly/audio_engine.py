"""Broadcast-quality dialogue-over-music mixing engine.

Replaces mixer.py's sidechain compression approach with a deterministic
volume-automation ducking strategy that eliminates pumping artifacts and
guarantees dialogue intelligibility.

Signal chain summary
--------------------
Per dialogue cue:
  HPF 90 Hz -> de-ess 6 kHz -> parametric EQ (presence/mud) ->
  compressor 3:1 / -20 dBFS / 10ms-100ms -> makeup gain to -9 LUFS

Song bed:
  loudnorm to -18 LUFS -> deterministic volume envelope (duck during VO
  windows) -> high-shelf dip 2-5 kHz during VO windows

Master bus:
  dialogue + song -> subtle stereo widener -> multiband limiter
  targeting -14 LUFS / -1 dBTP ceiling

NOTE: No global loudnorm on the final master. A post-mix loudnorm would
re-compress and destroy the carefully built ducking envelope.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CompressionProfile:
    """Compressor parameters for a dialogue signal chain.

    Defaults deliver 3:1 glue compression, not pumping limiting.
    """

    ratio: float = 3.0
    threshold_db: float = -20.0
    attack_ms: float = 10.0
    release_ms: float = 100.0
    makeup_db: float = 0.0  # applied after compression; auto-derived if 0


@dataclass
class EQProfile:
    """Parametric EQ settings for a dialogue signal chain."""

    presence_hz: float = 3500.0
    presence_db: float = 3.0
    mud_hz: float = 250.0
    mud_db: float = -2.5
    hpf_hz: float = 90.0
    deess_hz: float = 6000.0
    deess_db: float = -4.0


@dataclass
class DialogueCue:
    """A single dialogue clip positioned on the timeline.

    Attributes:
        audio_path: Path to the source WAV or audio file.
        start_sec: Position in the final mix where this cue begins, in seconds.
        duck_db: How far to pull the song down during this cue window.
            -12 dB is a moderate duck (speech clear, music still present).
            -18 dB is aggressive (almost inaudible music bed).
        gain_db: Additional output gain applied after the full signal chain.
            Normally left at 0; use only for intentional level offsets.
        eq_profile: EQ parameters for this cue (defaults to broadcast preset).
        compression_profile: Compressor parameters (defaults to 3:1 glue).
    """

    audio_path: Path
    start_sec: float
    duck_db: float = -18.0
    gain_db: float = 0.0
    eq_profile: EQProfile = field(default_factory=EQProfile)
    compression_profile: CompressionProfile = field(
        default_factory=CompressionProfile
    )


@dataclass
class CueMeasurement:
    """Loudness and lift measurement for a single dialogue cue.

    Attributes:
        audio_path: Source file for reference.
        start_sec: Cue start position in the mix.
        duration_sec: Cue duration.
        voice_lufs: Integrated LUFS of the voice-band (300-3400 Hz) in the
            output during this cue window.
        ambient_lufs: Integrated LUFS of the voice-band in the 2-second window
            immediately before this cue (song-only reference).
        lift_db: voice_lufs - ambient_lufs. Must be >= 6 dB to pass.
        passed: True when lift_db >= LIFT_THRESHOLD_DB.
    """

    audio_path: Path
    start_sec: float
    duration_sec: float
    voice_lufs: float = 0.0
    ambient_lufs: float = 0.0
    lift_db: float = 0.0
    passed: bool = False


@dataclass
class MixResult:
    """Result from a mix() call.

    Attributes:
        success: False if any fatal error occurred or any cue failed lift check.
        output_path: Written file path, or None on hard failure.
        actual_lufs: Integrated LUFS of the complete output file.
        peak_dbfs: True-peak dBFS of the output.
        duration_sec: Output duration in seconds.
        dialogue_count: Number of valid cues that were processed.
        cue_measurements: Per-cue intelligibility measurements.
        warnings: Non-fatal notes.
        errors: Fatal or fail-threshold messages.
        stderr: Raw ffmpeg stderr on failure (last 2000 chars).
    """

    success: bool
    output_path: Optional[Path] = None
    actual_lufs: float = 0.0
    peak_dbfs: float = 0.0
    duration_sec: float = 0.0
    dialogue_count: int = 0
    cue_measurements: list[CueMeasurement] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stderr: str = ""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LIFT_THRESHOLD_DB: float = 6.0   # minimum voice-band lift to pass
TARGET_LUFS: float = -14.0       # broadcast integrated target
TRUE_PEAK_CEILING: float = -1.0  # dBTP ceiling
SONG_BED_LUFS: float = -18.0     # song loudnorm target (under-VO reference)
CUE_TARGET_LUFS: float = -9.0    # per-cue normalisation target; hot enough to
                                  # stand 8-10 dB above a dense ducked bed
FADE_MS: float = 150.0           # edge fade duration for ducking envelope
AMBIENT_WINDOW_SEC: float = 2.0  # desired pre-cue reference window length
SAMPLE_RATE: int = 48000


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_ffmpeg() -> None:
    """Raise RuntimeError if ffmpeg is not on PATH."""
    import shutil
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg not found on PATH. Install via: brew install ffmpeg"
        )


def _run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    """Run a subprocess, capturing output. Raises on non-zero exit."""
    logger.debug("Running: %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )


def _probe_duration(path: Path) -> float:
    """Return duration in seconds via ffprobe. Returns 0.0 on any failure."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError, TypeError):
        return 0.0


def _probe_sample_rate(path: Path) -> int:
    """Return sample rate in Hz via ffprobe. Returns SAMPLE_RATE on failure."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return int(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError, TypeError):
        return SAMPLE_RATE


def _measure_lufs(path: Path) -> tuple[float, float]:
    """Return (integrated_lufs, true_peak_dbfs) for the given file.

    Uses ffmpeg's ebur128 filter. Returns (-99.0, -99.0) on failure.
    """
    cmd = [
        "ffmpeg", "-nostats", "-i", str(path),
        "-filter:a", "ebur128=peak=true:framelog=verbose",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False
        )
        stderr = result.stderr

        lufs_match = re.search(r"I:\s+([-\d.]+)\s+LUFS", stderr)
        peak_match = re.search(r"Peak:\s+([-\d.]+)\s+dBFS", stderr)

        lufs = float(lufs_match.group(1)) if lufs_match else -99.0
        peak = float(peak_match.group(1)) if peak_match else -99.0
        return lufs, peak
    except Exception:
        return -99.0, -99.0


def _measure_bandpass_lufs(path: Path, low_hz: float, high_hz: float) -> float:
    """Measure integrated LUFS after a bandpass filter (used for lift checks).

    Applies a Butterworth bandpass between low_hz and high_hz before measuring.
    Returns -99.0 on failure.
    """
    bandpass_chain = (
        f"highpass=f={low_hz}:poles=2,"
        f"lowpass=f={high_hz}:poles=2,"
        "ebur128=framelog=verbose"
    )
    cmd = [
        "ffmpeg", "-nostats", "-i", str(path),
        "-filter:a", bandpass_chain,
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False
        )
        m = re.search(r"I:\s+([-\d.]+)\s+LUFS", result.stderr)
        return float(m.group(1)) if m else -99.0
    except Exception:
        return -99.0


def _measure_segment_lufs(
    path: Path, start_sec: float, duration_sec: float,
    low_hz: float = 300.0, high_hz: float = 3400.0
) -> float:
    """Measure voice-band LUFS in a time segment of a file.

    Extracts the segment, applies bandpass filter, and returns integrated LUFS.
    """
    if duration_sec <= 0:
        return -99.0

    bandpass_chain = (
        f"atrim=start={start_sec:.3f}:duration={duration_sec:.3f},"
        f"asetpts=PTS-STARTPTS,"
        f"highpass=f={low_hz}:poles=2,"
        f"lowpass=f={high_hz}:poles=2,"
        "ebur128=framelog=verbose"
    )
    cmd = [
        "ffmpeg", "-nostats", "-i", str(path),
        "-filter:a", bandpass_chain,
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False
        )
        m = re.search(r"I:\s+([-\d.]+)\s+LUFS", result.stderr)
        return float(m.group(1)) if m else -99.0
    except Exception:
        return -99.0


def _loudnorm_analysis(path: Path, target_lufs: float = -18.0) -> dict:
    """Run loudnorm pass-1 analysis to obtain normalisation coefficients.

    Returns a dict with keys: input_i, input_tp, input_lra, input_thresh,
    target_offset, or an empty dict on failure or on silent input.
    """
    filter_str = (
        f"loudnorm=I={target_lufs}:TP={TRUE_PEAK_CEILING}:LRA=11"
        ":print_format=json"
    )
    cmd = [
        "ffmpeg", "-nostats", "-i", str(path),
        "-filter:a", filter_str,
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False
        )
        m = re.search(r"\{[^}]+\}", result.stderr, re.DOTALL)
        if m:
            data = json.loads(m.group())
            # If the signal is silent or near-silent the analysis returns '-inf'
            # strings, which ffmpeg will reject when passed back as measured_*.
            # Return empty dict to trigger the simple one-pass fallback.
            if data.get("input_i") in ("-inf", "inf", None):
                logger.warning(
                    "Loudnorm analysis of %s returned -inf input_i; "
                    "file may be near-silent or extremely short.",
                    path.name,
                )
                return {}
            return data
    except Exception:
        pass
    return {}


# ---------------------------------------------------------------------------
# Per-cue signal chain
# ---------------------------------------------------------------------------


def _build_cue_filter(
    eq: EQProfile,
    comp: CompressionProfile,
    target_lufs: float = CUE_TARGET_LUFS,
) -> str:
    """Return an ffmpeg filter-chain string for a single dialogue cue.

    Chain: HPF -> de-ess (peak EQ dip) -> parametric EQ presence/mud ->
           compress (acompressor) -> volume gain.

    The acompressor filter uses ratio, threshold, attack, and release as
    direct parameters. Makeup gain is applied as a separate volume filter
    so it is easy to override per-cue.

    Args:
        eq: EQ parameters.
        comp: Compression parameters.
        target_lufs: Not used for real-time normalisation here (that is done
            in the prep stage), but kept for documentation alignment.

    Returns:
        Comma-separated ffmpeg audio filter chain string (no input/output
        labels, suitable for embedding in a larger filtergraph).
    """
    # HPF to remove low-frequency rumble
    hpf = f"highpass=f={eq.hpf_hz:.0f}:poles=2"

    # De-esser: narrow peak EQ dip at sibilance frequency.
    # Use t=h (Hz bandwidth) with a tight 2000 Hz wide notch at 6 kHz.
    # t=q:w=4 would also work; Hz mode gives more predictable bandwidth.
    deess = (
        f"equalizer=f={eq.deess_hz:.0f}:t=h:w=2000:g={eq.deess_db:.1f}"
    )

    # Presence boost (upper midrange 3-4 kHz, Q=2 = moderately wide)
    # t=q:w=2 gives about 1.5 octaves around centre frequency
    presence = (
        f"equalizer=f={eq.presence_hz:.0f}:t=q:w=2.0:g={eq.presence_db:.1f}"
    )

    # Mud cut (250 Hz, Q=1.5 = moderate width)
    mud = f"equalizer=f={eq.mud_hz:.0f}:t=q:w=1.5:g={eq.mud_db:.1f}"

    # Compressor: 3:1 ratio, threshold in linear (ffmpeg acompressor uses
    # linear threshold 0..1 relative to max amplitude).
    # makeup is also a linear multiplier (1.0 = 0 dB, 2.0 = +6 dB).
    # We set makeup=1.0 here; loudnorm in stage 2 provides the true level target.
    threshold_linear = 10 ** (comp.threshold_db / 20.0)
    makeup_linear = 10 ** (comp.makeup_db / 20.0)
    acomp = (
        f"acompressor=threshold={threshold_linear:.5f}"
        f":ratio={comp.ratio:.1f}"
        f":attack={comp.attack_ms:.1f}"
        f":release={comp.release_ms:.1f}"
        f":makeup={makeup_linear:.4f}"
    )

    parts = [hpf, deess, presence, mud, acomp]
    return ",".join(parts)


def _prepare_cue(
    cue: DialogueCue,
    tmp_dir: Path,
    index: int,
) -> tuple[Path, float]:
    """Apply the full per-cue signal chain, normalise to CUE_TARGET_LUFS.

    Two-pass process:
    1. Apply HPF/EQ/compressor, write to a temp file.
    2. Run loudnorm analysis on that file, then write final normalised cue.

    Returns:
        Tuple of (processed_path, duration_sec). processed_path is a
        temporary 48 kHz stereo PCM file. duration_sec is the actual output
        duration.
    """
    src = cue.audio_path
    stage0 = tmp_dir / f"cue_{index:03d}_stage0.wav"
    stage1 = tmp_dir / f"cue_{index:03d}_stage1.wav"
    stage2 = tmp_dir / f"cue_{index:03d}_stage2.wav"

    # Stage 0: pre-limit source to -1 dBTP so subsequent loudnorm has headroom.
    # Sources that are already at 0 dBFS cannot be pushed louder without this step.
    # alimiter: level_in=1.0 (unity input gain), limit sets the ceiling in linear.
    pre_limit_level = 10 ** (-1.0 / 20.0)  # -1 dBTP in linear = 0.89125
    pre_limit_filter = (
        f"alimiter=level_in=1.0"
        f":level_out=1.0"
        f":limit={pre_limit_level:.5f}:attack=5:release=50:level=false"
    )
    cmd0 = [
        "ffmpeg", "-y", "-i", str(src),
        "-filter:a", pre_limit_filter,
        "-ar", str(SAMPLE_RATE), "-ac", "2",
        "-c:a", "pcm_s16le",
        str(stage0),
    ]
    try:
        _run(cmd0)
    except subprocess.CalledProcessError as exc:
        logger.error("Cue %d stage-0 pre-limit failed: %s", index, exc.stderr[-500:])
        raise

    # Stage 1: signal chain without normalisation
    chain = _build_cue_filter(cue.eq_profile, cue.compression_profile)

    cmd1 = [
        "ffmpeg", "-y", "-i", str(stage0),
        "-filter:a", chain,
        "-ar", str(SAMPLE_RATE), "-ac", "2",
        "-c:a", "pcm_s16le",
        str(stage1),
    ]
    try:
        _run(cmd1)
    except subprocess.CalledProcessError as exc:
        logger.error("Cue %d stage-1 failed: %s", index, exc.stderr[-500:])
        raise

    # Stage 2: loudnorm analysis pass then linear normalisation pass.
    # Use a slightly relaxed TP ceiling here (-0.5 dBTP) so that short
    # or spectrally dense cues are not prevented from reaching the target
    # by peak limiting. The final master's alimiter enforces -1 dBTP.
    CUE_TP = -0.5
    analysis = _loudnorm_analysis(stage1, target_lufs=CUE_TARGET_LUFS)

    if analysis:
        norm_filter = (
            f"loudnorm=I={CUE_TARGET_LUFS}"
            f":TP={CUE_TP}:LRA=11:linear=true"
            f":measured_I={analysis.get('input_i', '-23')}"
            f":measured_TP={analysis.get('input_tp', '-2')}"
            f":measured_LRA={analysis.get('input_lra', '7')}"
            f":measured_thresh={analysis.get('input_thresh', '-33')}"
            f":offset={analysis.get('target_offset', '0')}"
            f":print_format=summary"
        )
    else:
        # Fallback: simple loudnorm (one-pass, slightly less accurate)
        norm_filter = (
            f"loudnorm=I={CUE_TARGET_LUFS}:TP={CUE_TP}:LRA=11"
        )

    # Also apply output gain_db at this stage
    gain_linear = 10 ** (cue.gain_db / 20.0)
    full_chain = f"{norm_filter},volume={gain_linear:.5f}"

    cmd2 = [
        "ffmpeg", "-y", "-i", str(stage1),
        "-filter:a", full_chain,
        "-ar", str(SAMPLE_RATE), "-ac", "2",
        "-c:a", "pcm_s16le",
        str(stage2),
    ]
    try:
        _run(cmd2)
    except subprocess.CalledProcessError as exc:
        logger.error("Cue %d stage-2 normalisation failed: %s", index, exc.stderr[-500:])
        raise

    dur = _probe_duration(stage2)
    return stage2, dur


# ---------------------------------------------------------------------------
# Song prep chain
# ---------------------------------------------------------------------------


def _prepare_song(
    song_path: Path,
    offset_sec: float,
    total_duration: float,
    tmp_dir: Path,
) -> Path:
    """Trim and loudnorm the song to SONG_BED_LUFS (-18 LUFS).

    This is the quiet under-VO bed level. The ducking envelope will then
    pull it further during cue windows.

    Two-pass loudnorm for linear correction (minimal sonic character change).

    Args:
        song_path: Source song file.
        offset_sec: Start offset within the song.
        total_duration: How long to keep after the offset.
        tmp_dir: Working directory for temp files.

    Returns:
        Path to the prepared stereo 48 kHz PCM song file.
    """
    stage1 = tmp_dir / "song_stage1.wav"
    stage2 = tmp_dir / "song_stage2.wav"

    # Stage 1: trim to window, convert to 48 kHz stereo
    trim_filter = (
        f"atrim=start={offset_sec:.3f}:duration={total_duration:.3f},"
        "asetpts=PTS-STARTPTS"
    )
    cmd1 = [
        "ffmpeg", "-y", "-i", str(song_path),
        "-filter:a", trim_filter,
        "-ar", str(SAMPLE_RATE), "-ac", "2",
        "-c:a", "pcm_s16le",
        str(stage1),
    ]
    _run(cmd1)

    # Stage 2a: loudnorm analysis
    analysis = _loudnorm_analysis(stage1, target_lufs=SONG_BED_LUFS)

    if analysis:
        norm_filter = (
            f"loudnorm=I={SONG_BED_LUFS}"
            f":TP={TRUE_PEAK_CEILING}:LRA=11:linear=true"
            f":measured_I={analysis.get('input_i', '-23')}"
            f":measured_TP={analysis.get('input_tp', '-2')}"
            f":measured_LRA={analysis.get('input_lra', '7')}"
            f":measured_thresh={analysis.get('input_thresh', '-33')}"
            f":offset={analysis.get('target_offset', '0')}"
        )
    else:
        norm_filter = f"loudnorm=I={SONG_BED_LUFS}:TP={TRUE_PEAK_CEILING}:LRA=11"

    cmd2 = [
        "ffmpeg", "-y", "-i", str(stage1),
        "-filter:a", norm_filter,
        "-ar", str(SAMPLE_RATE), "-ac", "2",
        "-c:a", "pcm_s16le",
        str(stage2),
    ]
    _run(cmd2)

    return stage2


# ---------------------------------------------------------------------------
# Ducking envelope builder
# ---------------------------------------------------------------------------


def _build_duck_envelope(
    valid_cues: list[tuple[DialogueCue, float]],
    total_duration: float,
    fade_ms: float = FADE_MS,
) -> str:
    """Return an ffmpeg volume filter expression string for ducking.

    Builds a nested if() expression covering every cue window. Outside all
    windows the volume is 1.0 (no attenuation). Inside each window, it ramps
    linearly from 1.0 to the duck_db level over fade_ms ms, holds, then
    ramps back.

    Args:
        valid_cues: List of (DialogueCue, cue_duration_sec) pairs.
        total_duration: Total mix duration (informational, not used in expr).
        fade_ms: Ramp duration in milliseconds on each edge.

    Returns:
        ffmpeg volume filter expression string (value for the 'volume' param).
    """
    fade_sec = fade_ms / 1000.0

    # Sort cues by start time so overlapping fades make intuitive sense
    sorted_cues = sorted(valid_cues, key=lambda x: x[0].start_sec)

    if not sorted_cues:
        return "1"

    # Build the full expression as a single string by prepending each cue's
    # fragment and appending closing parens at the end. The structure is:
    #   if(ramp_down, ramp_val, if(hold, duck_val, if(ramp_up, ramp_val, <next>)))
    # where <next> is the next cue's block, and the innermost <next> is "1" (unity).
    full_parts: list[str] = []
    close_count = 0
    for cue, cue_dur in sorted_cues:
        duck_linear = max(0.001, 10 ** (cue.duck_db / 20.0))
        rise = 1.0 - duck_linear

        fi_start = cue.start_sec - fade_sec
        fi_end = cue.start_sec
        hold_end = cue.start_sec + cue_dur
        fo_end = hold_end + fade_sec

        full_parts.append(
            f"if(between(t,{fi_start:.4f},{fi_end:.4f}),"
            f"1-{rise:.6f}*(t-{fi_start:.4f})/{fade_sec:.4f},"
            f"if(between(t,{fi_end:.4f},{hold_end:.4f}),"
            f"{duck_linear:.6f},"
            f"if(between(t,{hold_end:.4f},{fo_end:.4f}),"
            f"{duck_linear:.6f}+{rise:.6f}*(t-{hold_end:.4f})/{fade_sec:.4f},"
        )
        close_count += 3

    full_parts.append("1")
    full_parts.append(")" * close_count)

    return "".join(full_parts)


# ---------------------------------------------------------------------------
# Master bus
# ---------------------------------------------------------------------------


def _apply_master_chain(
    premix_path: Path,
    output_path: Path,
    target_lufs: float = TARGET_LUFS,
    true_peak: float = TRUE_PEAK_CEILING,
) -> None:
    """Apply stereo widener + multiband limiter to the premix.

    The master chain is intentionally kept simple:
      - extrastereo for subtle widening (factor 1.2, keeps mono compatibility)
      - alimiter for true-peak limiting to -1 dBTP
      - No loudnorm: the integrated level is controlled by cue prep + ducking

    Args:
        premix_path: Input file from the ducked mix stage.
        output_path: Final output path.
        target_lufs: Not applied via loudnorm; used only for the limiter level
            which is set to just below the true-peak ceiling.
        true_peak: dBTP ceiling.
    """
    # Limiter level in linear (just below true peak ceiling)
    limit_level = 10 ** (true_peak / 20.0)

    master_chain = (
        # Subtle stereo widening: extrastereo factor 1.2 is barely perceptible
        # but adds a fraction of air. Clamp to prevent mono-collapse issues.
        "extrastereo=m=1.2:c=true,"
        # alimiter: hard true-peak limiter. attack 5ms, release 50ms.
        f"alimiter=level_in={limit_level:.5f}:level_out={limit_level:.5f}"
        f":limit={limit_level:.5f}:attack=5:release=50:level=false"
    )

    cmd = [
        "ffmpeg", "-y", "-i", str(premix_path),
        "-filter:a", master_chain,
        "-ar", str(SAMPLE_RATE), "-ac", "2",
        "-c:a", "pcm_s16le",
        str(output_path),
    ]
    _run(cmd)


# ---------------------------------------------------------------------------
# Per-cue lift verification
# ---------------------------------------------------------------------------


def _find_clean_ambient_window(
    cue: DialogueCue,
    all_cue_pairs: list[tuple[DialogueCue, float]],
    total_duration: float,
    desired_duration: float = AMBIENT_WINDOW_SEC,
) -> tuple[float, float]:
    """Find a clean ambient window (song-only, no cue audio, no fade region).

    Searches for the longest available gap before the target cue that is free
    from all cue audio and their associated fade regions. Falls back to post-cue
    if no sufficient pre-cue window exists.

    Args:
        cue: The cue whose ambient reference we need.
        all_cue_pairs: All (DialogueCue, duration) pairs in the mix.
        total_duration: Total mix duration.
        desired_duration: How long an ambient window we want (seconds).

    Returns:
        (start_sec, duration_sec) of the best available clean window.
    """
    fade_sec = FADE_MS / 1000.0
    # Build blocked regions: every cue's full fade+hold+fade window
    blocked: list[tuple[float, float]] = []
    for c, dur in all_cue_pairs:
        block_start = max(0.0, c.start_sec - fade_sec)
        block_end = c.start_sec + dur + fade_sec
        blocked.append((block_start, block_end))

    blocked.sort()

    # Find gaps before the target cue, searching backwards
    target_fence = max(0.0, cue.start_sec - fade_sec)

    # Collect free intervals before target_fence
    free_before: list[tuple[float, float]] = []
    prev_end = 0.0
    for bstart, bend in blocked:
        if bstart >= target_fence:
            break
        if prev_end < bstart:
            free_before.append((prev_end, min(bstart, target_fence)))
        prev_end = max(prev_end, bend)
    if prev_end < target_fence:
        free_before.append((prev_end, target_fence))

    # Pick the best (longest, closest to the cue) free window before the cue
    best_start: float | None = None
    best_dur: float = 0.0
    for seg_start, seg_end in reversed(free_before):
        avail = seg_end - seg_start
        if avail >= 0.25:
            use_dur = min(avail, desired_duration)
            use_start = seg_end - use_dur  # use the END of the gap (closest to cue)
            if use_dur > best_dur:
                best_start = use_start
                best_dur = use_dur

    if best_start is not None and best_dur >= 0.25:
        return best_start, best_dur

    # Fallback: use post-cue window
    post_start = cue.start_sec + (
        _probe_duration(cue.audio_path) if hasattr(cue.audio_path, 'exists') else 0
    ) + fade_sec
    post_dur = min(desired_duration, total_duration - post_start)
    if post_dur >= 0.25:
        return post_start, post_dur

    # Last resort: use whatever is available before the cue
    if cue.start_sec >= 0.25:
        return max(0.0, cue.start_sec - desired_duration), min(
            desired_duration, cue.start_sec
        )

    return 0.0, min(desired_duration, total_duration)


def _measure_cue_lift(
    output_path: Path,
    cue: DialogueCue,
    cue_dur: float,
    all_cue_pairs: list[tuple[DialogueCue, float]] | None = None,
    total_duration: float = 0.0,
) -> CueMeasurement:
    """Measure voice-band lift for a single cue in the final output.

    Computes LUFS in the 300-3400 Hz band:
    - During the cue window (voice_lufs)
    - In a clean ambient window free from any cue or fade content (ambient_lufs)

    Lift = voice_lufs - ambient_lufs. Passes if lift >= LIFT_THRESHOLD_DB.

    Args:
        output_path: The final mixed output file.
        cue: The DialogueCue being measured.
        cue_dur: Duration of the cue in the processed output (seconds).
        all_cue_pairs: All (DialogueCue, duration) pairs -- used to locate
            clean ambient windows. If None, falls back to simple pre-cue window.
        total_duration: Total mix duration (needed for post-cue fallback).

    Returns:
        Populated CueMeasurement dataclass.
    """
    measurement = CueMeasurement(
        audio_path=cue.audio_path,
        start_sec=cue.start_sec,
        duration_sec=cue_dur,
    )

    # Voice band: 300 Hz to 3400 Hz (ITU-T P.50 speech band)
    VOICE_LOW = 300.0
    VOICE_HIGH = 3400.0

    # Measure during cue window
    voice_lufs = _measure_segment_lufs(
        output_path,
        start_sec=cue.start_sec,
        duration_sec=cue_dur,
        low_hz=VOICE_LOW,
        high_hz=VOICE_HIGH,
    )

    # Locate best clean ambient window
    if all_cue_pairs is not None:
        ambient_start, ambient_dur = _find_clean_ambient_window(
            cue, all_cue_pairs, total_duration, desired_duration=AMBIENT_WINDOW_SEC
        )
    else:
        # Simple fallback: 2 seconds immediately before the cue
        ambient_start = max(0.0, cue.start_sec - AMBIENT_WINDOW_SEC)
        ambient_dur = cue.start_sec - ambient_start
        if ambient_dur < 0.25:
            ambient_start = cue.start_sec + cue_dur
            ambient_dur = AMBIENT_WINDOW_SEC

    ambient_lufs = _measure_segment_lufs(
        output_path,
        start_sec=ambient_start,
        duration_sec=ambient_dur,
        low_hz=VOICE_LOW,
        high_hz=VOICE_HIGH,
    )

    lift_db = voice_lufs - ambient_lufs
    passed = lift_db >= LIFT_THRESHOLD_DB

    measurement.voice_lufs = voice_lufs
    measurement.ambient_lufs = ambient_lufs
    measurement.lift_db = lift_db
    measurement.passed = passed

    return measurement


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def mix(
    song_path: str | Path,
    dialogue_cues: list[DialogueCue],
    output_path: str | Path,
    song_offset: float = 0.0,
    total_duration: float | None = None,
    song_structure: object | None = None,
    style_profile: object | None = None,
) -> MixResult:
    """Mix a song with dialogue cues into a broadcast-quality stereo audio file.

    This is the primary public entry point. Applies the full signal chain:

    1. Per-cue signal chain: HPF -> de-ess -> parametric EQ -> compressor ->
       makeup gain to -18 LUFS.
    2. Song prep: trim + loudnorm to -18 LUFS (quiet bed level).
    2.5. Per-section duck depth assignment: when song_structure is provided,
       compute_duck_envelope() from per_section_ducking.py updates each cue's
       duck_db to match the musical context (chorus vs. breakdown vs. drop etc).
       Falls back to the flat duck_db already set on each DialogueCue when no
       song_structure is supplied.
    3. Deterministic ducking: pre-computed volume-automation envelope on the
       song using nested if() expressions. 150ms linear fades at cue edges.
       No sidechain compression.
    4. High-shelf dip on the song during VO windows (reduces spectral
       competition with speech in the 3-5 kHz range).
    5. Master bus: extrastereo widener + alimiter targeting -1 dBTP.
    6. Verification: ebur128 measurement + per-cue voice-band lift check.
       Fails if any cue has < +6 dB lift over surrounding ambient.

    Args:
        song_path: Source song file (any format ffmpeg can decode).
        dialogue_cues: List of DialogueCue objects. Cues with missing files
            are skipped with a warning.
        output_path: Output file path. Parent directory is created if needed.
            Will be a 48 kHz stereo 16-bit PCM WAV.
        song_offset: Seconds into the song to start from (skip intro).
        total_duration: Desired output length in seconds. If None, uses the
            full song length after the offset.
        song_structure: Optional SongStructure from song_structure.py. When
            provided, duck depths are computed per section via
            per_section_ducking.compute_duck_envelope(). Without it, the
            duck_db on each DialogueCue is used as-is.
        style_profile: Optional StyleProfile from per_section_ducking.py.
            Allows callers to override per-section duck depths and shelf EQ.

    Returns:
        MixResult with success flag, LUFS/peak measurements, and per-cue
        intelligibility data.
    """
    _require_ffmpeg()

    song_path = Path(song_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = MixResult(success=False)

    # Validate song
    if not song_path.exists():
        result.errors.append(f"Song file not found: {song_path}")
        return result

    song_duration = _probe_duration(song_path)
    if song_duration <= 0:
        result.errors.append(f"Could not read duration from: {song_path}")
        return result

    available = song_duration - song_offset
    if total_duration is None:
        total_duration = available
    else:
        total_duration = min(total_duration, available)

    if total_duration <= 0:
        result.errors.append(
            f"Computed total_duration={total_duration:.2f}s is not positive. "
            f"Check song_offset={song_offset} vs song_duration={song_duration:.2f}"
        )
        return result

    # Validate cues
    valid_cues: list[tuple[DialogueCue, float]] = []
    for i, cue in enumerate(dialogue_cues):
        cue_path = Path(cue.audio_path)
        if not cue_path.exists():
            result.warnings.append(
                f"Cue {i} ({cue_path.name}) not found, skipping."
            )
            continue
        if cue.start_sec < 0:
            result.warnings.append(
                f"Cue {i} ({cue_path.name}) has negative start_sec, skipping."
            )
            continue
        if cue.start_sec >= total_duration:
            result.warnings.append(
                f"Cue {i} ({cue_path.name}) starts after mix end "
                f"({cue.start_sec:.2f}s > {total_duration:.2f}s), skipping."
            )
            continue
        dur = _probe_duration(cue_path)
        if dur <= 0:
            result.warnings.append(
                f"Cue {i} ({cue_path.name}) has zero duration, skipping."
            )
            continue
        valid_cues.append((cue, dur))

    logger.info(
        "mix: song=%s offset=%.1fs duration=%.1fs cues=%d valid",
        song_path.name, song_offset, total_duration, len(valid_cues),
    )

    with tempfile.TemporaryDirectory(prefix="fforge_audio_") as tmp:
        tmp_dir = Path(tmp)

        # Step 1: prepare song bed
        logger.info("Step 1/5: Preparing song bed (loudnorm to %s LUFS)", SONG_BED_LUFS)
        try:
            song_prepped = _prepare_song(
                song_path, song_offset, total_duration, tmp_dir
            )
        except subprocess.CalledProcessError as exc:
            result.errors.append("Song prep failed.")
            result.stderr = (exc.stderr or "")[-2000:]
            return result

        # Step 2: prepare each dialogue cue
        logger.info("Step 2/5: Processing %d dialogue cues", len(valid_cues))
        prepared_cues: list[tuple[DialogueCue, Path, float]] = []
        for i, (cue, _) in enumerate(valid_cues):
            try:
                processed_path, proc_dur = _prepare_cue(cue, tmp_dir, i)
                prepared_cues.append((cue, processed_path, proc_dur))
                logger.debug(
                    "  Cue %d: %s -> %.2fs", i, cue.audio_path.name, proc_dur
                )
            except subprocess.CalledProcessError as exc:
                result.warnings.append(
                    f"Cue {i} ({cue.audio_path.name}) processing failed, "
                    f"skipping. Error: {(exc.stderr or '')[-200:]}"
                )

        if not prepared_cues and dialogue_cues:
            result.warnings.append(
                "All cues failed processing. Output will be song-only."
            )

        # Step 2.5: apply per-section duck depths when song_structure is available.
        # Updates the duck_db on each prepared cue to match its musical context
        # (chorus vs. breakdown vs. drop, etc.) before the ducking envelope is built.
        if song_structure is not None and prepared_cues:
            logger.info(
                "Step 2.5/5: Computing per-section duck depths from song structure"
            )
            try:
                from tools.fandomforge.intelligence.per_section_ducking import (
                    compute_duck_envelope,
                    apply_duck_envelope_to_cues,
                )
                prepared_cue_objects = [c for c, _, _ in prepared_cues]
                duck_points = compute_duck_envelope(
                    prepared_cue_objects, song_structure, style_profile
                )
                updated_cue_objects = apply_duck_envelope_to_cues(
                    prepared_cue_objects, duck_points
                )
                prepared_cues = [
                    (updated_cue, proc_path, dur)
                    for updated_cue, (_, proc_path, dur)
                    in zip(updated_cue_objects, prepared_cues)
                ]
                from tools.fandomforge.intelligence.per_section_ducking import (
                    log_duck_schedule,
                )
                log_duck_schedule(duck_points)
            except Exception as exc:
                result.warnings.append(
                    f"Per-section duck computation failed, using flat duck_db values. "
                    f"Error: {exc}"
                )
                logger.warning(
                    "Per-section duck skipped: %s", exc, exc_info=True
                )

        # Step 3: build ducked mix
        logger.info("Step 3/5: Building ducked mix with deterministic envelope")
        premix_path = tmp_dir / "premix.wav"

        cue_pairs = [(c, d) for c, _, d in prepared_cues]

        try:
            _build_ducked_mix(
                song_prepped=song_prepped,
                prepared_cues=prepared_cues,
                cue_pairs=cue_pairs,
                total_duration=total_duration,
                premix_path=premix_path,
            )
        except subprocess.CalledProcessError as exc:
            result.errors.append("Ducked mix assembly failed.")
            result.stderr = (exc.stderr or "")[-2000:]
            return result

        # Step 4: master bus
        logger.info("Step 4/5: Applying master bus chain")
        try:
            _apply_master_chain(premix_path, output_path)
        except subprocess.CalledProcessError as exc:
            result.errors.append("Master bus processing failed.")
            result.stderr = (exc.stderr or "")[-2000:]
            return result

        # Step 5: measure and verify
        logger.info("Step 5/5: Measuring output and verifying cue lift")
        actual_lufs, peak_dbfs = _measure_lufs(output_path)

        cue_measurements: list[CueMeasurement] = []
        failed_cues: list[str] = []

        for cue, _, proc_dur in prepared_cues:
            measurement = _measure_cue_lift(
                output_path, cue, proc_dur,
                all_cue_pairs=cue_pairs,
                total_duration=total_duration,
            )
            cue_measurements.append(measurement)
            status = "PASS" if measurement.passed else "FAIL"
            logger.info(
                "  Cue %s: voice=%.1f LUFS | ambient=%.1f LUFS | "
                "lift=%.1f dB | %s",
                cue.audio_path.name,
                measurement.voice_lufs,
                measurement.ambient_lufs,
                measurement.lift_db,
                status,
            )
            if not measurement.passed:
                failed_cues.append(
                    f"{cue.audio_path.name}: lift={measurement.lift_db:.1f} dB "
                    f"(required >= {LIFT_THRESHOLD_DB:.0f} dB)"
                )

        all_passed = len(failed_cues) == 0

        if failed_cues:
            for msg in failed_cues:
                result.errors.append(f"Intelligibility FAIL: {msg}")

        result.success = all_passed
        result.output_path = output_path
        result.actual_lufs = actual_lufs
        result.peak_dbfs = peak_dbfs
        result.duration_sec = total_duration
        result.dialogue_count = len(prepared_cues)
        result.cue_measurements = cue_measurements

        logger.info(
            "Mix complete: LUFS=%.1f | Peak=%.1f dBTP | Cues=%d | %s",
            actual_lufs, peak_dbfs, len(prepared_cues),
            "PASS" if all_passed else "FAIL (intelligibility)",
        )

    return result


def _build_ducked_mix(
    song_prepped: Path,
    prepared_cues: list[tuple[DialogueCue, Path, float]],
    cue_pairs: list[tuple[DialogueCue, float]],
    total_duration: float,
    premix_path: Path,
) -> None:
    """Build the ducked mix from the prepared song and processed cue files.

    Assembles a single ffmpeg command that:
    - Loads the loudnorm'd song and all processed cue files.
    - Applies the deterministic duck envelope to the song.
    - Applies the high-shelf dip to the song during VO windows.
    - Delays each cue to its correct timeline position.
    - Mixes everything together with amix.

    Args:
        song_prepped: The loudnorm'd song temp file.
        prepared_cues: List of (cue, processed_path, cue_dur) tuples.
        cue_pairs: List of (cue, duration) for envelope building.
        total_duration: Output duration in seconds.
        premix_path: Output path for the premix.
    """
    cmd: list[str] = ["ffmpeg", "-y"]
    cmd += ["-i", str(song_prepped)]

    for _cue, proc_path, _dur in prepared_cues:
        cmd += ["-i", str(proc_path)]

    filter_parts: list[str] = []

    # Build duck envelope
    duck_expr = _build_duck_envelope(cue_pairs, total_duration)
    filter_parts.append(
        f"[0:a]volume='{duck_expr}':eval=frame[song_ducked]"
    )

    # Apply high-shelf dip on the song during VO windows via segment filter
    # For the main mix pipeline we use the simpler per-cue shelf approach:
    # apply equalizer only in cue windows by splitting/concatenating.
    # However with a large number of cues this becomes very long.
    # We use the shelf filter only if cue count is 8 or fewer.
    if len(cue_pairs) <= 8 and cue_pairs:
        shelf_hz = 3000.0
        dip_db = -3.0
        fade_sec = FADE_MS / 1000.0
        sorted_pairs = sorted(cue_pairs, key=lambda x: x[0].start_sec)

        # Build segment list with cursor tracking
        segments: list[tuple[float, float, bool]] = []
        cursor = 0.0
        for cue, dur in sorted_pairs:
            win_start = max(0.0, cue.start_sec - fade_sec)
            win_end = cue.start_sec + dur + fade_sec
            if cursor < win_start:
                segments.append((cursor, win_start, False))
            segments.append((win_start, min(win_end, total_duration), True))
            cursor = min(win_end, total_duration)
        if cursor < total_duration:
            segments.append((cursor, total_duration, False))

        # Filter only valid segments
        segments = [(s, e, d) for s, e, d in segments if (e - s) > 0.001]

        if segments:
            seg_labels: list[str] = []
            for i, (seg_start, seg_end, apply_dip) in enumerate(segments):
                lbl = f"[sh{i}]"
                seg_filter = (
                    f"[song_ducked]atrim=start={seg_start:.4f}:end={seg_end:.4f},"
                    "asetpts=PTS-STARTPTS"
                )
                if apply_dip:
                    seg_filter += (
                        f",equalizer=f={shelf_hz:.0f}:t=h:w=1:g={dip_db:.1f}"
                    )
                seg_filter += lbl
                filter_parts.append(seg_filter)
                seg_labels.append(lbl)

            n = len(seg_labels)
            concat_inputs = "".join(seg_labels)
            filter_parts.append(
                f"{concat_inputs}concat=n={n}:v=0:a=1[song_eq]"
            )
            song_out_label = "[song_eq]"
        else:
            song_out_label = "[song_ducked]"
    else:
        song_out_label = "[song_ducked]"

    # Position and pad each cue
    dialogue_labels: list[str] = []
    for idx, (cue, _proc_path, _proc_dur) in enumerate(prepared_cues, start=1):
        delay_ms = int(cue.start_sec * 1000)
        dlabel = f"[d{idx}]"
        filter_parts.append(
            f"[{idx}:a]adelay={delay_ms}|{delay_ms},"
            f"apad=pad_dur={total_duration:.3f}"
            f"{dlabel}"
        )
        dialogue_labels.append(dlabel)

    # Final mix
    if dialogue_labels:
        all_inputs = song_out_label + "".join(dialogue_labels)
        n_inputs = 1 + len(dialogue_labels)
        filter_parts.append(
            f"{all_inputs}amix=inputs={n_inputs}:duration=first:"
            f"dropout_transition=0:normalize=0,"
            f"atrim=end={total_duration:.3f}[premix]"
        )
    else:
        filter_parts.append(
            f"{song_out_label}atrim=end={total_duration:.3f}[premix]"
        )

    filter_complex = ";".join(filter_parts)

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[premix]",
        "-ar", str(SAMPLE_RATE), "-ac", "2",
        "-c:a", "pcm_s16le",
        str(premix_path),
    ]

    _run(cmd)
