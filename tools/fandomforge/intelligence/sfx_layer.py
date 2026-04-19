"""SFX library generator and auto-placement engine.

Generates a small royalty-free SFX library using ffmpeg synthesis (no
external sound files required) and automatically places SFX on the edit
timeline based on shot and song structure context.

Whooshes are placed on hard cuts. Impact hits land on song drops. Heartbeat
pulses fill quiet tension shots tagged with tense/brooding emotion.

SFX are synthesized via ffmpeg filters:
- Whoosh: filtered noise sweep (high-pass, sweep down) at three lengths.
- Impact: sub-bass boom + filtered noise burst for body, modelled on the
  classic 'cinematic hit' motif.
- Gun cock / reload: narrow-band noise burst tuned to metallic frequencies.
- Heartbeat: two LFO-shaped bass thuds spaced ~200 ms apart.

Generated files are cached in output_dir; regenerated only when missing.

Auto-placement integrates with:
- shot_optimizer.py EditPlan for cut positions.
- song_structure.py SongStructure for drop moments.
- shot_library.py Shot emotion/action columns for heartbeat placement.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SFXCue:
    """A single SFX event on the mix timeline.

    Attributes:
        sfx_name: Key from the SFX library dict (e.g. 'whoosh_short').
        time_sec: Position in the final mix timeline in seconds.
        volume_db: Mix volume offset relative to the library file's own level.
            0.0 = use as-is. Negative = quieter.
        fade_in_ms: Fade-in duration in milliseconds.
        fade_out_ms: Fade-out duration in milliseconds.
        reason: Human-readable description of why this cue was placed
            (for debugging and edit notes).
    """

    sfx_name: str
    time_sec: float
    volume_db: float = -20.0
    fade_in_ms: float = 5.0
    fade_out_ms: float = 20.0
    reason: str = ""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SAMPLE_RATE: int = 48000

# Minimum gap between two SFX cues of the same type (seconds)
_MIN_WHOOSH_GAP_SEC: float = 0.8
_MIN_IMPACT_GAP_SEC: float = 2.0
_MIN_HEARTBEAT_GAP_SEC: float = 4.0

# Default mix volumes
_WHOOSH_DEFAULT_DB: float = -20.0
_IMPACT_DEFAULT_DB: float = -8.0
_HEARTBEAT_DEFAULT_DB: float = -18.0

# Shot emotion/action tags that trigger heartbeat placement
_HEARTBEAT_EMOTIONS: frozenset[str] = frozenset({"tense", "grim", "vulnerable", "quiet"})
_HEARTBEAT_ACTIONS: frozenset[str] = frozenset({"standing", "watching", "listening", "none"})


# ---------------------------------------------------------------------------
# ffmpeg helpers
# ---------------------------------------------------------------------------


def _require_ffmpeg() -> None:
    """Raise RuntimeError if ffmpeg is not on PATH."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH. Install via: brew install ffmpeg")


def _run_ffmpeg(cmd: list[str], label: str = "") -> None:
    """Run an ffmpeg command and raise RuntimeError on failure.

    Args:
        cmd: Full command list including 'ffmpeg'.
        label: Optional description for the error message.

    Raises:
        RuntimeError: If the process exits with non-zero status.
    """
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        tag = f" [{label}]" if label else ""
        raise RuntimeError(
            f"ffmpeg failed{tag}: {exc.stderr[-500:]}"
        ) from exc


# ---------------------------------------------------------------------------
# SFX synthesis
# ---------------------------------------------------------------------------


def _synthesize_whoosh_short(out_path: Path) -> None:
    """Generate a short whoosh (~180ms) using a filtered noise sweep.

    The sweep starts at 8 kHz and decays quickly, creating a fast cut whoosh.

    Args:
        out_path: Destination WAV path.
    """
    # anoisesrc: white noise | highpass sweeping from 6k->2k via afade out | volume envelope
    filter_chain = (
        "anoisesrc=r={sr}:color=white:d=0.20,"
        "highpass=f=6000:poles=2,"
        "lowpass=f=12000:poles=1,"
        "afade=t=in:st=0:d=0.01,"
        "afade=t=out:st=0.13:d=0.07,"
        "volume=0.35"
    ).format(sr=_SAMPLE_RATE)
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", filter_chain,
        "-ar", str(_SAMPLE_RATE), "-ac", "1",
        "-c:a", "pcm_s16le", str(out_path),
    ]
    _run_ffmpeg(cmd, "whoosh_short")


