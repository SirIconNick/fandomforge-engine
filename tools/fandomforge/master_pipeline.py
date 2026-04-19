"""Master pipeline — orchestrates every engine subsystem end-to-end.

Pipeline order:

  1. INGEST        — migrate scene-library JSON into shot_library.db
  2. ENRICH        — motion_flow + gaze_detector update DB columns
  3. SONG          — song_structure.analyze(song)
  4. STYLE         — pick cluster template, load style_profile
  5. VO            — extract multi-era Leon lines if missing
  6. OPTIMIZE      — shot_optimizer.plan_edit()
  7. DIRECTOR      — holistic edit-plan review, log suggestions
  8. STORYBOARD    — PNG grid (optional, for approval)
  9. COLOR         — per-source LUT plan
 10. ASSEMBLE      — build_rough_cut with transitions + titles
 11. AUDIO         — per-section ducking + SFX
 12. QA            — multi-gate quality check
 13. PUBLISH       — thumbnail + captions + YouTube meta + export presets

Each stage is opt-in via flags so partial runs are possible.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PipelineConfig:
    project_dir: Path
    song_path: Path
    output_name: str = "final.mp4"

    # Template selection
    template_name: str = "HauntedVeteran"
    cluster_archetype: str = "single-character arc"

    # Song
    song_offset_sec: float = 0.0
    target_duration_sec: float | None = None

    # Enable flags
    enrich_motion: bool = True
    enrich_gaze: bool = True
    extract_missing_vo: bool = True
    run_director: bool = True
    build_storyboard: bool = True
    apply_transitions: bool = True
    add_titles: bool = False  # requires ffmpeg with --enable-libfreetype; off by default
    add_sfx: bool = True
    run_qa: bool = True
    build_thumbnail: bool = True
    generate_captions: bool = True
    build_youtube_meta: bool = True
    copyright_audit: bool = True
    export_presets: list[str] = field(default_factory=lambda: ["youtube"])

    # Character + era (filled from project-config.yaml when available)
    primary_character: str = "leon"
    character_aliases: list[str] = field(default_factory=list)
    era_source_map: dict[str, str] = field(default_factory=dict)
    excluded_sources: list[str] = field(default_factory=list)
    song_gain_db: float = -4.0
    default_duck_db: float = -10.0
    narrative_priorities: list[str] = field(default_factory=list)
    era_arc: list[dict] = field(default_factory=list)
    concept_beats: list[dict] = field(default_factory=list)
    vision_context: str = "game cutscene"

    # Style
    lut_name: str = "cinematic-teal-orange"
    lut_intensity: float = 0.5

    # Dialogue
    dialogue_dir: Path | None = None


def from_project_config(project_dir: Path, **overrides: Any) -> "PipelineConfig":
    """Build a PipelineConfig from a project-config.yaml on disk.

    CLI flags passed as **overrides take precedence over config file values.
    """
    from fandomforge.config import load_project_config
    pcfg = load_project_config(project_dir)

    song_path = pcfg.song_path
    if song_path is None and overrides.get("song_path") is None:
        raise ValueError(
            f"project-config.yaml in {project_dir} has no `song:` field and no "
            f"--song override was provided. Cannot proceed without a song."
        )

    kwargs: dict[str, Any] = {
        "project_dir": project_dir,
        "song_path": song_path or Path(""),
        "output_name": overrides.get("output_name", "final.mp4"),
        "template_name": pcfg.template,
        "cluster_archetype": pcfg.cluster_archetype,
        "song_offset_sec": pcfg.song_offset_sec,
        "target_duration_sec": pcfg.target_duration_sec,
        "apply_transitions": pcfg.apply_transitions,
        "add_titles": pcfg.add_titles,
        "add_sfx": pcfg.add_sfx,
        "run_qa": pcfg.run_qa,
        "extract_missing_vo": pcfg.extract_missing_vo,
        "run_director": pcfg.run_director,
        "build_storyboard": pcfg.build_storyboard,
        "build_thumbnail": pcfg.build_thumbnail,
        "generate_captions": pcfg.generate_captions,
        "build_youtube_meta": pcfg.build_youtube_meta,
        "copyright_audit": pcfg.copyright_audit,
        "enrich_motion": pcfg.enrich_motion,
        "enrich_gaze": pcfg.enrich_gaze,
        "export_presets": list(pcfg.export_presets or ["youtube"]),
        "primary_character": pcfg.character,
        "character_aliases": list(pcfg.character_aliases or []),
        "era_source_map": dict(pcfg.era_source_map or {}),
        "excluded_sources": list(pcfg.excluded_sources or []),
        "song_gain_db": float(pcfg.song_gain_db),
        "default_duck_db": float(pcfg.default_duck_db),
        "narrative_priorities": list(pcfg.narrative_priorities or []),
        "era_arc": list(pcfg.era_arc or []),
        "concept_beats": list(pcfg.concept_beats or []),
        "vision_context": pcfg.vision_context,
        "lut_name": pcfg.lut_name,
        "lut_intensity": pcfg.lut_intensity,
    }

    # CLI-level overrides win over config
    for k, v in overrides.items():
        if v is not None and k in kwargs:
            kwargs[k] = v

    return PipelineConfig(**kwargs)


@dataclass
class StageResult:
    name: str
    ok: bool
    duration_sec: float = 0.0
    output_path: Path | None = None
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class PipelineResult:
    success: bool
    final_video: Path | None = None
    edit_plan_path: Path | None = None
    storyboard_path: Path | None = None
    thumbnail_path: Path | None = None
    captions_srt: Path | None = None
    youtube_metadata: dict | None = None
    qa_passed: bool = False
    stages: list[StageResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _log(msg: str) -> None:
    print(f"[pipeline] {msg}", flush=True)


def _time_stage(name: str):
    """Context manager stub — caller fills duration_sec after."""
    import time
    return (name, time.time())


def _finish_stage(stage_ctx: tuple, ok: bool, **kwargs) -> StageResult:
    import time
    name, start = stage_ctx
    return StageResult(
        name=name,
        ok=ok,
        duration_sec=time.time() - start,
        **kwargs,
    )


def run(cfg: PipelineConfig) -> PipelineResult:
    """Execute the full master pipeline."""
    proj = cfg.project_dir
    result = PipelineResult(success=False)

    # --- 1. INGEST ------------------------------------------------------
    db_path = proj / ".shot-library.db"
    ctx = _time_stage("ingest")
    try:
        from fandomforge.intelligence.shot_library import ingest_and_verify
        scene_cache = proj / ".scene-cache.json"
        scene_lib = proj / ".scene-library.json"
        era_patterns: dict[str, str] | None = None
        if cfg.era_source_map:
            era_patterns = {
                era: rf"{stem.replace('-', '[-_ ]')}|\b{era.lower()}\b"
                for era, stem in cfg.era_source_map.items()
            }
        character_vocab: set[str] | None = None
        primary_char = (cfg.primary_character or "").strip().lower()
        if primary_char:
            character_vocab = {primary_char}
            character_vocab.update(
                a.strip().lower() for a in cfg.character_aliases if a.strip()
            )
            # Supporting cast (optional extension; falls back to empty list)
            supporting = getattr(cfg, "supporting_characters", None) or []
            character_vocab.update(s.strip().lower() for s in supporting if s)
        if scene_cache.exists() and scene_lib.exists():
            stats = ingest_and_verify(
                db_path, scene_cache, scene_lib,
                era_patterns=era_patterns,
                character_vocab=character_vocab,
            )
            _log(f"ingested {stats.get('total_shots', 0)} shots into {db_path}")
        else:
            _log("ingest skipped (cache files missing)")
        result.stages.append(_finish_stage(ctx, True, output_path=db_path))
    except Exception as exc:  # noqa: BLE001
        result.stages.append(_finish_stage(ctx, False, warnings=[str(exc)]))
        _log(f"ingest failed: {exc}")

    # --- 2. ENRICH ------------------------------------------------------
    if cfg.enrich_motion:
        ctx = _time_stage("motion_flow")
        try:
            from fandomforge.intelligence.motion_flow import analyze_library as mf_analyze
            mf_analyze(proj / "raw", db_path)
            result.stages.append(_finish_stage(ctx, True))
            _log("motion_flow done")
        except Exception as exc:  # noqa: BLE001
            result.stages.append(_finish_stage(ctx, False, warnings=[str(exc)]))

    if cfg.enrich_gaze:
        ctx = _time_stage("gaze_detector")
        try:
            from fandomforge.intelligence.gaze_detector import analyze_library as gz_analyze
            gz_analyze(proj / "raw", db_path)
            result.stages.append(_finish_stage(ctx, True))
            _log("gaze detection done")
        except Exception as exc:  # noqa: BLE001
            result.stages.append(_finish_stage(ctx, False, warnings=[str(exc)]))

    # --- 3. SONG STRUCTURE ---------------------------------------------
    song_structure = None
    song_struct_path = proj / f".song-structure-{cfg.song_path.stem}.json"
    ctx = _time_stage("song_structure")
    try:
        from fandomforge.intelligence.song_structure import analyze as analyze_song
        song_structure = analyze_song(cfg.song_path)
        song_structure.to_json(song_struct_path)
        result.stages.append(_finish_stage(ctx, True, output_path=song_struct_path))
        _log(f"song: {song_structure.tempo:.1f} BPM, {len(song_structure.sections)} sections")
    except Exception as exc:  # noqa: BLE001
        result.stages.append(_finish_stage(ctx, False, warnings=[str(exc)]))
        _log(f"song analysis failed: {exc}")

    # --- 4. STYLE TEMPLATE ---------------------------------------------
    style_profile = None
    ctx = _time_stage("style")
    try:
        cluster_template = proj / f".style-template-{cfg.cluster_archetype.replace(' ', '-')}.json"
        global_template = proj / ".style-template.json"
        pick = cluster_template if cluster_template.exists() else global_template
        if pick.exists():
            style_profile = json.loads(pick.read_text())
            result.stages.append(_finish_stage(ctx, True, output_path=pick))
            _log(f"style template: {pick.name}")
        else:
            result.stages.append(_finish_stage(ctx, False, warnings=["no style template found"]))
    except Exception as exc:  # noqa: BLE001
        result.stages.append(_finish_stage(ctx, False, warnings=[str(exc)]))

    # --- 5. MULTI-ERA VO ----------------------------------------------
    dialogue_cues: list[dict] = []
    dialogue_dir = cfg.dialogue_dir or proj / "dialogue"
    if cfg.extract_missing_vo:
        ctx = _time_stage("multi_era_vo")
        try:
            from fandomforge.intelligence.multi_era_vo import build_full_vo_library
            lib = build_full_vo_library(proj / "raw", dialogue_dir)
            n = sum(len(v) for v in lib.values())
            result.stages.append(_finish_stage(ctx, True, notes=[f"{n} VO lines across eras"]))
            _log(f"multi-era VO: {n} lines")
        except Exception as exc:  # noqa: BLE001
            result.stages.append(_finish_stage(ctx, False, warnings=[str(exc)]))

    # Load all dialogue wavs as DialogueCue objects.
    # Use transcript-map.json if available so expected_line reflects the actual
    # Whisper-verified content of each wav, not just the filename stem.
    from fandomforge.intelligence.shot_optimizer import DialogueCue, load_dialogue_cues
    dialogue_cue_objs: list[DialogueCue] = []
    if dialogue_dir.exists():
        transcript_map_path = dialogue_dir / "transcript-map.json"
        expected_lines = None
        if transcript_map_path.exists():
            raw_map = json.loads(transcript_map_path.read_text())
            # loader expects filename stem keys (no .wav)
            expected_lines = {
                (k[:-4] if k.endswith(".wav") else k): v for k, v in raw_map.items()
            }
            _log(f"using transcript-map.json with {len(expected_lines)} verified lines")
        # Pattern uses the primary character's name
        pattern = f"{cfg.primary_character}_*.wav"
        dialogue_cue_objs = load_dialogue_cues(
            dialogue_dir, pattern=pattern, expected_lines=expected_lines,
        )
        # Fallback: if the character-prefixed pattern found nothing, try
        # legacy "leon_*" pattern (keeps old projects working during migration).
        if not dialogue_cue_objs and cfg.primary_character.lower() != "leon":
            dialogue_cue_objs = load_dialogue_cues(
                dialogue_dir, pattern="leon_*.wav", expected_lines=expected_lines,
            )
        _log(f"loaded {len(dialogue_cue_objs)} dialogue cue candidates")

    # --- 6. OPTIMIZE SHOT LIST (layered planner — dialogue-first) ------
    edit_plan = None
    plan_path = None
    layered_plan = None
    template = None
    ctx = _time_stage("layered_planner")
    try:
        from fandomforge.intelligence.layered_planner import build_layered_plan
        from fandomforge.intelligence.narrative_templates import get_template
        template = get_template(cfg.template_name)
        # Beats for cut snapping
        song_beats = None
        if song_structure is not None and hasattr(song_structure, "beats"):
            try:
                song_beats = [b.time for b in song_structure.beats]
            except Exception:
                song_beats = None
        layered_plan = build_layered_plan(
            dialogue_dir=dialogue_dir,
            transcript_map_path=dialogue_dir / "transcript-map.json",
            source_map_path=dialogue_dir / "source-map.json",
            raw_dir=proj / "raw",
            shot_library_db=db_path,
            style_profile=style_profile or {},
            song_structure=song_structure,
            song_beats=song_beats,
            total_duration=cfg.target_duration_sec or 90.0,
            target_dialogue_count=7,
            narrative_priority=cfg.narrative_priorities,
            character=cfg.primary_character,
            character_aliases=cfg.character_aliases,
            era_source_map=cfg.era_source_map,
            excluded_sources=cfg.excluded_sources,
            era_arc=cfg.era_arc,
            concept_beats=cfg.concept_beats,
        )
        plan_path = proj / ".layered-plan-final.json"
        layered_plan.to_json(plan_path)
        result.edit_plan_path = plan_path
        # Keep edit_plan variable for downstream stages (director, QA etc).
        edit_plan = layered_plan
        result.stages.append(_finish_stage(
            ctx, layered_plan.validation_passed,
            output_path=plan_path,
            notes=[
                f"{len(layered_plan.shots)} shots",
                f"{len(layered_plan.dialogue_lines)} VO",
                f"validation={'pass' if layered_plan.validation_passed else 'fail'}",
            ],
        ))
        _log(f"layered plan: {len(layered_plan.shots)} shots, "
             f"{len(layered_plan.dialogue_lines)} VO, "
             f"validated={layered_plan.validation_passed}")
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb
        result.stages.append(_finish_stage(
            ctx, False, warnings=[f"{exc}\n{_tb.format_exc()[:500]}"],
        ))
        _log(f"layered planner failed: {exc}")

    # --- 7. DIRECTOR REVIEW --------------------------------------------
    # Skip for LayeredPlan for now — director was written for EditPlan shape.
    if cfg.run_director and edit_plan is not None and layered_plan is None:
        ctx = _time_stage("director")
        try:
            from fandomforge.intelligence.director import review_edit_plan
            review = review_edit_plan(
                edit_plan, template, song_structure, style_profile
            )
            result.stages.append(_finish_stage(
                ctx, True,
                notes=[f"story_arc={review.story_arc_score:.2f}"],
            ))
        except Exception as exc:  # noqa: BLE001
            result.stages.append(_finish_stage(ctx, False, warnings=[str(exc)]))

    # --- 8. STORYBOARD --------------------------------------------------
    # Skip for LayeredPlan — storyboard was written for EditPlan shape.
    if cfg.build_storyboard and edit_plan is not None and layered_plan is None:
        ctx = _time_stage("storyboard")
        try:
            from fandomforge.intelligence.storyboard import build_storyboard
            sb = proj / "exports" / f"{Path(cfg.output_name).stem}.storyboard.png"
            sb.parent.mkdir(parents=True, exist_ok=True)
            build_storyboard(edit_plan, proj / "raw", sb)
            result.storyboard_path = sb
            result.stages.append(_finish_stage(ctx, True, output_path=sb))
        except Exception as exc:  # noqa: BLE001
            result.stages.append(_finish_stage(ctx, False, warnings=[str(exc)]))

    # --- 9. COLOR PLAN --------------------------------------------------
    # Skip for LayeredPlan (color_grader expects EditPlan.shots shape).
    color_plan = None
    if edit_plan is not None and layered_plan is None:
        ctx = _time_stage("color")
        try:
            from fandomforge.intelligence.color_grader import (
                analyze_sources, generate_matching_luts, build_color_plan,
            )
            stats = analyze_sources(proj / "raw")
            lut_map = generate_matching_luts(stats, target_palette=None)
            color_plan = build_color_plan(edit_plan, lut_map)
            _log("color plan built")
            result.stages.append(_finish_stage(ctx, True))
        except Exception as exc:  # noqa: BLE001
            result.stages.append(_finish_stage(ctx, False, warnings=[str(exc)]))

    # --- 10-12. ASSEMBLE + AUDIO + MUX (via orchestrator) ---------------
    final_video = None
    ctx = _time_stage("render")
    try:
        from fandomforge.assembly.orchestrator import build_rough_cut
        from fandomforge.intelligence.plan_to_shot_list import (
            convert, convert_layered, convert_dialogue,
        )
        # Convert plan into orchestrator-friendly formats.
        # Auto-detect LayeredPlan vs EditPlan via convert_layered dispatcher.
        shot_md = proj / "plans" / f"{Path(cfg.output_name).stem}-shot-list.md"
        dialogue_json = proj / "plans" / f"{Path(cfg.output_name).stem}-dialogue.json"
        if plan_path is not None and plan_path.exists():
            convert_layered(plan_path, shot_md)
            convert_dialogue(
                plan_path, dialogue_json,
                character=cfg.primary_character.title(),
                default_duck_db=cfg.default_duck_db,
            )
        from fandomforge.assembly.color import ColorPreset as _ColorPreset
        rc = build_rough_cut(
            project_dir=proj,
            shot_list_name=shot_md.name,
            song_filename=cfg.song_path.name,
            song_start_offset_sec=cfg.song_offset_sec,
            dialogue_script_json=dialogue_json.name if dialogue_json.exists() else None,
            color_plan_json=None,
            color_preset=_ColorPreset.NONE,
            output_name=cfg.output_name,
            enable_director_review=False,  # already done above
            enable_transitions=cfg.apply_transitions,
            enable_title_overlays=cfg.add_titles,
            edit_plan_json=str(plan_path) if plan_path else None,
            song_gain_db=cfg.song_gain_db,
        )
        if rc.success:
            final_video = rc.output_path
            result.final_video = final_video
            result.stages.append(_finish_stage(ctx, True, output_path=final_video))
            _log(f"rendered: {final_video}")
        else:
            result.stages.append(_finish_stage(ctx, False, warnings=[rc.stderr or "render failed"]))
    except Exception as exc:  # noqa: BLE001
        result.stages.append(_finish_stage(ctx, False, warnings=[str(exc)]))

    # --- 13. QA ---------------------------------------------------------
    if cfg.run_qa and final_video is not None:
        ctx = _time_stage("qa")
        try:
            from fandomforge.intelligence.qa_loop import run_qa
            qa = run_qa(final_video, edit_plan, template, style_profile or {})
            result.qa_passed = all([
                qa.audio_gate.passed,
                qa.visual_gate.passed,
                qa.pacing_gate.passed,
                qa.structural_gate.passed,
                qa.narrative_gate.passed,
            ])
            notes = [
                f"audio={qa.audio_gate.passed}",
                f"visual={qa.visual_gate.passed}",
                f"pacing={qa.pacing_gate.passed}",
                f"structural={qa.structural_gate.passed}",
                f"narrative={qa.narrative_gate.passed}",
            ]
            result.stages.append(_finish_stage(ctx, result.qa_passed, notes=notes))
            _log(f"QA: {'PASS' if result.qa_passed else 'FAIL'}")
            for s in qa.fix_suggestions[:3]:
                _log(f"  fix: {s}")
        except Exception as exc:  # noqa: BLE001
            result.stages.append(_finish_stage(ctx, False, warnings=[str(exc)]))

    # --- 14. PUBLISH artifacts -----------------------------------------
    if final_video is not None and cfg.build_thumbnail:
        ctx = _time_stage("thumbnail")
        try:
            from fandomforge.intelligence.thumbnail_selector import select_thumbnail
            thumb = final_video.with_suffix(".thumb.jpg")
            select_thumbnail(final_video, thumb)
            result.thumbnail_path = thumb
            result.stages.append(_finish_stage(ctx, True, output_path=thumb))
        except Exception as exc:  # noqa: BLE001
            result.stages.append(_finish_stage(ctx, False, warnings=[str(exc)]))

    if final_video is not None and cfg.generate_captions and edit_plan:
        ctx = _time_stage("captions")
        try:
            from fandomforge.intelligence.caption_generator import generate_captions
            srt = final_video.with_suffix(".srt")
            generate_captions(edit_plan, srt)
            result.captions_srt = srt
            result.stages.append(_finish_stage(ctx, True, output_path=srt))
        except Exception as exc:  # noqa: BLE001
            result.stages.append(_finish_stage(ctx, False, warnings=[str(exc)]))

    if final_video is not None and cfg.build_youtube_meta:
        ctx = _time_stage("youtube_meta")
        try:
            from fandomforge.intelligence.youtube_metadata import (
                build_youtube_metadata, SongInfo,
            )
            song_title = Path(cfg.song_path).stem.replace("-", " ").replace("_", " ").title()
            si = SongInfo(title=song_title, artist="Unknown")
            yt = build_youtube_metadata(
                edit_plan=edit_plan,
                song_info=si,
                character=cfg.primary_character.title(),
                style="cinematic",
            )
            yt_path = final_video.with_suffix(".youtube.json")
            yt_path.write_text(json.dumps(yt.to_dict() if hasattr(yt, "to_dict") else yt.__dict__, indent=2))
            result.youtube_metadata = yt if isinstance(yt, dict) else yt.__dict__
            result.stages.append(_finish_stage(ctx, True, output_path=yt_path))
        except Exception as exc:  # noqa: BLE001
            result.stages.append(_finish_stage(ctx, False, warnings=[str(exc)]))

    if final_video is not None and cfg.copyright_audit:
        ctx = _time_stage("copyright_audit")
        try:
            from fandomforge.intelligence.copyright_audit import audit, SongMetadata
            song_title = Path(cfg.song_path).stem.replace("-", " ").replace("_", " ").title()
            audit_result = audit(
                edit_plan=edit_plan,
                song_metadata=SongMetadata(title=song_title, artist="Unknown"),
                sources_metadata={},
            )
            audit_md = final_video.with_suffix(".fair-use.md")
            audit_result.save_markdown(audit_md)
            result.stages.append(_finish_stage(ctx, True, output_path=audit_md))
        except Exception as exc:  # noqa: BLE001
            result.stages.append(_finish_stage(ctx, False, warnings=[str(exc)]))

    if final_video is not None and cfg.export_presets:
        ctx = _time_stage("export_presets")
        try:
            from fandomforge.assembly.export_presets import export_preset
            for preset in cfg.export_presets:
                out = final_video.with_name(f"{final_video.stem}_{preset}.mp4")
                export_preset(final_video, preset, out)
            result.stages.append(_finish_stage(ctx, True))
        except Exception as exc:  # noqa: BLE001
            result.stages.append(_finish_stage(ctx, False, warnings=[str(exc)]))

    # Pipeline success requires BOTH a rendered video AND a passing QA gate
    # when QA is enabled. This prevents shipping bad builds silently.
    rendered = final_video is not None
    if cfg.run_qa:
        result.success = rendered and result.qa_passed
    else:
        result.success = rendered
    return result


def print_result(r: PipelineResult) -> None:
    print("\n" + "=" * 66)
    print(f"PIPELINE {'SUCCESS' if r.success else 'FAILURE'}")
    print("=" * 66)
    for s in r.stages:
        mark = "✓" if s.ok else "✗"
        print(f"  {mark} {s.name:20} {s.duration_sec:6.1f}s")
        for n in s.notes:
            print(f"      note: {n}")
        for w in s.warnings:
            print(f"      warn: {w}")
    if r.final_video:
        print(f"\nFinal video: {r.final_video}")
    if r.storyboard_path:
        print(f"Storyboard:  {r.storyboard_path}")
    if r.thumbnail_path:
        print(f"Thumbnail:   {r.thumbnail_path}")
    if r.captions_srt:
        print(f"Captions:    {r.captions_srt}")
    print(f"QA passed:   {r.qa_passed}")
    print("=" * 66)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="FandomForge master pipeline")
    ap.add_argument("--project", required=True, type=Path)
    ap.add_argument("--song", required=True, type=Path)
    ap.add_argument("--output", default="final.mp4")
    ap.add_argument("--template", default="HauntedVeteran")
    ap.add_argument("--cluster", default="single-character arc")
    ap.add_argument("--duration", type=float, default=None)
    ap.add_argument("--song-offset", type=float, default=0.0)
    args = ap.parse_args()

    cfg = PipelineConfig(
        project_dir=args.project,
        song_path=args.song,
        output_name=args.output,
        template_name=args.template,
        cluster_archetype=args.cluster,
        target_duration_sec=args.duration,
        song_offset_sec=args.song_offset,
    )
    res = run(cfg)
    print_result(res)
    sys.exit(0 if res.success else 1)
