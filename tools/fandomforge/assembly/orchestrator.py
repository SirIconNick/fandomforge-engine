"""Rough-cut orchestrator — runs parse → director review → assemble → transitions →
title overlays → mix → color → final merge.

Pipeline (updated):
    1. Parse shot list
    2. Director holistic review (GPT-4o, optional — skipped if no API key)
    3. Load optional per-source color plan
    4. Assemble video with per-source grades applied inline
    5. Apply smart transitions between clips
    6. Burn title/text overlays via drawtext
    7. If no per-source plan: apply master color preset
    8. Mix audio: song + dialogue cues with automatic ducking
    9. Mux video + audio -> exports/<output_name>
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fandomforge.assembly.assemble import AssemblyResult, assemble_rough_cut
from fandomforge.assembly.color import ColorPreset, apply_base_grade
from fandomforge.assembly.color_plan import ColorPlan, load_color_plan
from fandomforge.assembly.dialogue import load_dialogue_json
from fandomforge.assembly.mixer import DialogueCue, MixResult, SfxCue, mix_audio
from fandomforge.assembly.parser import parse_shot_list
from fandomforge.assembly.title_overlay import (
    OverlayPlan,
    build_overlay_layer,
    build_overlay_plan_from_edit_plan,
)
from fandomforge.assembly.transitions import (
    AssembledWithTransitions,
    ShotContext,
    TransitionType,
    apply_transitions,
)


@dataclass
class RoughCutResult:
    success: bool
    output_path: Path | None
    assembly: AssemblyResult | None = None
    mix: MixResult | None = None
    transitions: AssembledWithTransitions | None = None
    director_review: "Any | None" = None
    duration_sec: float = 0.0
    warnings: list[str] = field(default_factory=list)
    stderr: str = ""


def _load_dialogue_cues(
    dialogue_script_json: Path | None,
    dialogue_dir: Path,
) -> tuple[list[DialogueCue], list[str]]:
    """Load dialogue cues from a JSON dialogue script.

    Returns (cues, warnings). Cues with missing audio WAVs are skipped and
    reported in the warnings list.
    """
    if dialogue_script_json is None or not dialogue_script_json.exists():
        return [], []

    entries = load_dialogue_json(dialogue_script_json)
    cues: list[DialogueCue] = []
    warnings: list[str] = []
    for entry in entries:
        audio_path = dialogue_dir / entry.audio_filename
        if not audio_path.exists():
            warnings.append(
                f"Dialogue cue skipped — missing audio: {entry.audio_filename} "
                f"({entry.character}: \"{entry.line[:40]}\")"
            )
            continue
        cues.append(
            DialogueCue(
                audio_path=audio_path,
                start_sec=entry.start_sec,
                gain_db=entry.gain_db,
                duck_db=entry.duck_db,
            )
        )
    return cues, warnings


def _merge_video_and_audio(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "320k",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=900)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def build_rough_cut(
    project_dir: Path | str,
    *,
    shot_list_name: str = "shot-list.md",
    song_filename: str | None = None,
    dialogue_script_json: str | None = None,
    color_preset: ColorPreset = ColorPreset.TACTICAL,
    color_plan_json: str | None = None,
    edit_plan_json: str | None = None,
    overlay_editor_name: str = "FandomForge",
    enable_director_review: bool = True,
    enable_transitions: bool = True,
    enable_title_overlays: bool = True,
    output_name: str = "rough-cut.mp4",
    target_width: int = 1920,
    target_height: int = 1080,
    target_fps: int = 24,
    song_start_offset_sec: float = 0.0,
    song_gain_db: float = -4.0,
    keep_work_dir: bool = False,
    sfx_plan_json: str | None = None,
) -> RoughCutResult:
    """End-to-end rough cut build.

    Pipeline:
        1. Parse shot list
        2. Director holistic review (GPT-4o) — optional, requires OPENAI_API_KEY
        3. Load optional per-source color plan
        4. Assemble video (with per-source grades applied inline)
        5. Apply smart transitions between assembled clips
        6. Burn title/text overlays (chapter titles, intro cards, credits)
        7. If no per-source plan: apply master color preset
        8. Mix audio: song + dialogue cues with automatic ducking
        9. Mux video + audio -> exports/<output_name>

    Args:
        project_dir: Root directory of the FandomForge project.
        shot_list_name: Filename of the shot list markdown (searched in root,
            plans/, and demos/).
        song_filename: Audio file name in raw/ to use as the backing track.
        dialogue_script_json: JSON dialogue script filename for VO cues.
        color_preset: Master color grade preset applied when no per-source
            color plan is provided.
        color_plan_json: Per-source color plan JSON filename.
        edit_plan_json: Path or filename of the edit plan JSON (produced by
            director.propose_edit). Used to build title overlays from act names,
            character intros, dialogue placements, and end credits. Searched in
            root, data/, and exports/.
        overlay_editor_name: Editor handle displayed in the end credits.
        enable_director_review: If True and OPENAI_API_KEY is set, runs the
            GPT-4o holistic review before assembly. The review result is stored
            in RoughCutResult.director_review but does NOT block assembly.
        enable_transitions: If True, applies smart transition effects between
            clips using the assembly/transitions module.
        enable_title_overlays: If True, burns text overlays from the edit plan
            into the video via drawtext.
        output_name: Final output filename in exports/.
        target_width: Output pixel width.
        target_height: Output pixel height.
        target_fps: Output frame rate.
        song_start_offset_sec: Where in the song to start mixing.
        song_gain_db: Gain applied to the song before mixing with dialogue.
        keep_work_dir: If True, `.assembly-work/` stays on disk after a
            successful render (useful for debugging). Default False cleans it.
            On failure, the work dir is always preserved so you can inspect
            what went wrong.
        sfx_plan_json: Optional sfx-plan.json filename. When provided (or when
            data/sfx-plan.json exists), action SFX are layered into the mix
            and source-clip scene audio is blended under the song per the
            plan's scene_audio_blend settings.
    """
    project_dir = Path(project_dir)
    raw_dir = project_dir / "raw"
    dialogue_dir = project_dir / "dialogue"
    exports_dir = project_dir / "exports"
    work_dir = project_dir / ".assembly-work"
    exports_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    director_review_result: Any | None = None
    transitions_result: AssembledWithTransitions | None = None

    # Resolve shot list path — check (in order): root, plans/, demos/.
    candidate_paths = [
        project_dir / shot_list_name,
        project_dir / "plans" / shot_list_name,
        project_dir / "demos" / shot_list_name,
    ]
    shot_list = next((p for p in candidate_paths if p.exists()), candidate_paths[0])
    if not shot_list.exists():
        return RoughCutResult(
            success=False,
            output_path=None,
            stderr=(
                f"Shot list not found. Looked in: "
                f"{', '.join(str(p.relative_to(project_dir)) for p in candidate_paths)}"
            ),
        )

    # 1. Parse
    shots = parse_shot_list(shot_list)
    if not shots:
        return RoughCutResult(
            success=False,
            output_path=None,
            stderr=f"No shots parsed from {shot_list}. Check table format.",
        )

    # Load edit plan JSON if available (used for director review + title overlays)
    edit_plan_dict: dict[str, Any] = {}
    if edit_plan_json:
        ep_candidates = [
            Path(edit_plan_json) if Path(edit_plan_json).is_absolute() else None,
            project_dir / edit_plan_json,
            project_dir / "data" / edit_plan_json,
            project_dir / "exports" / edit_plan_json,
            project_dir / "plans" / edit_plan_json,
        ]
        for ep_path in ep_candidates:
            if ep_path and ep_path.exists():
                try:
                    edit_plan_dict = json.loads(ep_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as exc:
                    warnings.append(f"Could not load edit plan JSON ({ep_path}): {exc}")
                break
        else:
            warnings.append(f"edit_plan_json not found in project: {edit_plan_json}")

    # If no edit_plan dict yet, synthesize a minimal one from parsed shots
    if not edit_plan_dict:
        edit_plan_dict = {
            "shots": [
                {
                    "number": s.number,
                    "hero": s.hero,
                    "description": s.description,
                    "era": "",
                    "mood": s.mood,
                    "song_time_sec": s.song_time_sec,
                    "duration_sec": s.duration_sec,
                    "slot_name": f"act_{s.act}",
                    "character_speaks": False,
                }
                for s in shots
            ]
        }

    # 2. Director holistic review (non-blocking)
    if enable_director_review:
        import os
        from fandomforge.intelligence.openai_helper import _load_env as _le
        _le(project_dir)
        if os.environ.get("OPENAI_API_KEY"):
            try:
                from fandomforge.intelligence.director import review_edit_plan
                review_output = work_dir / "director-review.json"
                director_review_result = review_edit_plan(
                    edit_plan_dict,
                    output_json=review_output,
                    project_root=project_dir,
                )
                if not director_review_result.success:
                    warnings.append(
                        f"Director review failed (non-fatal): {director_review_result.error}"
                    )
                elif director_review_result.specific_revision_suggestions:
                    top = director_review_result.specific_revision_suggestions[0]
                    warnings.append(
                        f"Director review top suggestion (priority 1): {top.description}"
                    )
            except Exception as exc:
                warnings.append(f"Director review raised exception (non-fatal): {exc}")
        else:
            warnings.append(
                "Director review skipped: OPENAI_API_KEY not set. "
                "Set it in .env to enable GPT-4o holistic review."
            )

    # 3. Load per-source color plan if provided.
    color_plan_obj: ColorPlan | None = None
    if color_plan_json:
        given = Path(color_plan_json)
        if given.is_absolute() and given.exists():
            color_plan_obj = load_color_plan(given)
        else:
            cp_candidates = [
                project_dir / color_plan_json,
                project_dir / "data" / color_plan_json,
                project_dir / "demos" / color_plan_json,
            ]
            resolved = next((p for p in cp_candidates if p.exists()), None)
            if resolved:
                color_plan_obj = load_color_plan(resolved)
            else:
                warnings.append(f"color_plan_json not found in project: {color_plan_json}")

    # 4. Assemble video (with per-source grade applied during extraction)
    video_noaudio = work_dir / "assembled_noaudio.mp4"
    asm = assemble_rough_cut(
        shots=shots,
        raw_dir=raw_dir,
        output_path=video_noaudio,
        width=target_width,
        height=target_height,
        fps=target_fps,
        work_dir=work_dir / "clips",
        color_plan=color_plan_obj,
    )
    if not asm.success:
        return RoughCutResult(
            success=False,
            output_path=None,
            assembly=asm,
            director_review=director_review_result,
            stderr=asm.stderr,
        )
    warnings.extend(asm.warnings)

    # 5. Apply smart transitions between assembled clips
    current_video = video_noaudio
    if enable_transitions:
        clips_dir = work_dir / "clips"
        # Collect clip paths in shot order (same naming convention as assemble.py)
        clip_paths = [
            clips_dir / f"shot_{s.number:03d}.mp4"
            for s in shots
            if (clips_dir / f"shot_{s.number:03d}.mp4").exists()
        ]

        if len(clip_paths) >= 2:
            # Build ShotContext list from parsed shots
            act_numbers = [s.act for s in shots]
            contexts: list[ShotContext] = []
            for idx, s in enumerate(shots):
                is_act_boundary = idx > 0 and s.act != act_numbers[idx - 1]
                contexts.append(
                    ShotContext(
                        mood=s.mood or "",
                        act=s.act,
                        era="",  # era not in ShotEntry; enriched from edit_plan if available
                        motion_direction="static",
                        is_on_drop=False,  # drop info from song structure if available
                        is_act_boundary=is_act_boundary,
                        is_flashback="flashback" in (s.mood or "").lower()
                        or "memory" in (s.mood or "").lower(),
                        character_speaks=False,
                        duration_sec=s.duration_sec,
                    )
                )

            # Enrich contexts from edit_plan_dict shots if present
            ep_shots_by_num = {
                ep_s.get("number", ep_s.get("n", 0)): ep_s
                for ep_s in edit_plan_dict.get("shots", [])
            }
            for idx, s in enumerate(shots):
                ep = ep_shots_by_num.get(s.number)
                if ep:
                    contexts[idx].era = str(ep.get("era", ""))
                    contexts[idx].character_speaks = bool(ep.get("character_speaks", False))

            with_transitions_path = work_dir / "with_transitions.mp4"
            transitions_result = apply_transitions(
                clip_paths,
                contexts,
                with_transitions_path,
                work_dir=work_dir / "transitions",
                width=target_width,
                height=target_height,
                fps=target_fps,
            )
            if transitions_result.success:
                current_video = with_transitions_path
                if transitions_result.transition_failures > 0:
                    warnings.append(
                        f"{transitions_result.transition_failures} transition(s) fell back "
                        f"to hard cut due to render errors."
                    )
            else:
                warnings.append(
                    f"Transitions pass failed ({transitions_result.stderr[:200]}); "
                    f"using concat-only video."
                )
        else:
            warnings.append(
                "Transitions skipped: fewer than 2 assembled clips found in work dir."
            )

    # 6. Title overlays
    if enable_title_overlays:
        try:
            overlay_plan: OverlayPlan = build_overlay_plan_from_edit_plan(
                edit_plan_dict,
                total_duration_sec=asm.duration_sec,
                editor_name=overlay_editor_name,
            )
            if overlay_plan.overlays:
                with_overlays_path = work_dir / "with_overlays.mp4"
                chapters_path = exports_dir / "youtube-chapters.txt"
                try:
                    result_path = build_overlay_layer(
                        current_video,
                        overlay_plan,
                        with_overlays_path,
                        chapters_output=chapters_path,
                        width=target_width,
                        height=target_height,
                        fps=target_fps,
                        work_dir=work_dir / "overlays",
                    )
                    current_video = result_path
                except (RuntimeError, FileNotFoundError) as exc:
                    warnings.append(
                        f"Title overlay render failed ({exc}); continuing without overlays."
                    )
            else:
                warnings.append(
                    "Title overlays skipped: no overlay entries generated from edit plan."
                )
        except Exception as exc:
            warnings.append(
                f"Title overlay pass raised exception (non-fatal): {exc}"
            )

    # 7. Master color grade (skip if per-source plan was used)
    if color_plan_obj is None and color_preset != ColorPreset.NONE:
        graded = work_dir / "graded.mp4"
        color_result = apply_base_grade(current_video, graded, preset=color_preset)
        final_video = graded if color_result.success else current_video
        if not color_result.success:
            warnings.append(
                f"Master color grade failed; using ungraded video. "
                f"{color_result.stderr[:200]}"
            )
    else:
        final_video = current_video

    # 8. Mix audio
    mix_result: MixResult | None = None
    audio_path: Path | None = None
    if song_filename:
        # Autopilot's copy_song step writes to assets/; legacy callers may put
        # the song in raw/. Also accept an absolute / project-relative path.
        given = Path(song_filename)
        candidates: list[Path] = []
        if given.is_absolute():
            candidates.append(given)
        else:
            candidates.extend([
                project_dir / "assets" / given.name,
                project_dir / "assets" / song_filename,
                raw_dir / given.name,
                raw_dir / song_filename,
                project_dir / song_filename,
            ])
        song_path = next((p for p in candidates if p.exists()), None)
        if song_path is not None and not _validate_song_stream(song_path):
            warnings.append(
                f"Song {song_path.name} has no decodable audio stream — "
                f"falling back to silent track."
            )
            song_path = None
        if song_path is not None:
            dialogue_resolved: Path | None = None
            if dialogue_script_json:
                given = Path(dialogue_script_json)
                if given.is_absolute() and given.exists():
                    dialogue_resolved = given
                else:
                    d_candidates = [
                        project_dir / dialogue_script_json,
                        project_dir / "data" / dialogue_script_json,
                        project_dir / "demos" / dialogue_script_json,
                        project_dir / "plans" / dialogue_script_json,
                    ]
                    dialogue_resolved = next(
                        (p for p in d_candidates if p.exists()), None
                    )
                if dialogue_resolved is None:
                    warnings.append(
                        f"Dialogue JSON not found in project: {dialogue_script_json}"
                    )
            cues, dialogue_warnings = _load_dialogue_cues(
                dialogue_resolved, dialogue_dir
            )
            warnings.extend(dialogue_warnings)

            # Resolve optional sfx-plan.json (arg override > default path).
            sfx_cues, scene_audio_path, scene_audio_gain_db, sfx_warnings = (
                _prepare_sfx_and_scene_audio(
                    project_dir=project_dir,
                    work_dir=work_dir,
                    shots=shots,
                    raw_dir=raw_dir,
                    total_duration_sec=asm.duration_sec,
                    sfx_plan_json=sfx_plan_json,
                )
            )
            warnings.extend(sfx_warnings)

            audio_path = work_dir / "mixed_audio.wav"
            mix_result = mix_audio(
                song_path=song_path,
                dialogue_cues=cues,
                output_path=audio_path,
                total_duration_sec=asm.duration_sec,
                song_start_offset_sec=song_start_offset_sec,
                song_gain_db=song_gain_db,
                sfx_cues=sfx_cues,
                scene_audio_path=scene_audio_path,
                scene_audio_gain_db=scene_audio_gain_db,
            )
            if not mix_result.success:
                warnings.append(
                    f"Audio mix failed, falling back to silent track. "
                    f"{mix_result.stderr[:200]}"
                )
                audio_path = None
        else:
            warnings.append(
                f"Song file not found in assets/ or raw/: {song_filename}"
            )

    # 9. Merge video + audio
    final_output = exports_dir / output_name

    if audio_path is not None and audio_path.exists():
        if not _merge_video_and_audio(final_video, audio_path, final_output):
            return RoughCutResult(
                success=False,
                output_path=None,
                assembly=asm,
                mix=mix_result,
                transitions=transitions_result,
                director_review=director_review_result,
                warnings=warnings,
                stderr="Failed to mux video + audio into final MP4.",
            )
    else:
        # No audio — add silent track
        cmd = [
            "ffmpeg", "-y",
            "-i", str(final_video),
            "-f", "lavfi",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            str(final_output),
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=300)
        except subprocess.CalledProcessError as exc:
            return RoughCutResult(
                success=False,
                output_path=None,
                assembly=asm,
                mix=mix_result,
                transitions=transitions_result,
                director_review=director_review_result,
                warnings=warnings,
                stderr=(exc.stderr or str(exc))[-1000:],
            )

    # Clean the scratch dir on success unless the caller asked to keep it.
    # On failure we preserve it so the user can inspect the half-finished state.
    if not keep_work_dir:
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except OSError:
            pass

    return RoughCutResult(
        success=True,
        output_path=final_output,
        assembly=asm,
        mix=mix_result,
        transitions=transitions_result,
        director_review=director_review_result,
        duration_sec=asm.duration_sec,
        warnings=warnings,
    )


def _prepare_sfx_and_scene_audio(
    *,
    project_dir: Path,
    work_dir: Path,
    shots: list[Any],
    raw_dir: Path,
    total_duration_sec: float,
    sfx_plan_json: str | None,
) -> tuple[list[SfxCue], Path | None, float, list[str]]:
    """Build SfxCue list + scene-audio WAV from sfx-plan.json.

    - Resolves SFX file paths (falls back silently when a variant is missing)
    - Builds scene_audio.wav by extracting source-clip audio per shot and
      concatenating onto a timeline track. Respects scene_audio_blend.enabled.
    - Returns (cues, scene_audio_path, scene_audio_gain_db, warnings).
    """
    warnings: list[str] = []

    candidates: list[Path] = []
    if sfx_plan_json:
        given = Path(sfx_plan_json)
        if given.is_absolute():
            candidates.append(given)
        else:
            candidates.extend([
                project_dir / sfx_plan_json,
                project_dir / "data" / sfx_plan_json,
            ])
    candidates.append(project_dir / "data" / "sfx-plan.json")

    plan_path = next((p for p in candidates if p.exists()), None)
    if plan_path is None:
        return [], None, -20.0, warnings

    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        warnings.append(f"Could not read sfx-plan.json: {exc}")
        return [], None, -20.0, warnings

    from fandomforge.intelligence.sfx_engine import resolve_sfx_file

    cues: list[SfxCue] = []
    missing: set[str] = set()
    for event in plan.get("events") or []:
        variant = event.get("variant")
        kind = event.get("kind")
        if not variant or not kind:
            continue
        audio = resolve_sfx_file(variant, kind, project_dir)
        if audio is None:
            missing.add(f"{kind}/{variant}")
            continue
        cues.append(
            SfxCue(
                audio_path=audio,
                start_sec=float(event.get("time_sec") or 0.0),
                gain_db=float(event.get("gain_db") or 0.0),
            )
        )
    if missing:
        warnings.append(
            f"SFX missing {len(missing)} variant(s) (first: {sorted(missing)[0]}). "
            f"Drop .wav files under {project_dir}/sfx/<kind>/ or ~/.fandomforge/sfx/<kind>/."
        )

    blend = plan.get("scene_audio_blend") or {}
    scene_enabled = bool(blend.get("enabled", False))
    scene_gain = float(blend.get("gain_db", -20.0))
    scene_path: Path | None = None
    if scene_enabled and shots:
        scene_path = work_dir / "scene_audio.wav"
        ok, build_warnings = _build_scene_audio_track(
            shots=shots,
            raw_dir=raw_dir,
            work_dir=work_dir,
            out_wav=scene_path,
            total_duration_sec=total_duration_sec,
        )
        warnings.extend(build_warnings)
        if not ok:
            warnings.append(
                "Scene-audio build produced empty output. Mix continues without scene bed."
            )
            scene_path = None

    return cues, scene_path, scene_gain, warnings


def _build_scene_audio_track(
    *,
    shots: list[Any],
    raw_dir: Path,
    work_dir: Path,
    out_wav: Path,
    total_duration_sec: float,
) -> tuple[bool, list[str]]:
    """Rebuild a timeline-aligned scene-audio WAV from the source clips.

    For each shot: extract <duration> seconds of audio from the source video
    starting at the shot's source timecode. Concatenate all per-shot WAVs in
    shot order and trim to total_duration_sec. Silent clips are padded with
    silence so timing stays honest.
    """
    if shutil.which("ffmpeg") is None:
        return False, ["ffmpeg not found; cannot build scene audio"]

    from fandomforge.assembly.assemble import _find_source_video

    work = work_dir / "scene_audio_clips"
    work.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    per_clip_wavs: list[Path] = []
    for shot in shots:
        clip_wav = work / f"shot_{shot.number:03d}.wav"
        dur = float(shot.duration_sec or 0)
        if dur <= 0:
            continue

        source = _find_source_video(raw_dir, shot.source_id)
        start_sec = shot.source_timestamp_sec
        if source is None or start_sec is None:
            # Silent filler keeps timing correct on shots with no source audio.
            _write_silence_wav(clip_wav, dur)
            per_clip_wavs.append(clip_wav)
            continue

        try:
            subprocess.run(
                ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
                 "-ss", f"{start_sec:.3f}",
                 "-i", str(source),
                 "-t", f"{dur:.3f}",
                 "-vn", "-ac", "2", "-ar", "48000",
                 "-acodec", "pcm_s16le",
                 str(clip_wav)],
                check=True, capture_output=True, timeout=60,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            _write_silence_wav(clip_wav, dur)
        if clip_wav.exists() and clip_wav.stat().st_size > 0:
            per_clip_wavs.append(clip_wav)

    if not per_clip_wavs:
        return False, warnings

    concat_list = work / "concat.txt"
    with concat_list.open("w") as f:
        for p in per_clip_wavs:
            f.write(f"file '{p.resolve()}'\n")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-f", "concat", "-safe", "0",
             "-i", str(concat_list),
             "-t", f"{total_duration_sec:.3f}",
             "-acodec", "pcm_s16le",
             str(out_wav)],
            check=True, capture_output=True, timeout=300,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        warnings.append(f"scene audio concat failed: {exc}")
        return False, warnings

    return out_wav.exists() and out_wav.stat().st_size > 1000, warnings


def _write_silence_wav(path: Path, duration_sec: float) -> None:
    """Emit a silent WAV of the given duration to keep concat timing correct."""
    if shutil.which("ffmpeg") is None:
        return
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi",
             "-i", f"anullsrc=channel_layout=stereo:sample_rate=48000",
             "-t", f"{duration_sec:.3f}",
             "-acodec", "pcm_s16le",
             str(path)],
            check=True, capture_output=True, timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass


def _validate_song_stream(path: Path) -> bool:
    """Confirm a song file has a decodable audio stream before passing to mix.

    A corrupt or video-only container silently muxes to a silent track,
    producing graded.mp4 files with no audio. Cheap ffprobe pre-check
    prevents that class of bug.
    """
    if shutil.which("ffprobe") is None:
        return True  # can't check, assume ok
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error",
             "-select_streams", "a:0",
             "-show_entries", "stream=codec_name,channels,sample_rate",
             "-of", "default=noprint_wrappers=1:nokey=1",
             str(path)],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    if proc.returncode != 0:
        return False
    out = (proc.stdout or "").strip()
    # Must have codec_name AND channels AND sample_rate (three non-empty lines).
    lines = [l for l in out.splitlines() if l.strip()]
    return len(lines) >= 3