def _synthesize_whoosh_medium(out_path: Path) -> None:
    """Generate a medium whoosh (~320ms) using a filtered noise sweep.

    Slightly lower starting frequency than the short version for a beefier feel.

    Args:
        out_path: Destination WAV path.
    """
    filter_chain = (
        "anoisesrc=r={sr}:color=pink:d=0.36,"
        "highpass=f=3500:poles=2,"
        "lowpass=f=10000:poles=1,"
        "afade=t=in:st=0:d=0.02,"
        "afade=t=out:st=0.26:d=0.10,"
        "volume=0.30"
    ).format(sr=_SAMPLE_RATE)
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", filter_chain,
        "-ar", str(_SAMPLE_RATE), "-ac", "1",
        "-c:a", "pcm_s16le", str(out_path),
    ]
    _run_ffmpeg(cmd, "whoosh_medium")


def _synthesize_whoosh_long(out_path: Path) -> None:
    """Generate a long whoosh (~550ms) suitable for slow-motion transitions.

    Args:
        out_path: Destination WAV path.
    """
    filter_chain = (
        "anoisesrc=r={sr}:color=pink:d=0.60,"
        "highpass=f=2000:poles=2,"
        "lowpass=f=9000:poles=2,"
        "afade=t=in:st=0:d=0.04,"
        "afade=t=out:st=0.42:d=0.18,"
        "volume=0.25"
    ).format(sr=_SAMPLE_RATE)
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", filter_chain,
        "-ar", str(_SAMPLE_RATE), "-ac", "1",
        "-c:a", "pcm_s16le", str(out_path),
    ]
    _run_ffmpeg(cmd, "whoosh_long")


def _synthesize_impact_boom(out_path: Path) -> None:
    """Generate a low sub-bass impact boom (~600ms).

    Combines a 50 Hz sine tone with a very short noise burst for transient attack.

    Args:
        out_path: Destination WAV path.
    """
    tmp = out_path.parent / "_tmp_boom_sine.wav"
    tmp_noise = out_path.parent / "_tmp_boom_noise.wav"

    # Sub-bass sine component
    cmd_sine = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", (
            "sine=frequency=50:sample_rate={sr}:duration=0.65"
            ",afade=t=out:st=0.30:d=0.35"
            ",volume=0.80"
        ).format(sr=_SAMPLE_RATE),
        "-ar", str(_SAMPLE_RATE), "-ac", "1",
        "-c:a", "pcm_s16le", str(tmp),
    ]
    _run_ffmpeg(cmd_sine, "boom_sine")

    # Transient noise burst (the 'crack' on the front of the hit)
    cmd_noise = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", (
            "anoisesrc=r={sr}:color=brown:d=0.65"
            ",bandpass=f=200:width_type=h:w=300"
            ",afade=t=in:st=0:d=0.002"
            ",afade=t=out:st=0.06:d=0.59"
            ",volume=0.55"
        ).format(sr=_SAMPLE_RATE),
        "-ar", str(_SAMPLE_RATE), "-ac", "1",
        "-c:a", "pcm_s16le", str(tmp_noise),
    ]
    _run_ffmpeg(cmd_noise, "boom_noise")

    # Mix sine + noise
    cmd_mix = [
        "ffmpeg", "-y",
        "-i", str(tmp), "-i", str(tmp_noise),
        "-filter_complex", "amix=inputs=2:duration=longest:normalize=0,volume=1.0",
        "-ar", str(_SAMPLE_RATE), "-ac", "1",
        "-c:a", "pcm_s16le", str(out_path),
    ]
    _run_ffmpeg(cmd_mix, "boom_mix")

    # Cleanup intermediates
    for p in (tmp, tmp_noise):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def _synthesize_impact_snare_crack(out_path: Path) -> None:
    """Generate a snare-crack cinematic hit (~250ms).

    Tuned pink noise burst centered around 1.5-4 kHz for a tight snappy hit.

    Args:
        out_path: Destination WAV path.
    """
    filter_chain = (
        "anoisesrc=r={sr}:color=pink:d=0.28,"
        "bandpass=f=2500:width_type=h:w=3000,"
        "afade=t=in:st=0:d=0.001,"
        "afade=t=out:st=0.04:d=0.24,"
        "volume=0.70"
    ).format(sr=_SAMPLE_RATE)
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", filter_chain,
        "-ar", str(_SAMPLE_RATE), "-ac", "1",
        "-c:a", "pcm_s16le", str(out_path),
    ]
    _run_ffmpeg(cmd, "snare_crack")


