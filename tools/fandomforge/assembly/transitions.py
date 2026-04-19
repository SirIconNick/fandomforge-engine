"""Smart transition library for FandomForge assembly pipeline.

Replaces the concat-only approach with context-aware transition effects.
Each transition is rendered as a short video segment via ffmpeg filter_complex
and inserted between clips in the final assembly timeline.

Transition types implemented:
    hard_cut        -- default, no effect, direct splice
    dissolve        -- cross-dissolve (0.2-0.5s), emotional/act boundaries
    flash_white     -- white flash for drop moments and impact reveals
    flash_black     -- black flash for dark reveals
    whip_pan        -- motion-blur slide for action cuts matching direction
    light_leak      -- warm orange flare overlay for emotional peaks
    glitch_rgb      -- RGB channel split + digital noise for era jumps
    match_cut       -- subject-position-aligned cut (requires motion flow data)
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Transition type enum
# ---------------------------------------------------------------------------

class TransitionType(str, Enum):
    """All supported transition types."""

    HARD_CUT = "hard_cut"
    DISSOLVE = "dissolve"
    FLASH_WHITE = "flash_white"
    FLASH_BLACK = "flash_black"
    WHIP_PAN = "whip_pan"
    LIGHT_LEAK = "light_leak"
    GLITCH_RGB = "glitch_rgb"
    MATCH_CUT = "match_cut"


# ---------------------------------------------------------------------------
# Shot context dataclass
# ---------------------------------------------------------------------------

@dataclass
class ShotContext:
    """Contextual metadata used to select the appropriate transition.

    Attributes:
        mood: Emotional tag from the shot entry (e.g. 'tense', 'calm', 'brutal').
        act: Act number (1-based) this shot belongs to.
        era: Source era tag (e.g. 'RE2R-1998', 'RE9-2025'). Used to detect
            era jumps that warrant a glitch transition.
        motion_direction: Dominant motion direction from optical flow analysis.
            One of 'left', 'right', 'up', 'down', 'static', 'mixed'.
        is_on_drop: True when this shot starts on a song drop/peak moment.
        is_act_boundary: True when a new act starts on the cut to this shot.
        is_flashback: True when this shot is tagged as a memory/flashback.
        character_speaks: True when the character has audible dialogue here.
        duration_sec: Shot hold time in seconds.
    """

    mood: str = ""
    act: int = 1
    era: str = ""
    motion_direction: str = "static"
    is_on_drop: bool = False
    is_act_boundary: bool = False
    is_flashback: bool = False
    character_speaks: bool = False
    duration_sec: float = 2.0


# ---------------------------------------------------------------------------
# Transition result
# ---------------------------------------------------------------------------

@dataclass
class TransitionResult:
    """Result of applying a single transition.

    Attributes:
        transition_type: The type that was applied.
        output_path: Path to the rendered transition video clip, or None
            for hard cuts (no file needed).
        duration_sec: Duration of the transition segment in seconds.
        success: False if rendering failed and a fallback was used.
        fallback_used: True when the requested effect failed and a hard cut
            was substituted.
        error: Error message if success is False.
    """

    transition_type: TransitionType
    output_path: Path | None
    duration_sec: float
    success: bool = True
    fallback_used: bool = False
    error: str = ""


# ---------------------------------------------------------------------------
# Selector logic
# ---------------------------------------------------------------------------

def select_transition(
    shot_a: ShotContext,
    shot_b: ShotContext,
) -> TransitionType:
    """Choose the best transition type given the context of two adjacent shots.

    Decision tree (in priority order):
        1. Drop moment on shot_b  ->  flash_white
        2. Act boundary + emotional shift  ->  dissolve
        3. Era jump (different non-empty eras)  ->  glitch_rgb
        4. Flashback entry/exit  ->  dissolve
        5. Action-to-action, matching motion direction  ->  match_cut or whip_pan
        6. Emotional peak (light_leak mood tag)  ->  light_leak
        7. Quiet moment to louder (calm -> tense/peak)  ->  dissolve
        8. Dark reveal  ->  flash_black
        9. Default  ->  hard_cut

    Args:
        shot_a: Context for the outgoing shot.
        shot_b: Context for the incoming shot.

    Returns:
        The recommended TransitionType.
    """
    # Rule 1: Drop moment
    if shot_b.is_on_drop:
        return TransitionType.FLASH_WHITE

    # Rule 2: Era jump — checked before act boundary because an era change is a
    # stronger visual signal and glitch is more specific than dissolve.
    era_a = (shot_a.era or "").strip()
    era_b = (shot_b.era or "").strip()
    if era_a and era_b and era_a != era_b:
        return TransitionType.GLITCH_RGB

    # Rule 3: Act boundary with mood shift (no era data available)
    if shot_b.is_act_boundary:
        if shot_a.mood != shot_b.mood:
            return TransitionType.DISSOLVE

    # Rule 4: Flashback entry or exit
    if shot_a.is_flashback != shot_b.is_flashback:
        return TransitionType.DISSOLVE

    # Rule 5: Action-to-action with matching motion direction
    action_moods = {"tense", "brutal", "chaotic", "fighting", "aiming", "shooting"}
    both_action = (
        any(t in shot_a.mood for t in action_moods)
        and any(t in shot_b.mood for t in action_moods)
    )
    if both_action:
        if (
            shot_a.motion_direction not in ("static", "mixed", "")
            and shot_a.motion_direction == shot_b.motion_direction
        ):
            return TransitionType.MATCH_CUT
        if shot_a.motion_direction not in ("static", "mixed", ""):
            return TransitionType.WHIP_PAN
        return TransitionType.HARD_CUT

    # Rule 6: Emotional peak (light_leak tagged shots)
    if "peak" in shot_a.mood or "peak" in shot_b.mood:
        return TransitionType.LIGHT_LEAK

    # Rule 7: Quiet to louder
    calm_moods = {"calm", "quiet", "still", "breather"}
    loud_moods = {"tense", "peak", "brutal", "chaotic", "fighting"}
    a_calm = any(t in shot_a.mood for t in calm_moods)
    b_loud = any(t in shot_b.mood for t in loud_moods)
    if a_calm and b_loud:
        return TransitionType.DISSOLVE

    # Rule 8: Dark reveal (moving into darkness/grim/dead/aftermath)
    dark_moods = {"grim", "dead", "wounded", "aftermath", "dark"}
    if any(t in shot_b.mood for t in dark_moods):
        return TransitionType.FLASH_BLACK

    # Default
    return TransitionType.HARD_CUT


# ---------------------------------------------------------------------------
# Effect renderers
# ---------------------------------------------------------------------------

def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _get_clip_duration(clip_path: Path) -> float | None:
    """Probe a clip for its duration in seconds via ffprobe."""
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(clip_path),
            ],
            capture_output=True, text=True, timeout=15,
        )
        return float(probe.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _render_dissolve(
    clip_a: Path,
    clip_b: Path,
    output_path: Path,
    *,
    duration_sec: float = 0.3,
    width: int = 1920,
    height: int = 1080,
    fps: int = 24,
) -> bool:
    """Cross-dissolve using xfade filter.

    Extracts a tail segment from clip_a and head segment from clip_b,
    then blends them with xfade=dissolve.
    """
    if not _have_ffmpeg():
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dur_a = _get_clip_duration(clip_a) or 2.0
    tail_start = max(0.0, dur_a - duration_sec)

    filter_complex = (
        f"[0:v]trim=start={tail_start:.4f}:duration={duration_sec:.4f},"
        f"setpts=PTS-STARTPTS,scale={width}:{height},fps={fps}[va];"
        f"[1:v]trim=start=0:duration={duration_sec:.4f},"
        f"setpts=PTS-STARTPTS,scale={width}:{height},fps={fps}[vb];"
        f"[va][vb]xfade=transition=dissolve:duration={duration_sec:.4f}:offset=0[out]"
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostats",
        "-i", str(clip_a),
        "-i", str(clip_b),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-an",
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output_path),
    ]
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, check=True, timeout=60,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _render_flash(
    clip_a: Path,
    clip_b: Path,
    output_path: Path,
    *,
    color: Literal["white", "black"] = "white",
    flash_duration_sec: float = 0.1,
    width: int = 1920,
    height: int = 1080,
    fps: int = 24,
) -> bool:
    """Insert a white or black flash frame between clip_a and clip_b.

    The flash is a short solid-color segment rendered at max brightness
    (white) or black, placed at the cut point.
    """
    if not _have_ffmpeg():
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)

    color_hex = "ffffff" if color == "white" else "000000"
    flash_frames = max(1, int(flash_duration_sec * fps))
    flash_duration = flash_frames / fps

    # Build flash clip in temp, then concat: tail_of_a + flash + head_of_b
    tmp_dir = Path(tempfile.mkdtemp(prefix="ff_flash_"))
    tail = tmp_dir / "tail.mp4"
    flash_clip = tmp_dir / "flash.mp4"
    head = tmp_dir / "head.mp4"
    concat_list = tmp_dir / "concat.txt"

    try:
        dur_a = _get_clip_duration(clip_a) or 2.0
        tail_dur = min(0.5, dur_a)
        tail_start = max(0.0, dur_a - tail_dur)

        # Extract tail of clip_a
        if not subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-nostats",
                "-ss", f"{tail_start:.4f}",
                "-i", str(clip_a),
                "-t", f"{tail_dur:.4f}",
                "-vf", f"scale={width}:{height},fps={fps}",
                "-c:v", "libx264", "-crf", "20", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-an",
                str(tail),
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, check=True, timeout=30,
        ).returncode == 0:
            return False

        # Generate flash frame
        if not subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi",
                "-i", f"color=c=0x{color_hex}:s={width}x{height}:r={fps}",
                "-t", f"{flash_duration:.4f}",
                "-c:v", "libx264", "-crf", "18", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-an",
                str(flash_clip),
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, check=True, timeout=10,
        ).returncode == 0:
            return False

        # Extract head of clip_b
        if not subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-nostats",
                "-ss", "0",
                "-i", str(clip_b),
                "-t", "0.5",
                "-vf", f"scale={width}:{height},fps={fps}",
                "-c:v", "libx264", "-crf", "20", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-an",
                str(head),
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, check=True, timeout=30,
        ).returncode == 0:
            return False

        with concat_list.open("w") as f:
            f.write(f"file '{tail.resolve()}'\n")
            f.write(f"file '{flash_clip.resolve()}'\n")
            f.write(f"file '{head.resolve()}'\n")

        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-nostats",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c", "copy", "-movflags", "+faststart",
                str(output_path),
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, check=True, timeout=30,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    finally:
        import shutil as _sh
        _sh.rmtree(tmp_dir, ignore_errors=True)


def _render_whip_pan(
    clip_a: Path,
    clip_b: Path,
    output_path: Path,
    *,
    direction: str = "right",
    duration_sec: float = 0.2,
    width: int = 1920,
    height: int = 1080,
    fps: int = 24,
) -> bool:
    """Whip-pan via motion-blurred xfade slideleft/right/up/down.

    Approximates a real whip pan: Gaussian blur on both sides of the cut
    combined with a directional slide xfade.
    """
    if not _have_ffmpeg():
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)

    xfade_map = {
        "right": "slideleft",
        "left": "slideright",
        "up": "slidedown",
        "down": "slideup",
    }
    xfade = xfade_map.get(direction.lower(), "slideleft")

    dur_a = _get_clip_duration(clip_a) or 2.0
    tail_start = max(0.0, dur_a - duration_sec)
    half = duration_sec / 2.0

    filter_complex = (
        f"[0:v]trim=start={tail_start:.4f}:duration={duration_sec:.4f},"
        f"setpts=PTS-STARTPTS,scale={width}:{height},fps={fps},"
        f"gblur=sigma=8:steps=2[va];"
        f"[1:v]trim=start=0:duration={duration_sec:.4f},"
        f"setpts=PTS-STARTPTS,scale={width}:{height},fps={fps},"
        f"gblur=sigma=8:steps=2[vb];"
        f"[va][vb]xfade=transition={xfade}:duration={half:.4f}:offset={half:.4f}[out]"
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostats",
        "-i", str(clip_a),
        "-i", str(clip_b),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-an",
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output_path),
    ]
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, check=True, timeout=60,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _render_light_leak(
    clip_a: Path,
    clip_b: Path,
    output_path: Path,
    *,
    duration_sec: float = 0.4,
    width: int = 1920,
    height: int = 1080,
    fps: int = 24,
) -> bool:
    """Warm orange/amber light leak overlay at the cut point.

    Blends the tail of clip_a and head of clip_b through a warm color
    overlay that peaks at the midpoint, simulating an analog light leak.
    Uses curves + xfade with a fade transition, plus an orange tint overlay.
    """
    if not _have_ffmpeg():
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dur_a = _get_clip_duration(clip_a) or 2.0
    tail_start = max(0.0, dur_a - duration_sec)

    # Orange warmth via curves: boost red, cut blue slightly
    warm_curve = "curves=r='0/0 1/1':b='0/0 0.8/0.6 1/0.85'"

    filter_complex = (
        f"[0:v]trim=start={tail_start:.4f}:duration={duration_sec:.4f},"
        f"setpts=PTS-STARTPTS,scale={width}:{height},fps={fps},{warm_curve}[va];"
        f"[1:v]trim=start=0:duration={duration_sec:.4f},"
        f"setpts=PTS-STARTPTS,scale={width}:{height},fps={fps}[vb];"
        f"[va][vb]xfade=transition=fade:duration={duration_sec:.4f}:offset=0[xf];"
        f"[xf]{warm_curve}[out]"
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostats",
        "-i", str(clip_a),
        "-i", str(clip_b),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-an",
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output_path),
    ]
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, check=True, timeout=60,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _render_glitch_rgb(
    clip_a: Path,
    clip_b: Path,
    output_path: Path,
    *,
    duration_sec: float = 0.25,
    width: int = 1920,
    height: int = 1080,
    fps: int = 24,
) -> bool:
    """RGB channel-split glitch effect at the cut point.

    Separates the tail of clip_a into R, G, B channels and applies
    horizontal offsets to simulate a CRT or digital glitch. Then cuts
    to clip_b. The effect is purely on the outgoing shot's tail frames.
    """
    if not _have_ffmpeg():
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dur_a = _get_clip_duration(clip_a) or 2.0
    tail_start = max(0.0, dur_a - duration_sec)

    # RGB split: shift red right +8px, blue left -8px, green stays
    # Uses split + rgbshift approximation via chromashift (if available)
    # or geq channel manipulation as fallback.
    # We use split into three planes and pad/crop for offset.
    glitch_filter = (
        f"[0:v]trim=start={tail_start:.4f}:duration={duration_sec:.4f},"
        f"setpts=PTS-STARTPTS,scale={width}:{height},fps={fps},"
        f"split=3[r0][g0][b0];"
        f"[r0]lutrgb=r='r':g=0:b=0,pad=iw+16:ih:8:0[rc];"
        f"[g0]lutrgb=r=0:g='g':b=0[gc];"
        f"[b0]lutrgb=r=0:g=0:b='b',pad=iw+16:ih:0:0[bc];"
        f"[rc][gc]blend=all_mode=addition[rg];"
        f"[rg][bc]blend=all_mode=addition,crop={width}:{height}[glitch];"
        f"[1:v]trim=start=0:duration=0.1,"
        f"setpts=PTS-STARTPTS,scale={width}:{height},fps={fps}[vb];"
        f"[glitch][vb]concat=n=2:v=1:a=0[out]"
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostats",
        "-i", str(clip_a),
        "-i", str(clip_b),
        "-filter_complex", glitch_filter,
        "-map", "[out]",
        "-an",
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output_path),
    ]
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, check=True, timeout=60,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _render_match_cut(
    clip_a: Path,
    clip_b: Path,
    output_path: Path,
    *,
    width: int = 1920,
    height: int = 1080,
    fps: int = 24,
) -> bool:
    """Match cut: a very short dissolve (2 frames) to smooth subject-aligned cuts.

    A true match cut aligns subject position between two shots. Without
    real motion-flow data at render time, we use a 2-frame cross-fade that
    removes the jarring snap while preserving the visual continuity effect.
    Full optical-flow-based alignment is handled upstream by motion_flow.py;
    this renderer handles the blend at the splice point.
    """
    return _render_dissolve(
        clip_a, clip_b, output_path,
        duration_sec=2.0 / fps,
        width=width, height=height, fps=fps,
    )


# ---------------------------------------------------------------------------
# Public API: apply_transitions
# ---------------------------------------------------------------------------

@dataclass
class AssembledWithTransitions:
    """Result of assembling clips with smart transitions applied.

    Attributes:
        success: True if final video was created.
        output_path: Path to the assembled video file.
        clips_assembled: Total clips that made it into the output.
        transitions_applied: Count of non-hard-cut transitions rendered.
        transition_failures: Number of transitions that fell back to hard cut.
        warnings: Non-fatal issues encountered during assembly.
        stderr: Last ffmpeg error string on failure.
    """

    success: bool
    output_path: Path | None
    clips_assembled: int = 0
    transitions_applied: int = 0
    transition_failures: int = 0
    warnings: list[str] = field(default_factory=list)
    stderr: str = ""


def apply_transitions(
    clips: list[Path],
    contexts: list[ShotContext],
    output_path: Path | str,
    *,
    work_dir: Path | str | None = None,
    width: int = 1920,
    height: int = 1080,
    fps: int = 24,
    overrides: dict[int, TransitionType] | None = None,
) -> AssembledWithTransitions:
    """Assemble a list of clips with smart transitions between them.

    For each adjacent pair, selects the appropriate transition (or uses
    an override if supplied), renders a transition segment, then concatenates
    everything via ffmpeg's concat demuxer.

    Hard cuts are handled by direct concatenation with no intermediate clip.
    All other transitions generate a short video segment placed between the
    two clips in the concat list.

    Args:
        clips: Ordered list of clip paths. Must all exist.
        contexts: One ShotContext per clip. Must have the same length as clips.
        output_path: Where to write the assembled video.
        work_dir: Scratch directory for transition segments. Defaults to a
            subdirectory beside output_path.
        width: Output width in pixels.
        height: Output height in pixels.
        fps: Output frame rate.
        overrides: Optional mapping of index -> TransitionType to force a
            specific transition between clips[index] and clips[index+1].

    Returns:
        AssembledWithTransitions with result details.
    """
    if not clips:
        return AssembledWithTransitions(
            success=False, output_path=None, stderr="No clips provided."
        )

    if len(clips) != len(contexts):
        return AssembledWithTransitions(
            success=False,
            output_path=None,
            stderr=f"clips ({len(clips)}) and contexts ({len(contexts)}) length mismatch.",
        )

    if not _have_ffmpeg():
        return AssembledWithTransitions(
            success=False, output_path=None, stderr="ffmpeg not found."
        )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    work = Path(work_dir) if work_dir else out.parent / ".transition-work"
    work.mkdir(parents=True, exist_ok=True)

    overrides = overrides or {}
    warnings: list[str] = []
    transitions_applied = 0
    transition_failures = 0

    # Build an ordered list of segments for the concat demuxer.
    # Each entry is a Path to a video file.
    segment_paths: list[Path] = []

    # Add first clip wholesale
    if not clips[0].exists():
        return AssembledWithTransitions(
            success=False, output_path=None, stderr=f"Clip not found: {clips[0]}"
        )
    segment_paths.append(clips[0])

    for i in range(len(clips) - 1):
        clip_a = clips[i]
        clip_b = clips[i + 1]

        if not clip_b.exists():
            warnings.append(f"Clip {i+1} not found, skipping: {clip_b}")
            continue

        t_type = overrides.get(i, select_transition(contexts[i], contexts[i + 1]))

        if t_type == TransitionType.HARD_CUT:
            # No transition clip needed; just append the next shot directly
            segment_paths.append(clip_b)
            continue

        # Render the transition segment
        trans_out = work / f"trans_{i:04d}_{t_type.value}.mp4"
        success = False

        if t_type == TransitionType.DISSOLVE:
            success = _render_dissolve(
                clip_a, clip_b, trans_out, width=width, height=height, fps=fps
            )
        elif t_type == TransitionType.FLASH_WHITE:
            success = _render_flash(
                clip_a, clip_b, trans_out, color="white", width=width, height=height, fps=fps
            )
        elif t_type == TransitionType.FLASH_BLACK:
            success = _render_flash(
                clip_a, clip_b, trans_out, color="black", width=width, height=height, fps=fps
            )
        elif t_type == TransitionType.WHIP_PAN:
            success = _render_whip_pan(
                clip_a, clip_b, trans_out,
                direction=contexts[i].motion_direction or "right",
                width=width, height=height, fps=fps,
            )
        elif t_type == TransitionType.LIGHT_LEAK:
            success = _render_light_leak(
                clip_a, clip_b, trans_out, width=width, height=height, fps=fps
            )
        elif t_type == TransitionType.GLITCH_RGB:
            success = _render_glitch_rgb(
                clip_a, clip_b, trans_out, width=width, height=height, fps=fps
            )
        elif t_type == TransitionType.MATCH_CUT:
            success = _render_match_cut(
                clip_a, clip_b, trans_out, width=width, height=height, fps=fps
            )

        if success and trans_out.exists() and trans_out.stat().st_size > 1000:
            # Replace the tail of clip_a and head of clip_b with the transition segment.
            # Pop the last appended full clip and replace it with a trimmed version
            # (minus the overlap frames), then the transition, then a trimmed clip_b head.
            # For simplicity in concat-demuxer mode, we treat the transition as an
            # additive segment inserted BETWEEN the two clips. The transition already
            # contains the tail/head overlap internally, so we splice it in directly.
            segment_paths.append(trans_out)
            transitions_applied += 1
        else:
            warnings.append(
                f"Transition {t_type.value} between shot {i} and {i+1} failed; "
                f"using hard cut."
            )
            transition_failures += 1

        # Always append the full next clip after the transition segment.
        # The transition segment already provides visual continuity at the junction;
        # the full clip provides the hold time.
        segment_paths.append(clip_b)

    if not segment_paths:
        return AssembledWithTransitions(
            success=False, output_path=None,
            stderr="No segments to assemble after transition rendering.",
        )

    # Write concat list and run final assembly
    concat_list = work / "final-concat.txt"
    with concat_list.open("w") as f:
        for p in segment_paths:
            f.write(f"file '{p.resolve()}'\n")

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostats",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        "-movflags", "+faststart",
        str(out),
    ]
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, check=True, timeout=900,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return AssembledWithTransitions(
            success=False, output_path=None,
            clips_assembled=len(clips),
            transitions_applied=transitions_applied,
            transition_failures=transition_failures,
            warnings=warnings,
            stderr=str(exc),
        )

    return AssembledWithTransitions(
        success=True,
        output_path=out,
        clips_assembled=len(clips),
        transitions_applied=transitions_applied,
        transition_failures=transition_failures,
        warnings=warnings,
    )
