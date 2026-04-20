"""Video assembly — stitch clips together on beat from a shot list."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from fandomforge.assembly.color_plan import ColorPlan
from fandomforge.assembly.color import ColorPreset, _PRESET_FILTERS
from fandomforge.assembly.parser import ShotEntry


@dataclass
class AssemblyResult:
    """Outcome of an assembly run."""

    success: bool
    output_path: Path | None
    clips_assembled: int = 0
    clips_skipped: int = 0
    skipped_reasons: list[str] = field(default_factory=list)
    duration_sec: float = 0.0
    warnings: list[str] = field(default_factory=list)
    stderr: str = ""


def _check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found. Install with: brew install ffmpeg")


def _find_source_video(raw_dir: Path, source_id: str) -> Path | None:
    """Find a downloaded video file for a source ID in raw/ (recursive).

    Searches:
      1. raw/<source_id>.*
      2. raw/**/<source_id>.*  (any subdirectory, e.g. raw/fights/)
      3. If source_id starts with 'fight_', strips the prefix and retries
         (supports the convention where fight-comp source_ids are tagged
         with 'fight_' but the actual files live under raw/fights/<stem>.mp4)
    """
    VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}

    def _find(stem: str) -> Path | None:
        # Try direct raw/<stem>.<ext> first
        for p in raw_dir.glob(f"{stem}.*"):
            if p.suffix.lower() in VIDEO_EXTS:
                return p
        # Then recursive search
        for p in raw_dir.rglob(f"{stem}.*"):
            if p.suffix.lower() in VIDEO_EXTS:
                return p
        return None

    hit = _find(source_id)
    if hit:
        return hit
    if source_id.startswith("fight_"):
        return _find(source_id[len("fight_"):])
    # Inverse mapping: source_id is the bare stem but the raw file has the
    # 'fight_' prefix (common when sources were symlinked from a project
    # whose filenames used the prefix but the scene data keys on the clean
    # stem). Try the fight_-prefixed variant.
    fight_hit = _find(f"fight_{source_id}")
    if fight_hit:
        return fight_hit
    return None


def _extract_clip(
    source_video: Path,
    output_path: Path,
    start_sec: float,
    duration_sec: float,
    target_width: int = 1920,
    target_height: int = 1080,
    target_fps: int = 24,
    color_preset: ColorPreset = ColorPreset.NONE,
) -> bool:
    """Extract a single clip with normalized resolution, frame rate, and optional color grade.

    Frame-accurate seek (-ss after -i) + re-encode. Applies per-source color filter
    during extraction so each clip gets its correct grade before concat.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    scale_filter = (
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
        f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={target_fps}"
    )

    color_filter = _PRESET_FILTERS.get(color_preset, "null")
    if color_filter and color_filter != "null":
        vf = f"{scale_filter},{color_filter}"
    else:
        vf = scale_filter

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostats",
        "-ss", f"{start_sec:.3f}",
        "-i", str(source_video),
        "-t", f"{duration_sec:.3f}",
        "-vf", vf,
        "-c:v", "libx264",
        "-crf", "20",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-an",
        "-movflags", "+faststart",
        str(output_path),
    ]
    # Use DEVNULL for stderr so the buffer can't fill and block ffmpeg
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            check=True,
            timeout=120,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _make_black_clip(
    output_path: Path,
    duration_sec: float,
    width: int = 1920,
    height: int = 1080,
    fps: int = 24,
) -> bool:
    """Create a black video clip of the given duration (for title cards, gaps)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostats",
        "-f", "lavfi",
        "-i", f"color=c=black:s={width}x{height}:r={fps}",
        "-t", f"{duration_sec:.3f}",
        "-c:v", "libx264",
        "-crf", "22",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-an",
        str(output_path),
    ]
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            check=True,
            timeout=60,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _concat_clips(
    clip_paths: list[Path],
    output_path: Path,
) -> bool:
    """Concatenate clips via ffmpeg's concat demuxer.

    Requires all clips to have matching codec / resolution / fps. Since
    _extract_clip normalizes output, this works reliably.
    """
    if not clip_paths:
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    concat_list = output_path.parent / "concat-list.txt"
    with concat_list.open("w") as f:
        for p in clip_paths:
            f.write(f"file '{p.resolve()}'\n")

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostats",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            check=True,
            timeout=600,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    finally:
        concat_list.unlink(missing_ok=True)


def assemble_rough_cut(
    shots: list[ShotEntry],
    raw_dir: Path | str,
    output_path: Path | str,
    *,
    width: int = 1920,
    height: int = 1080,
    fps: int = 24,
    work_dir: Path | str | None = None,
    on_progress: "callable | None" = None,
    color_plan: ColorPlan | None = None,
    parallel: int = 4,
) -> AssemblyResult:
    """Assemble a rough-cut video by extracting each shot in parallel and concatenating.

    Args:
        shots: Shot list (parsed from markdown)
        raw_dir: Where downloaded source videos live
        output_path: Where to write the assembled MP4
        width/height/fps: Output normalization
        work_dir: Scratch directory for intermediate clips
        on_progress: Optional callback fn(completed, total, shot_number)
        color_plan: Per-source color preset config
        parallel: Number of concurrent ffmpeg extractions (default 4)
    """
    _check_ffmpeg()

    raw_dir = Path(raw_dir)
    output_path = Path(output_path)
    work = Path(work_dir) if work_dir else output_path.parent / ".assembly-work"
    work.mkdir(parents=True, exist_ok=True)

    # Pair each shot with its target clip path so results preserve order
    tasks: list[tuple[int, ShotEntry, Path]] = []
    for idx, shot in enumerate(shots):
        clip_out = work / f"shot_{shot.number:03d}.mp4"
        tasks.append((idx, shot, clip_out))

    total_duration = 0.0

    def _process(task: tuple[int, ShotEntry, Path]) -> tuple[int, bool, str]:
        idx, shot, clip_out = task

        # Per-shot checkpoint: if a prior run already produced a valid clip at
        # this path, reuse it. This turns a crashed render into a resumable one
        # — re-running roughcut picks up where it failed instead of starting
        # from scratch. 10KB threshold matches the success check below.
        if clip_out.exists() and clip_out.stat().st_size > 10000:
            try:
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(clip_out)],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
                )
                if float(probe.stdout.decode().strip() or 0) > 0.1:
                    return (idx, True, "resumed")
            except (ValueError, AttributeError, subprocess.TimeoutExpired):
                pass
            # Stale / corrupt — re-extract below.

        if shot.is_placeholder():
            ok = _make_black_clip(clip_out, shot.duration_sec, width, height, fps)
            return (idx, ok, "placeholder" if ok else "placeholder-failed")

        source_video = _find_source_video(raw_dir, shot.source_id)
        if source_video is None:
            ok = _make_black_clip(clip_out, shot.duration_sec, width, height, fps)
            note = f"source-missing:{shot.source_id}"
            return (idx, ok, note)

        if shot.source_timestamp_sec is None:
            ok = _make_black_clip(clip_out, shot.duration_sec, width, height, fps)
            return (idx, ok, "unparseable-ts")

        preset = (
            color_plan.preset_for(shot.source_id, shot.act)
            if color_plan is not None
            else ColorPreset.NONE
        )
        ok = False
        for attempt in range(3):
            ok = _extract_clip(
                source_video, clip_out, shot.source_timestamp_sec, shot.duration_sec,
                width, height, fps, color_preset=preset,
            )
            if ok and clip_out.exists() and clip_out.stat().st_size > 10000:
                # Probe to confirm it decodes
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(clip_out)],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
                )
                try:
                    if float(probe.stdout.decode().strip() or 0) > 0.1:
                        return (idx, True, "ok" if attempt == 0 else f"ok-retry{attempt}")
                except (ValueError, AttributeError):
                    pass
            ok = False
        # Fallback to black so timeline stays intact
        black_ok = _make_black_clip(clip_out, shot.duration_sec, width, height, fps)
        return (idx, black_ok, f"extract-failed:{source_video.name}")

    # Parallel extraction — critical for speed with per-source color filters
    import concurrent.futures
    from threading import Lock
    results: dict[int, tuple[bool, str]] = {}
    skipped_reasons: list[str] = []
    warnings: list[str] = []
    lock = Lock()
    done = [0]

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        futures = {pool.submit(_process, t): t for t in tasks}
        for fut in concurrent.futures.as_completed(futures):
            task = futures[fut]
            shot = task[1]
            try:
                idx, ok, note = fut.result()
            except Exception as exc:
                idx, ok, note = task[0], False, f"exception:{exc}"
            results[idx] = (ok, note)
            with lock:
                done[0] += 1
                if on_progress:
                    on_progress(done[0], len(tasks), shot.number)
            if ok:
                total_duration += shot.duration_sec
                if note.startswith(("source-missing", "extract-failed")):
                    warnings.append(f"shot {shot.number} filled with black ({note})")
                elif note == "unparseable-ts":
                    warnings.append(f"shot {shot.number} filled with black (bad timestamp)")
            else:
                skipped_reasons.append(f"shot {shot.number}: {note}")

    # Collect clips in shot order
    clip_paths: list[Path] = []
    assembled_count = 0
    for idx, _shot, clip_out in tasks:
        res = results.get(idx)
        if res and res[0]:
            clip_paths.append(clip_out)
            assembled_count += 1

    if not clip_paths:
        return AssemblyResult(
            success=False,
            output_path=None,
            clips_assembled=0,
            clips_skipped=len(shots),
            skipped_reasons=skipped_reasons,
            warnings=warnings,
            stderr="No clips assembled.",
        )

    if not _concat_clips(clip_paths, output_path):
        return AssemblyResult(
            success=False,
            output_path=None,
            clips_assembled=assembled_count,
            clips_skipped=len(shots) - assembled_count,
            skipped_reasons=skipped_reasons,
            warnings=warnings,
            stderr="ffmpeg concat failed.",
        )

    return AssemblyResult(
        success=True,
        output_path=output_path,
        clips_assembled=assembled_count,
        clips_skipped=len(shots) - assembled_count,
        skipped_reasons=skipped_reasons,
        duration_sec=total_duration,
        warnings=warnings,
    )