def _synthesize_impact_bass_drop(out_path: Path) -> None:
    """Generate a bass drop impact with a pitch-falling sine (~800ms).

    Simulates the 'dun DUN' bass drop moment from cinematic trailers.

    Args:
        out_path: Destination WAV path.
    """
    # Pitch-sweep: use afreqshift approximation via a modulated sine
    # ffmpeg does not have a native pitch-sweep filter, so we combine a low
    # constant sine with an envelope to give the feel of a drop.
    filter_chain = (
        "sine=frequency=65:sample_rate={sr}:duration=0.85"
        ",afade=t=in:st=0:d=0.01"
        ",afade=t=out:st=0.50:d=0.35"
        ",volume=0.90"
    ).format(sr=_SAMPLE_RATE)
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", filter_chain,
        "-ar", str(_SAMPLE_RATE), "-ac", "1",
        "-c:a", "pcm_s16le", str(out_path),
    ]
    _run_ffmpeg(cmd, "bass_drop")


def _synthesize_gun_cock(out_path: Path) -> None:
    """Generate a gun cock / reload click (~120ms).

    A short metallic-band noise burst mimicking a slide rack or bolt action.

    Args:
        out_path: Destination WAV path.
    """
    filter_chain = (
        "anoisesrc=r={sr}:color=white:d=0.14,"
        "bandpass=f=5000:width_type=h:w=4000,"
        "afade=t=in:st=0:d=0.001,"
        "afade=t=out:st=0.04:d=0.10,"
        "volume=0.60"
    ).format(sr=_SAMPLE_RATE)
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", filter_chain,
        "-ar", str(_SAMPLE_RATE), "-ac", "1",
        "-c:a", "pcm_s16le", str(out_path),
    ]
    _run_ffmpeg(cmd, "gun_cock")


def _synthesize_heartbeat(out_path: Path) -> None:
    """Generate a two-thud heartbeat pulse (~650ms total).

    Two short 60 Hz sine bursts spaced ~220ms apart, each with a fast decay.

    Args:
        out_path: Destination WAV path.
    """
    # First thud
    tmp1 = out_path.parent / "_hb_thud1.wav"
    tmp2 = out_path.parent / "_hb_thud2.wav"

    thud_filter = (
        "sine=frequency=60:sample_rate={sr}:duration=0.18"
        ",afade=t=in:st=0:d=0.003"
        ",afade=t=out:st=0.06:d=0.12"
        ",volume=0.75"
    ).format(sr=_SAMPLE_RATE)

    for tmp in (tmp1, tmp2):
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", thud_filter,
            "-ar", str(_SAMPLE_RATE), "-ac", "1",
            "-c:a", "pcm_s16le", str(tmp),
        ]
        _run_ffmpeg(cmd, f"hb_{tmp.stem}")

    # Second thud delayed by 220ms then concat
    delay_ms = 220
    cmd_combine = [
        "ffmpeg", "-y",
        "-i", str(tmp1), "-i", str(tmp2),
        "-filter_complex", (
            f"[1:a]adelay={delay_ms}|{delay_ms}[d2];"
            "[0:a][d2]amix=inputs=2:duration=longest:normalize=0"
        ),
        "-ar", str(_SAMPLE_RATE), "-ac", "1",
        "-c:a", "pcm_s16le", str(out_path),
    ]
    _run_ffmpeg(cmd_combine, "heartbeat_combine")

    for p in (tmp1, tmp2):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_sfx_library(output_dir: str | Path) -> dict[str, Path]:
    """Generate (or verify) the complete SFX library and return a name->path map.

    All files are synthesized via ffmpeg. If a file already exists at its
    expected path it is reused without regeneration.

    Args:
        output_dir: Directory where WAV files will be written.

    Returns:
        Dict mapping SFX name to absolute Path. Keys:
          - 'whoosh_short'       : ~180ms fast cut whoosh
          - 'whoosh_medium'      : ~320ms medium transition whoosh
          - 'whoosh_long'        : ~550ms slow-motion whoosh
          - 'impact_boom'        : sub-bass low boom
          - 'impact_snare_crack' : tight snare crack
          - 'impact_bass_drop'   : bass drop pitch fall
          - 'gun_cock'           : metallic click/rack
          - 'heartbeat'          : two-thud pulse

    Raises:
        RuntimeError: If ffmpeg is not available or synthesis fails.
    """
    _require_ffmpeg()
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    synthesis_map: dict[str, tuple[Path, callable]] = {
        "whoosh_short":       (out / "sfx_whoosh_short.wav",       _synthesize_whoosh_short),
        "whoosh_medium":      (out / "sfx_whoosh_medium.wav",      _synthesize_whoosh_medium),
        "whoosh_long":        (out / "sfx_whoosh_long.wav",        _synthesize_whoosh_long),
        "impact_boom":        (out / "sfx_impact_boom.wav",        _synthesize_impact_boom),
        "impact_snare_crack": (out / "sfx_impact_snare_crack.wav", _synthesize_impact_snare_crack),
        "impact_bass_drop":   (out / "sfx_impact_bass_drop.wav",   _synthesize_impact_bass_drop),
        "gun_cock":           (out / "sfx_gun_cock.wav",           _synthesize_gun_cock),
        "heartbeat":          (out / "sfx_heartbeat.wav",          _synthesize_heartbeat),
    }

    library: dict[str, Path] = {}
    for name, (path, fn) in synthesis_map.items():
        if path.exists():
            logger.debug("SFX cached: %s", path.name)
        else:
            logger.info("Generating SFX: %s -> %s", name, path.name)
            try:
                fn(path)
            except RuntimeError as exc:
                logger.error("Failed to generate %s: %s", name, exc)
                continue

        if path.exists():
            library[name] = path
        else:
            logger.warning("SFX file missing after synthesis: %s", path)

    logger.info(
        "SFX library ready: %d/%d files", len(library), len(synthesis_map)
    )
    return library


