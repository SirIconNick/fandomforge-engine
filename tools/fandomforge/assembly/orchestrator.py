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
from fandomforge.assembly.mixer import DialogueCue, MixResult, mix_audio
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
        song_path = raw_dir / song_filename
        if song_path.exists():
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
            audio_path = work_dir / "mixed_audio.wav"
            mix_result = mix_audio(
                song_path=song_path,
                dialogue_cues=cues,
                output_path=audio_path,
                total_duration_sec=asm.duration_sec,
                song_start_offset_sec=song_start_offset_sec,
                song_gain_db=song_gain_db,
            )
            if not mix_result.success:
                warnings.append(
                    f"Audio mix failed, falling back to silent track. "
                    f"{mix_result.stderr[:200]}"
                )
                audio_path = None
        else:
            warnings.append(f"Song file not found in raw/: {song_filename}")

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