def auto_place_sfx(
    edit_plan: object,
    song_structure: object,
    sfx_library: Optional[dict[str, Path]] = None,
    output_dir: Optional[str | Path] = None,
) -> list[SFXCue]:
    """Auto-place SFX cues based on the edit plan and song structure.

    Placement rules:
    - whoosh_short or whoosh_medium on every hard cut detected from the edit
      plan shot list (cues placed 40ms before the cut, volume -20 dB).
    - impact_boom or impact_bass_drop at every drop moment in the song
      structure (volume -8 dB, placed at the exact drop timestamp).
    - heartbeat at shots tagged with tense/brooding emotion that do not fall
      within 2s of a drop or impact (volume -18 dB, placed at shot start).

    Args:
        edit_plan: EditPlan object from shot_optimizer.py. Expected to have
            a .shots attribute (list of ShotRecord with .start_sec and
            .emotion / .shot fields). Loosely typed for forward compatibility.
        song_structure: SongStructure from song_structure.py with
            .drop_moments and .sections attributes.
        sfx_library: Dict from generate_sfx_library(). When None, the
            function generates a library in output_dir.
        output_dir: Directory used when sfx_library is None and generation
            is needed.

    Returns:
        List of SFXCue objects sorted by time_sec. Empty list if no placements
        qualify or if the edit_plan has no shots.

    Raises:
        ValueError: If neither sfx_library nor output_dir is provided.
    """
    if sfx_library is None:
        if output_dir is None:
            raise ValueError(
                "Either sfx_library or output_dir must be provided to auto_place_sfx."
            )
        sfx_library = generate_sfx_library(output_dir)

    shots = list(getattr(edit_plan, "shots", []) or [])
    drop_moments: list[float] = list(getattr(song_structure, "drop_moments", []) or [])
    sections = list(getattr(song_structure, "sections", []) or [])

    cues: list[SFXCue] = []

    # Track last-placed times per SFX type to enforce minimum gaps
    last_whoosh_time: float = -999.0
    last_impact_time: float = -999.0
    last_heartbeat_time: float = -999.0

    # Helper: check if a time is within window of any drop
    def near_drop(t: float, window: float = 2.0) -> bool:
        return any(abs(t - d) <= window for d in drop_moments)

    # Helper: find section mood at time
    def section_mood_at(t: float) -> str:
        for sec in sections:
            if sec.start_time <= t < sec.end_time:
                return str(getattr(sec, "mood", "unknown"))
        return "unknown"

    # -------------------------------------------------------------------------
    # Whoosh on every cut (between consecutive shots)
    # -------------------------------------------------------------------------
    for i in range(1, len(shots)):
        cut_time = float(getattr(shots[i], "start_sec", 0.0))
        # Place whoosh slightly before the cut
        whoosh_time = max(0.0, cut_time - 0.04)

        if (whoosh_time - last_whoosh_time) < _MIN_WHOOSH_GAP_SEC:
            continue

        # Choose whoosh variant based on shot duration (longer shots get longer whoosh)
        prev_shot_dur = float(
            getattr(shots[i - 1], "duration_sec", None)
            or (cut_time - float(getattr(shots[i - 1], "start_sec", 0.0)))
        )
        if prev_shot_dur < 1.0:
            sfx_key = "whoosh_short"
        elif prev_shot_dur < 2.5:
            sfx_key = "whoosh_medium"
        else:
            sfx_key = "whoosh_long"

        if sfx_key not in sfx_library:
            sfx_key = "whoosh_short"

        cues.append(SFXCue(
            sfx_name=sfx_key,
            time_sec=whoosh_time,
            volume_db=_WHOOSH_DEFAULT_DB,
            fade_in_ms=3.0,
            fade_out_ms=15.0,
            reason=f"hard cut at {cut_time:.2f}s",
        ))
        last_whoosh_time = whoosh_time
        logger.debug("Whoosh [%s] placed at %.2fs (cut)", sfx_key, whoosh_time)

    # -------------------------------------------------------------------------
    # Impact hit on drop moments
    # -------------------------------------------------------------------------
    for drop_time in drop_moments:
        if (drop_time - last_impact_time) < _MIN_IMPACT_GAP_SEC:
            continue

        mood = section_mood_at(drop_time)
        if mood == "peak":
            sfx_key = "impact_bass_drop"
        else:
            sfx_key = "impact_boom"

        if sfx_key not in sfx_library:
            sfx_key = next(
                (k for k in ("impact_boom", "impact_bass_drop") if k in sfx_library),
                None,
            )
            if sfx_key is None:
                continue

        cues.append(SFXCue(
            sfx_name=sfx_key,
            time_sec=drop_time,
            volume_db=_IMPACT_DEFAULT_DB,
            fade_in_ms=0.0,
            fade_out_ms=50.0,
            reason=f"song drop at {drop_time:.2f}s (mood={mood})",
        ))
        last_impact_time = drop_time
        logger.debug("Impact [%s] placed at %.2fs (drop)", sfx_key, drop_time)

    # -------------------------------------------------------------------------
    # Heartbeat during quiet/tense brooding shots
    # -------------------------------------------------------------------------
    heartbeat_key = "heartbeat"
    if heartbeat_key not in sfx_library:
        logger.debug("Heartbeat SFX not in library, skipping heartbeat placement.")
        heartbeat_key = None  # type: ignore[assignment]

    if heartbeat_key:
        for shot in shots:
            shot_time = float(getattr(shot, "start_sec", 0.0))
            emotion = str(getattr(shot, "emotion", "") or "").lower()
            action = str(getattr(shot, "action", "") or "").lower()

            is_tense_shot = (
                emotion in _HEARTBEAT_EMOTIONS
                or action in _HEARTBEAT_ACTIONS
                or "brooding" in (getattr(shot, "desc", "") or "").lower()
            )

            if not is_tense_shot:
                continue
            if near_drop(shot_time, window=2.0):
                continue
            if (shot_time - last_heartbeat_time) < _MIN_HEARTBEAT_GAP_SEC:
                continue

            cues.append(SFXCue(
                sfx_name=heartbeat_key,
                time_sec=shot_time,
                volume_db=_HEARTBEAT_DEFAULT_DB,
                fade_in_ms=10.0,
                fade_out_ms=30.0,
                reason=f"tense shot (emotion={emotion}, action={action})",
            ))
            last_heartbeat_time = shot_time
            logger.debug(
                "Heartbeat placed at %.2fs (emotion=%s action=%s)",
                shot_time, emotion, action,
            )

    cues.sort(key=lambda c: c.time_sec)

    logger.info(
        "auto_place_sfx: %d cues total  "
        "(whooshes=%d  impacts=%d  heartbeats=%d)",
        len(cues),
        sum(1 for c in cues if c.sfx_name.startswith("whoosh")),
        sum(1 for c in cues if c.sfx_name.startswith("impact")),
        sum(1 for c in cues if c.sfx_name == "heartbeat"),
    )
    return cues
