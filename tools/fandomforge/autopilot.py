"""Autopilot orchestrator — prompt + song + sources → finished MP4.

Ties together beat analysis, source ingestion, edit-plan drafting,
shot-proposer, emotion arc, color planning, roughcut assembly, QA gate,
and export into a single idempotent DAG.

Each step:
  - Checks whether its output already exists and is valid (SHA match or
    schema validation). If so, skips.
  - Otherwise runs, then writes an event to .history/autopilot.jsonl.

Failure handling: on any step failure, the DAG pauses. The user can fix the
issue (edit the broken artifact manually, add missing media, etc.) and
re-run `ff autopilot --project <slug>` to resume from the last good step.

The `edit-strategist` step is special: it needs an LLM. If
ANTHROPIC_API_KEY is absent, a stub edit-plan is written from the prompt
(keyword heuristic) so the downstream DAG can still run.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


# ---------- Event stream ----------


@dataclass
class AutopilotEvent:
    ts: str
    run_id: str
    step_id: str
    status: str  # started | ok | skipped | failed
    message: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    duration_sec: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "ts": self.ts,
            "run_id": self.run_id,
            "step_id": self.step_id,
            "status": self.status,
            "message": self.message,
            "evidence": self.evidence,
        }
        if self.duration_sec is not None:
            d["duration_sec"] = self.duration_sec
        return d


def _history_dir(project_dir: Path) -> Path:
    return project_dir / ".history"


def _event_log_path(project_dir: Path) -> Path:
    return _history_dir(project_dir) / "autopilot.jsonl"


def _append_event(project_dir: Path, event: AutopilotEvent) -> None:
    path = _event_log_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event.to_dict()) + "\n")


# ---------- Step DAG ----------


@dataclass
class Step:
    id: str
    label: str
    check_done: Callable[["AutopilotContext"], bool]
    run: Callable[["AutopilotContext"], AutopilotEvent]


@dataclass
class AutopilotContext:
    run_id: str
    project_slug: str
    project_dir: Path
    song_path: Path | None
    source_glob: str | None
    prompt: str
    verbose: bool = True
    # Library mode: pull shots from the global library instead of raw/.
    from_library: bool = False
    # Fandom filter for library mode, e.g. {"John Wick": 0.4, "Mad Max": 0.6}.
    # Keys are fandom labels (matched case-insensitively against library index),
    # values are relative weights for the sampling pass.
    fandom_mix: dict[str, float] | None = None

    def log(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)

    def record(self, event: AutopilotEvent) -> None:
        _append_event(self.project_dir, event)
        if self.verbose:
            print(f"[{event.status}] {event.step_id}: {event.message}", flush=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifact_exists_and_valid(ctx: AutopilotContext, artifact: str) -> bool:
    from fandomforge.validation import validate, ValidationError

    path = ctx.project_dir / "data" / f"{artifact}.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text())
        validate(data, artifact)
        return True
    except (ValidationError, json.JSONDecodeError, KeyError):
        return False


def _resolve_ff_binary() -> str:
    """Find the ff binary. Prefer FF_BINARY env, then the venv we're running in."""
    env_binary = os.environ.get("FF_BINARY")
    if env_binary and Path(env_binary).exists():
        return env_binary
    venv_bin = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "ff"
    if venv_bin.exists():
        return str(venv_bin)
    # Fall back to PATH lookup
    which = shutil.which("ff")
    if which:
        return which
    return "ff"


def _run_subproc(args: list[str], cwd: Path) -> tuple[int, str, str]:
    if args and args[0] == "ff":
        args = [_resolve_ff_binary(), *args[1:]]
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _use_subprocess() -> bool:
    """When FF_AUTOPILOT_SUBPROCESS=1, fall back to shelling out to `ff <sub>`.

    Default is in-process (each step calls the underlying module function
    directly). The subprocess path exists for debugging or for cases where
    process isolation matters — e.g. to confirm a bug isn't caused by
    cross-step state carried in the same interpreter.
    """
    return os.environ.get("FF_AUTOPILOT_SUBPROCESS", "").strip() in ("1", "true", "yes")


# ---------- Step implementations ----------


def step_scaffold(ctx: AutopilotContext) -> AutopilotEvent:
    start = time.perf_counter()
    ctx.project_dir.mkdir(parents=True, exist_ok=True)
    (ctx.project_dir / "assets").mkdir(exist_ok=True)
    (ctx.project_dir / "raw").mkdir(exist_ok=True)
    (ctx.project_dir / "data").mkdir(exist_ok=True)
    _history_dir(ctx.project_dir).mkdir(exist_ok=True)
    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="scaffold",
        status="ok", message="project dirs ready",
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_copy_song(ctx: AutopilotContext) -> AutopilotEvent:
    start = time.perf_counter()
    if not ctx.song_path:
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="copy_song",
            status="skipped", message="no --song provided",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    target = ctx.project_dir / "assets" / f"song{ctx.song_path.suffix}"
    if not target.exists() or target.stat().st_size != ctx.song_path.stat().st_size:
        shutil.copy2(ctx.song_path, target)
    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="copy_song",
        status="ok", message=f"song copied to {target.name}",
        evidence={"path": str(target)},
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_ingest_sources(ctx: AutopilotContext) -> AutopilotEvent:
    """Run `ff ingest` on every source video in raw/ so they land in the catalog."""
    start = time.perf_counter()
    raw = ctx.project_dir / "raw"

    # Library mode: skip local ingest and symlink matching sources into raw/
    # so downstream steps (roughcut, export) see a populated raw/ dir.
    if ctx.from_library:
        try:
            from fandomforge import library as lib
        except Exception as exc:  # noqa: BLE001
            return AutopilotEvent(
                ts=_now(), run_id=ctx.run_id, step_id="ingest_sources",
                status="failed",
                message=f"library mode requested but library module unavailable: {exc}",
                duration_sec=round(time.perf_counter() - start, 3),
            )
        raw.mkdir(parents=True, exist_ok=True)
        candidates = lib.candidate_sources(
            fandoms=ctx.fandom_mix,
            min_status="done",
        )
        if not candidates:
            return AutopilotEvent(
                ts=_now(), run_id=ctx.run_id, step_id="ingest_sources",
                status="failed",
                message=(
                    "no library sources matched "
                    + (f"fandoms {list((ctx.fandom_mix or {}).keys())}" if ctx.fandom_mix else "any filter")
                    + ". run `ff library list --sources` to see what's available."
                ),
                duration_sec=round(time.perf_counter() - start, 3),
            )
        linked = 0
        for src in candidates:
            target = raw / src.path.name
            if target.exists() or target.is_symlink():
                continue
            try:
                target.symlink_to(src.path)
                linked += 1
            except OSError as exc:
                # Fall back to copy if the filesystem doesn't support symlinks
                import shutil as _sh
                _sh.copy2(src.path, target)
                linked += 1
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="ingest_sources",
            status="ok",
            message=f"library: linked {linked} source(s) from {len(candidates)} candidate(s)",
            evidence={
                "from_library": True,
                "fandom_mix": ctx.fandom_mix,
                "linked": linked,
                "total_candidates": len(candidates),
            },
            duration_sec=round(time.perf_counter() - start, 3),
        )

    catalog = ctx.project_dir / "data" / "catalog.json"
    if not raw.exists():
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="ingest_sources",
            status="skipped", message="no raw/ folder yet",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    videos: list[Path] = []
    for pattern in ("*.mp4", "*.mov", "*.mkv", "*.webm"):
        # rglob → picks up nested fandom dirs like raw/marvel/, raw/star-wars/.
        # Flat raw/ layouts keep working because rglob includes the top level.
        videos.extend(raw.rglob(pattern))

    if not videos:
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="ingest_sources",
            status="skipped", message="no source videos in raw/",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    # Check if all of them are already ingested
    if catalog.exists():
        try:
            known = {
                c.get("id") or c.get("source_id")
                for c in (json.loads(catalog.read_text()).get("clips") or [])
            }
            all_ids = {v.stem for v in videos}
            if all_ids.issubset(known):
                return AutopilotEvent(
                    ts=_now(), run_id=ctx.run_id, step_id="ingest_sources",
                    status="skipped",
                    message=f"all {len(videos)} sources already in catalog",
                    duration_sec=round(time.perf_counter() - start, 3),
                )
        except Exception:  # noqa: BLE001
            pass

    # `ff ingest` needs a --fandom label. Use the first user-prompt fandom if we
    # can infer one, else "Unknown". Users can refine later via `ff ingest` manually.
    default_fandom = "Unknown"
    prompt_fandoms = [w.strip() for w in (ctx.prompt or "").split(",") if w.strip()]
    if prompt_fandoms:
        default_fandom = prompt_fandoms[0][:60] or "Unknown"

    project_arg = str(ctx.project_dir)

    ingested = 0
    failures: list[str] = []
    if _use_subprocess():
        for v in videos:
            rc, out, err = _run_subproc(
                ["ff", "ingest", str(v),
                 "--project", project_arg,
                 "--fandom", default_fandom,
                 "--source-type", "short",
                 "--no-characters"],
                cwd=ctx.project_dir.parent.parent,
            )
            if rc == 0:
                ingested += 1
            else:
                failures.append(f"{v.name}: exit {rc} — {err[-200:] if err else out[-200:]}")
    else:
        from fandomforge.ingest import ingest_source
        for v in videos:
            try:
                report = ingest_source(
                    video_path=v,
                    project_dir=ctx.project_dir,
                    fandom=default_fandom,
                    source_type="short",
                    run_characters=False,
                )
                if report.succeeded:
                    ingested += 1
                else:
                    first_fail = next(
                        (s for s in report.steps if s.status == "failed"), None
                    )
                    detail = first_fail.detail if first_fail else "unknown"
                    failures.append(f"{v.name}: {detail[:200]}")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{v.name}: {type(exc).__name__}: {exc}")

    if failures:
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="ingest_sources",
            status="failed" if ingested == 0 else "ok",
            message=(
                f"ingested {ingested}/{len(videos)} sources"
                + ("; failures in: " + "; ".join(failures[:3]) if failures else "")
            ),
            evidence={"ingested": ingested, "failures": failures},
            duration_sec=round(time.perf_counter() - start, 3),
        )

    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="ingest_sources",
        status="ok",
        message=f"ingested {ingested} source video{'s' if ingested != 1 else ''}",
        evidence={"ingested": ingested},
        duration_sec=round(time.perf_counter() - start, 3),
    )


def _find_song(ctx: AutopilotContext) -> Path | None:
    if ctx.song_path:
        return ctx.song_path
    for name in ("song.mp3", "song.wav", "song.m4a", "song.flac"):
        candidate = ctx.project_dir / "assets" / name
        if candidate.exists():
            return candidate
    return None


def step_beat_analyze(ctx: AutopilotContext) -> AutopilotEvent:
    start = time.perf_counter()
    out = ctx.project_dir / "data" / "beat-map.json"
    if _artifact_exists_and_valid(ctx, "beat-map"):
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="beat_analyze",
            status="skipped", message="beat-map.json already valid",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    song = _find_song(ctx)
    if not song:
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="beat_analyze",
            status="failed", message="no song found in assets/",
        )

    if _use_subprocess():
        rc, stdout, stderr = _run_subproc(
            ["ff", "beat", "analyze", str(song), "-o", str(out)],
            cwd=ctx.project_dir.parent.parent,
        )
        if rc != 0:
            return AutopilotEvent(
                ts=_now(), run_id=ctx.run_id, step_id="beat_analyze",
                status="failed", message=f"ff beat analyze exit {rc}",
                evidence={"stderr": stderr[-800:]},
                duration_sec=round(time.perf_counter() - start, 3),
            )
    else:
        try:
            from fandomforge import __version__ as _ff_version
            from fandomforge.audio import (
                analyze_beats,
                compute_energy_curve,
                detect_drops,
            )
            from fandomforge.audio.drops import detect_breakdowns, detect_buildups
            from fandomforge.validation import validate_and_write

            beat_map = analyze_beats(song)
            drops = detect_drops(song)
            buildups = detect_buildups(song, drops)
            breakdowns = detect_breakdowns(song)
            curve = compute_energy_curve(song)
            payload = {
                "schema_version": 1,
                **beat_map.to_dict(),
                "drops": [d.to_dict() for d in drops],
                "buildups": [b.to_dict() for b in buildups],
                "breakdowns": [bd.to_dict() for bd in breakdowns],
                "energy_curve": [[t, e] for t, e in curve],
                "generated_at": _now(),
                "generator": f"autopilot/beat-analyze ({_ff_version})",
            }
            out.parent.mkdir(parents=True, exist_ok=True)
            validate_and_write(payload, "beat-map", out)
        except Exception as exc:  # noqa: BLE001
            return AutopilotEvent(
                ts=_now(), run_id=ctx.run_id, step_id="beat_analyze",
                status="failed",
                message=f"beat analyze failed: {type(exc).__name__}: {exc}",
                duration_sec=round(time.perf_counter() - start, 3),
            )

    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="beat_analyze",
        status="ok", message="beat-map.json written",
        evidence={"path": str(out)},
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_tension_curve(ctx: AutopilotContext) -> AutopilotEvent:
    """Phase 2.3 — build tension-curve.json from edit-plan + beat-map +
    emotion-arc. Read by Phase 4.4 arc_shape_realized + qa.arc_shape_realized.
    """
    start = time.perf_counter()
    out = ctx.project_dir / "data" / "tension-curve.json"
    edit_plan_path = ctx.project_dir / "data" / "edit-plan.json"
    beat_map_path = ctx.project_dir / "data" / "beat-map.json"
    emotion_arc_path = ctx.project_dir / "data" / "emotion-arc.json"

    if not edit_plan_path.exists():
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="tension_curve",
            status="skipped", message="no edit-plan.json yet",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    try:
        from fandomforge.intelligence.tension_curve import (
            build_tension_curve, write_tension_curve,
        )
        edit_plan = json.loads(edit_plan_path.read_text(encoding="utf-8"))
        beat_map = (
            json.loads(beat_map_path.read_text(encoding="utf-8"))
            if beat_map_path.exists() else None
        )
        emotion_arc = (
            json.loads(emotion_arc_path.read_text(encoding="utf-8"))
            if emotion_arc_path.exists() else None
        )
        curve = build_tension_curve(edit_plan, beat_map=beat_map, emotion_arc=emotion_arc)
        write_tension_curve(curve, out)
    except Exception as exc:  # noqa: BLE001
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="tension_curve",
            status="failed",
            message=f"tension_curve failed: {type(exc).__name__}: {exc}",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    s = curve.get("summary") or {}
    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="tension_curve",
        status="ok",
        message=(
            f"tension curve: peak_actual={s.get('peak_actual')} "
            f"@t={s.get('peak_actual_time_sec')}s "
            f"builds={s.get('builds_to_climax')} resolves={s.get('resolves')} "
            f"rms_delta={s.get('rms_delta')}"
        ),
        evidence={"path": str(out), "summary": s},
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_aspect_normalize(ctx: AutopilotContext) -> AutopilotEvent:
    """Phase 3.1 — build aspect-plan.json from the shot list + per-source
    profiles. Records pillarbox/letterbox/crop/scale decisions per shot;
    the orchestrator's render pass reads this before applying color grade
    so AR adjustments precede visual unification.
    """
    start = time.perf_counter()
    out = ctx.project_dir / "data" / "aspect-plan.json"
    shot_list_path = ctx.project_dir / "data" / "shot-list.json"

    if not shot_list_path.exists():
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="aspect_normalize",
            status="skipped", message="no shot-list.json yet",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    # Pull target AR from project-config (default 16:9)
    target_ar = "16:9"
    cfg_path = ctx.project_dir / "project-config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            target_ar = str(cfg.get("aspect_ratio") or "16:9")
        except (json.JSONDecodeError, OSError):
            target_ar = "16:9"

    try:
        from fandomforge.intelligence.aspect_ratio import (
            build_aspect_plan, load_source_profiles, write_aspect_plan,
        )
        shot_list = json.loads(shot_list_path.read_text(encoding="utf-8"))
        profiles = load_source_profiles(ctx.project_dir)
        plan = build_aspect_plan(shot_list, target_ar=target_ar, source_profiles=profiles)
        write_aspect_plan(plan, out)
    except Exception as exc:  # noqa: BLE001
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="aspect_normalize",
            status="failed",
            message=f"aspect_normalize failed: {type(exc).__name__}: {exc}",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    s = plan["summary"]
    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="aspect_normalize",
        status="ok",
        message=(
            f"aspect plan: {s['no_op_count']} no-op, "
            f"{s['pillarbox_count']} pillar, {s['letterbox_count']} letter, "
            f"{s['crop_count']} crop, {s['ar_change_count']} AR transitions"
        ),
        evidence={"path": str(out), "summary": s},
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_extract_clip_metadata(ctx: AutopilotContext) -> AutopilotEvent:
    """Phase 1.3 — enrich every shot in shot-list.json with the metadata
    fields the slot-fit scorer keys off (emotional_register, clip_category,
    action_intensity_pct, dialogue_clarity_score, lip_sync_confidence,
    visual_style, audio_type, energy_zone_fit). Reads scenes + transcripts
    + source-profiles from the project's standard locations.
    """
    start = time.perf_counter()
    shot_list_path = ctx.project_dir / "data" / "shot-list.json"
    if not shot_list_path.exists():
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="extract_clip_metadata",
            status="skipped",
            message="no shot-list.json yet (skipping enrichment)",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    try:
        from fandomforge.intelligence.clip_metadata import (
            coverage_report,
            enrich_shot_list,
        )
        from fandomforge.validation import validate_and_write

        shot_list = json.loads(shot_list_path.read_text(encoding="utf-8"))
        enriched = enrich_shot_list(shot_list, ctx.project_dir)
        validate_and_write(enriched, "shot-list", shot_list_path)
        coverage = coverage_report(enriched)
    except Exception as exc:  # noqa: BLE001
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="extract_clip_metadata",
            status="failed",
            message=f"enrich failed: {type(exc).__name__}: {exc}",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    # Populated %s for the noisy fields
    msg = (
        f"shots: {coverage['total_shots']}, "
        f"register={coverage['emotional_register']*100:.0f}%, "
        f"category={coverage['clip_category']*100:.0f}%, "
        f"audio={coverage['audio_type']*100:.0f}%, "
        f"dialogue={coverage['dialogue_clarity_score']*100:.0f}%"
    )
    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="extract_clip_metadata",
        status="ok", message=msg,
        evidence={"coverage": coverage},
        duration_sec=round(time.perf_counter() - start, 3),
    )


def _is_dialogue_edit(ctx: AutopilotContext) -> bool:
    """True when the project's active edit_type is dialogue_narrative.
    Used to gate Phase 6 dialogue pipeline steps — they only make sense
    for dialogue-driven edits."""
    intent_path = ctx.project_dir / "data" / "intent.json"
    if not intent_path.exists():
        return False
    try:
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return str(intent.get("edit_type") or "") == "dialogue_narrative"


def step_dialogue_script_draft(ctx: AutopilotContext) -> AutopilotEvent:
    """Phase 6 — draft a dialogue script from the prompt + intent. Skipped
    for non-dialogue edits. Writes data/dialogue-script.json."""
    start = time.perf_counter()
    if not _is_dialogue_edit(ctx):
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="dialogue_script_draft",
            status="skipped",
            message="not a dialogue_narrative edit — skipping",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    try:
        from fandomforge.intelligence.dialogue_script import build_script
        from fandomforge.validation import validate_and_write
        intent_path = ctx.project_dir / "data" / "intent.json"
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
        script = build_script(
            prompt=ctx.prompt,
            project_slug=ctx.project_slug,
            intent=intent,
        )
        out = ctx.project_dir / "data" / "dialogue-script.json"
        validate_and_write(script, "dialogue-script", out)
    except Exception as exc:  # noqa: BLE001
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="dialogue_script_draft",
            status="failed",
            message=f"script draft failed: {type(exc).__name__}: {exc}",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    n_lines = len((script or {}).get("lines") or [])
    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="dialogue_script_draft",
        status="ok",
        message=f"drafted {n_lines} dialogue line(s)",
        evidence={"line_count": n_lines},
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_dialogue_clip_search(ctx: AutopilotContext) -> AutopilotEvent:
    """Phase 6 — search whisper transcripts for clips that match each
    scripted line. Writes data/dialogue-candidates.json (one entry per
    line, each with a top-K ranked list of candidates)."""
    start = time.perf_counter()
    if not _is_dialogue_edit(ctx):
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="dialogue_clip_search",
            status="skipped",
            message="not a dialogue_narrative edit — skipping",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    script_path = ctx.project_dir / "data" / "dialogue-script.json"
    if not script_path.exists():
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="dialogue_clip_search",
            status="skipped",
            message="no dialogue-script.json — script step must run first",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    try:
        from fandomforge.intelligence.dialogue_search import (
            search_script, load_transcripts,
        )
        script = json.loads(script_path.read_text(encoding="utf-8"))
        transcripts = load_transcripts(ctx.project_dir)
        if not transcripts:
            return AutopilotEvent(
                ts=_now(), run_id=ctx.run_id, step_id="dialogue_clip_search",
                status="skipped",
                message="no whisper transcripts in data/transcripts/ — cannot search",
                duration_sec=round(time.perf_counter() - start, 3),
            )
        candidates_raw = search_script(script, transcripts, top_k=5)
        candidates = {
            line_idx: [
                {"source_id": c.source_id, "start_sec": c.start_sec,
                 "end_sec": c.end_sec, "text": c.text, "score": c.score}
                for c in cands
            ]
            for line_idx, cands in candidates_raw.items()
        }
        out = ctx.project_dir / "data" / "dialogue-candidates.json"
        out.write_text(json.dumps({
            "schema_version": 1,
            "project_slug": ctx.project_slug,
            "candidates_per_line": candidates,
            "generated_at": _now(),
            "generator": "autopilot/dialogue_search",
        }, indent=2))
    except Exception as exc:  # noqa: BLE001
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="dialogue_clip_search",
            status="failed",
            message=f"search failed: {type(exc).__name__}: {exc}",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    total_cands = sum(len(v) for v in candidates.values())
    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="dialogue_clip_search",
        status="ok",
        message=f"found {total_cands} candidate(s) across {len(candidates)} line(s)",
        evidence={"lines_searched": len(candidates), "total_candidates": total_cands},
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_dialogue_placement(ctx: AutopilotContext) -> AutopilotEvent:
    """Phase 6 — assign each script line to a SAFE dialogue window and
    build the mixer cue plan. Writes data/dialogue-placement-plan.json."""
    start = time.perf_counter()
    if not _is_dialogue_edit(ctx):
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="dialogue_placement",
            status="skipped",
            message="not a dialogue_narrative edit — skipping",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    script_path = ctx.project_dir / "data" / "dialogue-script.json"
    candidates_path = ctx.project_dir / "data" / "dialogue-candidates.json"
    windows_path = ctx.project_dir / "data" / "dialogue-windows.json"
    for p in (script_path, candidates_path, windows_path):
        if not p.exists():
            return AutopilotEvent(
                ts=_now(), run_id=ctx.run_id, step_id="dialogue_placement",
                status="skipped",
                message=f"missing {p.name} — run upstream steps first",
                duration_sec=round(time.perf_counter() - start, 3),
            )
    try:
        from fandomforge.intelligence.dialogue_place import (
            assign_lines_to_windows, build_mixer_cues,
        )
        script = json.loads(script_path.read_text(encoding="utf-8"))
        cands_payload = json.loads(candidates_path.read_text(encoding="utf-8"))
        windows = json.loads(windows_path.read_text(encoding="utf-8"))
        candidates_per_line = cands_payload.get("candidates_per_line") or {}
        placements = assign_lines_to_windows(script, candidates_per_line, windows)
        placement_dicts = []
        for p in placements:
            pdict = {
                "line_index": p.line_index,
                "decision": p.decision,
                "requested_start_sec": p.requested_start_sec,
                "requested_duration_sec": p.requested_duration_sec,
                "flag_at_placement": p.flag_at_placement,
                "placed_start_sec": p.placed_start_sec,
                "reason": p.reason,
            }
            if getattr(p, "cue_index", None) is not None:
                pdict["cue_index"] = p.cue_index
            if getattr(p, "cue_text", None) is not None:
                pdict["cue_text"] = p.cue_text
            if getattr(p, "suggested_alternative_sec", None) is not None:
                pdict["suggested_alternative_sec"] = p.suggested_alternative_sec
            placement_dicts.append(pdict)

        payload = {
            "schema_version": 1,
            "project_slug": ctx.project_slug,
            "song_duration_sec": float(windows.get("song_duration_sec", 0.0)),
            "placements": placement_dicts,
            "mixer_cues": build_mixer_cues(placements),
            "generated_at": _now(),
            "generator": "autopilot/dialogue_place",
        }
        out = ctx.project_dir / "data" / "dialogue-placement-plan.json"
        out.write_text(json.dumps(payload, indent=2))
    except Exception as exc:  # noqa: BLE001
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="dialogue_placement",
            status="failed",
            message=f"placement failed: {type(exc).__name__}: {exc}",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    placed = sum(1 for p in placement_dicts if p.get("decision") == "PLACE")
    rejected = sum(1 for p in placement_dicts if p.get("decision") == "REJECT")
    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="dialogue_placement",
        status="ok",
        message=f"placed {placed} / rejected {rejected} / total {len(placement_dicts)}",
        evidence={"placed": placed, "rejected": rejected,
                  "total": len(placement_dicts)},
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_densify_shot_list(ctx: AutopilotContext) -> AutopilotEvent:
    """Fill gaps between sync-point shots so total duration matches song.

    propose_shot_list() emits one shot per drop+downbeat, which leaves the
    timeline mostly empty (229s song → 15 shots → 16s total). qa.duration
    hard-fails, so autopilot never reaches the render stage.

    This step reads the sparse shot-list and inserts "insert"-role filler
    shots in every gap, respecting each act's pacing band. Filler
    source_id mirrors the flanking slot shot (cheap; scene-matching is a
    follow-up).

    Idempotent: detects when a shot-list has already been densified (any
    shot carries `densified=true`) and skips to avoid double-expansion.
    """
    start = time.perf_counter()
    shot_list_path = ctx.project_dir / "data" / "shot-list.json"
    if not shot_list_path.exists():
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="densify_shot_list",
            status="skipped", message="no shot-list.json to densify",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    try:
        from fandomforge.intelligence.shot_proposer import densify_shot_list
        from fandomforge.validation import validate_and_write

        shot_list = json.loads(shot_list_path.read_text(encoding="utf-8"))
        if any(s.get("densified") for s in shot_list.get("shots") or []):
            return AutopilotEvent(
                ts=_now(), run_id=ctx.run_id, step_id="densify_shot_list",
                status="skipped",
                message="shot-list already densified",
                duration_sec=round(time.perf_counter() - start, 3),
            )

        before_count = len(shot_list.get("shots") or [])
        edit_plan_path = ctx.project_dir / "data" / "edit-plan.json"
        edit_plan = None
        if edit_plan_path.exists():
            try:
                edit_plan = json.loads(edit_plan_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                edit_plan = None

        # Beat-map gives us song duration if the shot-list doesn't have it.
        song_duration = shot_list.get("song_duration_sec")
        if not song_duration:
            bm_path = ctx.project_dir / "data" / "beat-map.json"
            if bm_path.exists():
                try:
                    bm = json.loads(bm_path.read_text(encoding="utf-8"))
                    song_duration = bm.get("duration_sec")
                except (OSError, json.JSONDecodeError):
                    song_duration = None

        # Load scene boundaries per source for scene-matched filler picking.
        # Key by catalog's source_id so densify_shot_list can look up by the
        # same id the flanking slot shot references.
        scenes_by_source: dict[str, list[dict[str, Any]]] = {}
        scenes_dir = ctx.project_dir / "data" / "scenes"
        catalog_path = ctx.project_dir / "data" / "source-catalog.json"
        if scenes_dir.exists() and catalog_path.exists():
            try:
                catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                catalog = {"sources": []}
            for entry in (catalog.get("sources") or []):
                path = entry.get("path") or ""
                if not path:
                    continue
                stem = Path(path).stem
                scene_file = scenes_dir / f"{stem}.json"
                if not scene_file.exists():
                    continue
                try:
                    sdata = json.loads(scene_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                scenes = sdata.get("scenes") or []
                if scenes:
                    # shot_proposer emits path-stem source_ids — key scenes
                    # the same way so the filler picker's source rotation
                    # matches the shot-list's source_ids 1:1.
                    scenes_by_source[stem] = scenes

        densified = densify_shot_list(
            shot_list,
            edit_plan=edit_plan,
            song_duration_sec=song_duration,
            scenes_by_source=scenes_by_source or None,
        )
        validate_and_write(densified, "shot-list", shot_list_path)

        # Reconcile edit-plan fps/resolution to the authoritative shot-list
        # values so qa.fps_resolution doesn't block on LLM/stub defaults
        # picking a wrong fps. shot-list is the source of truth here — it
        # was computed from actual source video metadata.
        if edit_plan is not None and edit_plan_path.exists():
            new_fps = densified.get("fps")
            new_res = densified.get("resolution")
            changed = False
            if new_fps is not None and edit_plan.get("fps") != new_fps:
                edit_plan["fps"] = new_fps
                changed = True
            if new_res is not None and edit_plan.get("resolution") != new_res:
                edit_plan["resolution"] = new_res
                changed = True
            if changed:
                validate_and_write(edit_plan, "edit-plan", edit_plan_path)
    except Exception as exc:  # noqa: BLE001
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="densify_shot_list",
            status="failed",
            message=f"densify failed: {type(exc).__name__}: {exc}",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    after_count = len(densified.get("shots") or [])
    total_sec = sum(
        int(s.get("duration_frames", 0)) for s in densified.get("shots") or []
    ) / float(densified.get("fps") or 24.0)
    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="densify_shot_list",
        status="ok",
        message=(
            f"densified {before_count} → {after_count} shots "
            f"(+{after_count - before_count} fillers), total {total_sec:.1f}s"
        ),
        evidence={
            "shots_before": before_count,
            "shots_after": after_count,
            "total_duration_sec": round(total_sec, 2),
        },
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_stamp_color_grade_confidence(ctx: AutopilotContext) -> AutopilotEvent:
    """Phase 3.3 — stamp `color_grade_confidence` into every shot of
    shot-list.json from the source's quality_tier + color_cast. The
    `qa.color_grade_confidence` rule reads this field to warn on shots
    that will likely need a manual Resolve pass."""
    start = time.perf_counter()
    shot_list_path = ctx.project_dir / "data" / "shot-list.json"
    profiles_dir = ctx.project_dir / "data" / "source-profiles"
    if not shot_list_path.exists():
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="stamp_color_grade_confidence",
            status="skipped",
            message="no shot-list.json — nothing to stamp",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    try:
        from fandomforge.intelligence.color_grader import compute_shot_confidence
        from fandomforge.validation import validate_and_write

        shot_list = json.loads(shot_list_path.read_text(encoding="utf-8"))

        # Cache the per-source profile reads so a 217-shot list doesn't hit
        # disk 217 times.
        profile_cache: dict[str, dict[str, Any] | None] = {}

        def _profile_for(source_id: str) -> dict[str, Any] | None:
            if source_id in profile_cache:
                return profile_cache[source_id]
            path = profiles_dir / f"{source_id}.json"
            data: dict[str, Any] | None = None
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    data = None
            profile_cache[source_id] = data
            return data

        stamped = 0
        low_count = 0
        for shot in shot_list.get("shots") or []:
            source_id = shot.get("source_id")
            profile = _profile_for(source_id) if source_id else None
            confidence = compute_shot_confidence(profile)
            shot["color_grade_confidence"] = round(confidence, 3)
            stamped += 1
            if confidence < 0.6:
                low_count += 1

        validate_and_write(shot_list, "shot-list", shot_list_path)
    except Exception as exc:  # noqa: BLE001
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="stamp_color_grade_confidence",
            status="failed",
            message=f"stamp failed: {type(exc).__name__}: {exc}",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="stamp_color_grade_confidence",
        status="ok",
        message=(
            f"stamped color_grade_confidence on {stamped} shot(s); "
            f"{low_count} below 0.6 floor (manual Resolve pass recommended)"
            if stamped else "no shots in list — nothing stamped"
        ),
        evidence={"stamped": stamped, "low_confidence_count": low_count},
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_profile_sources(ctx: AutopilotContext) -> AutopilotEvent:
    """Build a source-profile.json per ingested source.

    Produces the per-source visual fingerprint (luma/chroma histograms,
    color cast, grain, sharpness, AR/fps/resolution) that downstream
    Phase 3 stages (visual signature DB, slot-fit scorer, AR arbiter,
    quality-gap mitigator) all key off. Idempotent — skips sources that
    already have a profile written by the same generator version.
    """
    start = time.perf_counter()
    out_dir = ctx.project_dir / "data" / "source-profiles"
    catalog_path = ctx.project_dir / "data" / "source-catalog.json"

    if not catalog_path.exists():
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="profile_sources",
            status="skipped",
            message="no source-catalog.json yet (skipping profiling)",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    try:
        from fandomforge.intelligence.source_profiler import (
            PROFILER_VERSION,
            profile_source,
            write_source_profile,
        )

        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        profiled = 0
        skipped = 0
        failed = 0
        for entry in catalog.get("sources") or []:
            path_str = entry.get("path")
            if not path_str:
                continue
            path = Path(path_str)
            if not path.exists():
                failed += 1
                continue

            # Use the path stem as the source_id so it joins with shot-list's
            # human-readable source_id (e.g. "extraction-2"). For sources
            # under raw/fights/, prepend "fight_" to match the shot-list
            # convention (the assembly's _find_source_video strips this
            # prefix back when locating the actual file).
            sid = path.stem
            if "fights" in path.parent.name.lower():
                sid = f"fight_{sid}"
            catalog_id = entry.get("id") or entry.get("source_id") or ""

            # Idempotent: skip if a profile already exists at the current
            # generator version.
            existing = out_dir / f"{re.sub(r'[^A-Za-z0-9._-]', '_', sid)}.json"
            if existing.exists():
                try:
                    existing_data = json.loads(existing.read_text(encoding="utf-8"))
                    if existing_data.get("generator", "").endswith(PROFILER_VERSION):
                        skipped += 1
                        continue
                except (json.JSONDecodeError, OSError):
                    pass  # fall through to re-profile

            try:
                profile = profile_source(path, sid, deep=True, n_frames=20)
                if catalog_id:
                    profile["era_label"] = profile.get("era_label") or f"catalog:{catalog_id[:16]}"
                write_source_profile(profile, ctx.project_dir)
                profiled += 1
            except Exception:  # noqa: BLE001 — best-effort per-source
                failed += 1
    except Exception as exc:  # noqa: BLE001
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="profile_sources",
            status="failed",
            message=f"profile_sources failed: {type(exc).__name__}: {exc}",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="profile_sources",
        status="ok",
        message=f"source profiles: {profiled} new, {skipped} skipped, {failed} failed",
        evidence={"out_dir": str(out_dir)},
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_intent(ctx: AutopilotContext) -> AutopilotEvent:
    """Build intent.json from the user prompt + project config + song hint.

    Runs early (before ingest) so downstream stages — edit-plan, arc
    architect, slot-fit, dialogue script — all key off a single resolved
    edit_type, tone vector, speaker list, and target duration. Produces the
    intent.schema.json artifact.
    """
    start = time.perf_counter()
    out = ctx.project_dir / "data" / "intent.json"
    if _artifact_exists_and_valid(ctx, "intent"):
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="intent",
            status="skipped", message="intent.json already valid",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    project_config: dict[str, Any] | None = None
    cfg_path = ctx.project_dir / "project-config.json"
    if cfg_path.exists():
        try:
            project_config = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            project_config = None

    # Song duration for default target_duration_sec
    song_duration_sec: float | None = None
    song_path = _find_song(ctx)
    if song_path is not None:
        try:
            from fandomforge.assembly.mixer import _probe_duration
            d = _probe_duration(song_path)
            if d > 0:
                song_duration_sec = float(d)
        except Exception:  # noqa: BLE001
            song_duration_sec = None

    # Fandom roster for speaker inference
    fandom_roster: list[dict[str, Any]] = []
    if project_config:
        roster = project_config.get("fandoms") or []
        if isinstance(roster, list):
            fandom_roster = [f if isinstance(f, dict) else {"name": str(f)} for f in roster]

    # Source ids for additional speaker evidence
    source_ids: list[str] = []
    sc_path = ctx.project_dir / "data" / "source-catalog.json"
    if sc_path.exists():
        try:
            sc = json.loads(sc_path.read_text(encoding="utf-8"))
            for s in (sc.get("sources") or []):
                sid = s.get("id") or s.get("source_id")
                if sid:
                    source_ids.append(str(sid))
        except (json.JSONDecodeError, OSError):
            source_ids = []

    try:
        from fandomforge.intelligence.intent_classifier import classify_intent
        from fandomforge.validation import validate_and_write

        intent = classify_intent(
            ctx.prompt,
            project_config=project_config,
            song_duration_sec=song_duration_sec,
            fandom_roster=fandom_roster,
            source_ids=source_ids,
        )
        validate_and_write(intent, "intent", out)
    except Exception as exc:  # noqa: BLE001
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="intent",
            status="failed",
            message=f"intent classifier failed: {type(exc).__name__}: {exc}",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    msg = (
        f"intent: {intent['edit_type']} ({intent['edit_type_source']}), "
        f"target={intent['target_duration_sec']}s ({intent['duration_source']}), "
        f"conf={intent['confidence']:.2f}"
        + (" [needs-confirm]" if intent.get("needs_user_confirmation") else "")
    )
    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="intent",
        status="ok", message=msg,
        evidence={"path": str(out), "edit_type": intent["edit_type"]},
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_arc_architect(ctx: AutopilotContext) -> AutopilotEvent:
    """Phase 2.1 — overlay cross-type pacing/tension/arc-role onto the
    edit-plan's acts[]. Idempotent: re-runs whenever an existing edit-plan
    lacks the Phase 2.1 fields. Replaces the post-process inside
    step_edit_plan so it can fire even when edit-plan generation skipped.
    """
    start = time.perf_counter()
    plan_path = ctx.project_dir / "data" / "edit-plan.json"
    intent_path = ctx.project_dir / "data" / "intent.json"
    beat_map_path = ctx.project_dir / "data" / "beat-map.json"

    if not plan_path.exists() or not intent_path.exists():
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="arc_architect",
            status="skipped",
            message="edit-plan.json or intent.json missing",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="arc_architect",
            status="failed",
            message=f"could not read edit-plan or intent: {exc}",
        )

    existing_acts = plan.get("acts") or []
    has_phase2_fields = any(
        "pacing" in a and "tension_target" in a and "arc_role" in a
        for a in existing_acts
    )
    if has_phase2_fields:
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="arc_architect",
            status="skipped",
            message=f"acts already carry Phase 2.1 fields ({len(existing_acts)} acts)",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    try:
        from fandomforge.intelligence.arc_architect import build_acts as _build_acts
        from fandomforge.validation import validate_and_write

        beat_map = None
        if beat_map_path.exists():
            beat_map = json.loads(beat_map_path.read_text(encoding="utf-8"))
        target_duration = float(intent.get("target_duration_sec") or plan.get("length_seconds") or 60.0)
        new_acts = _build_acts(
            intent, beat_map=beat_map, target_duration_sec=target_duration,
        )
        plan["acts"] = new_acts
        validate_and_write(plan, "edit-plan", plan_path)
    except Exception as exc:  # noqa: BLE001
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="arc_architect",
            status="failed",
            message=f"arc_architect failed: {type(exc).__name__}: {exc}",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="arc_architect",
        status="ok",
        message=(
            f"arc_architect overlaid {len(new_acts)} acts with cross-type "
            f"pacing/tension/role for edit_type={intent.get('edit_type')}"
        ),
        evidence={"path": str(plan_path), "act_count": len(new_acts)},
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_zone_classify(ctx: AutopilotContext) -> AutopilotEvent:
    """Build energy-zones.json from the song + beat-map.

    Produces per-250ms band energies, zone labels (low/mid/high/drop/buildup/
    breakdown), and percussive vs sustained transient classification.
    Foundation for downstream pacing + dialogue-window placement.
    """
    start = time.perf_counter()
    out = ctx.project_dir / "data" / "energy-zones.json"
    if _artifact_exists_and_valid(ctx, "energy-zones"):
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="zone_classify",
            status="skipped", message="energy-zones.json already valid",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    song = _find_song(ctx)
    if not song:
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="zone_classify",
            status="failed", message="no song found in assets/",
        )
    beat_map_path = ctx.project_dir / "data" / "beat-map.json"
    beat_map = None
    if beat_map_path.exists():
        try:
            beat_map = json.loads(beat_map_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            beat_map = None

    try:
        from fandomforge.audio.energy_zones import (
            analyze_energy_zones,
            write_energy_zones,
        )
        from fandomforge.validation import validate_and_write

        result = analyze_energy_zones(song, resolution_sec=0.25, beat_map=beat_map)
        payload = {
            "schema_version": result.schema_version,
            "duration_sec": result.duration_sec,
            "sample_rate_hz": result.sample_rate_hz,
            "resolution_sec": result.resolution_sec,
            "zones": [z.to_dict() for z in result.zones],
            "bands": [b.to_dict() for b in result.bands],
            "transients": [t.to_dict() for t in result.transients],
            "generator": result.generator,
        }
        validate_and_write(payload, "energy-zones", out)
    except Exception as exc:  # noqa: BLE001
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="zone_classify",
            status="failed",
            message=f"zone classify failed: {type(exc).__name__}: {exc}",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="zone_classify",
        status="ok",
        message=(
            f"energy-zones.json: {len(payload['zones'])} zones, "
            f"{len(payload['bands'])} band samples, "
            f"{len(payload['transients'])} transients"
        ),
        evidence={"path": str(out)},
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_dialogue_windows(ctx: AutopilotContext) -> AutopilotEvent:
    """Build dialogue-windows.json from energy-zones + beat-map.

    Classifies every 250ms slice as SAFE / RISKY / BLOCKED for dialogue
    placement. If a project has a dialogue.json with cues, also writes
    dialogue-placement-plan.json resolving each cue against the windows.
    """
    start = time.perf_counter()
    out = ctx.project_dir / "data" / "dialogue-windows.json"
    if _artifact_exists_and_valid(ctx, "dialogue-windows"):
        # Even when windows are cached, re-resolve placement plan if the
        # cues changed. Cheap pass — no audio analysis.
        try:
            _maybe_write_placement_plan(ctx, out)
        except Exception:  # noqa: BLE001
            pass
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="dialogue_windows",
            status="skipped", message="dialogue-windows.json already valid",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    energy_zones_path = ctx.project_dir / "data" / "energy-zones.json"
    if not energy_zones_path.exists():
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="dialogue_windows",
            status="failed",
            message="energy-zones.json missing — run zone_classify first",
        )
    beat_map_path = ctx.project_dir / "data" / "beat-map.json"

    try:
        from fandomforge.audio.dialogue_windows import (
            classify_windows,
            write_dialogue_windows,
        )
        from fandomforge.validation import validate_and_write

        energy_zones = json.loads(energy_zones_path.read_text(encoding="utf-8"))
        beat_map = None
        if beat_map_path.exists():
            beat_map = json.loads(beat_map_path.read_text(encoding="utf-8"))
        result = classify_windows(energy_zones, beat_map=beat_map)
        payload = result.to_dict()
        validate_and_write(payload, "dialogue-windows", out)

        _maybe_write_placement_plan(ctx, out)
    except Exception as exc:  # noqa: BLE001
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="dialogue_windows",
            status="failed",
            message=f"dialogue windows failed: {type(exc).__name__}: {exc}",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="dialogue_windows",
        status="ok",
        message=(
            f"dialogue-windows.json: {payload['safe_window_count']} SAFE, "
            f"{payload['risky_window_count']} RISKY, "
            f"{payload['blocked_window_count']} BLOCKED"
        ),
        evidence={"path": str(out)},
        duration_sec=round(time.perf_counter() - start, 3),
    )


def _maybe_write_placement_plan(ctx: AutopilotContext, windows_path: Path) -> None:
    """If a dialogue.json with cues exists, resolve placements and write
    dialogue-placement-plan.json. Silent no-op when no cues are present."""
    from fandomforge.audio.dialogue_windows import (
        DialogueWindow,
        build_placement_plan,
        write_placement_plan,
    )
    from fandomforge.validation import validate_and_write

    # Discover dialogue.json — common locations
    dialogue_candidates = [
        ctx.project_dir / "dialogue" / "dialogue.json",
        ctx.project_dir / "dialogue.json",
        ctx.project_dir / "data" / "dialogue.json",
    ]
    dialogue_path = next((p for p in dialogue_candidates if p.exists()), None)
    if dialogue_path is None:
        return
    try:
        dlg = json.loads(dialogue_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    cues = dlg.get("cues") or []
    if not cues:
        return

    if not windows_path.exists():
        return
    windows_payload = json.loads(windows_path.read_text(encoding="utf-8"))
    windows = [DialogueWindow(
        start_sec=float(w["start_sec"]),
        end_sec=float(w["end_sec"]),
        flag=w["flag"],
        reason_codes=list(w.get("reason_codes") or []),
        min_duration_available_sec=float(w.get("min_duration_available_sec", 0)),
        rms_at_start=float(w.get("rms_at_start", 0)),
        mid_density_at_start=float(w.get("mid_density_at_start", 0)),
    ) for w in windows_payload.get("windows") or []]

    placements = build_placement_plan(cues, windows)
    out = ctx.project_dir / "data" / "dialogue-placement-plan.json"
    payload = {
        "schema_version": 1,
        "project_slug": ctx.project_slug,
        "placements": [p.to_dict() for p in placements],
        "summary": {
            "place": sum(1 for p in placements if p.decision == "PLACE"),
            "shift": sum(1 for p in placements if p.decision == "SHIFT"),
            "reject": sum(1 for p in placements if p.decision == "REJECT"),
        },
        "generator": "ff dialogue windows",
    }
    validate_and_write(payload, "dialogue-placement-plan", out)


def _load_anthropic_key() -> str | None:
    if key := os.environ.get("ANTHROPIC_API_KEY"):
        return key.strip()
    # Try web/.env.local
    env_path = Path(__file__).resolve().parent.parent.parent / "web" / ".env.local"
    if env_path.exists():
        try:
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
    return None


def _heuristic_edit_plan(ctx: AutopilotContext, duration: float) -> dict[str, Any]:
    fandoms = [w.strip() for w in ctx.prompt.split(",") if w.strip()][:4] or ["Fandom A", "Fandom B"]
    act_len = duration / 3.0
    theme = ctx.prompt[:120] or "autopilot draft"
    if len(theme) < 3:
        theme = "autopilot draft"
    one_sentence = (
        f"Autopilot draft from prompt: {ctx.prompt[:160]}"
        if ctx.prompt and len(ctx.prompt) >= 10
        else "Autopilot draft created without a detailed prompt — refine the concept."
    )
    act_defs = [
        ("setup", "establish emotional anchor"),
        ("escalation", "raise stakes and tension"),
        ("resolution", "deliver payoff / catharsis"),
    ]
    acts: list[dict[str, Any]] = []
    for i, (name, goal) in enumerate(act_defs):
        acts.append({
            "number": i + 1,
            "name": name,
            "start_sec": round(act_len * i, 2),
            "end_sec": round(act_len * (i + 1), 2),
            "energy_target": [30, 60, 85][i],
            "emotional_goal": goal,
        })
    return {
        "schema_version": 1,
        "project_slug": ctx.project_slug,
        "concept": {"theme": theme, "one_sentence": one_sentence},
        "song": {"title": "autopilot", "artist": "unknown", "duration_sec": duration},
        "fandoms": [{"name": f} for f in fandoms],
        "vibe": "mixed",
        "length_seconds": round(duration, 2),
        "platform_target": "youtube",
        "acts": acts,
        "generated_at": _now(),
        "generator": "autopilot/stub-v1",
    }


def _tool_input_schema_for_edit_plan() -> dict[str, Any]:
    """Load the edit-plan JSON schema and adapt it as a tool input_schema.

    Anthropic's tool-use validates the model's output against the input_schema
    before returning it, so this is the most reliable way to get
    structurally-valid output. We strip JSON Schema draft annotations that
    aren't part of the tool schema spec.
    """
    from fandomforge.schemas import load_schema

    raw = load_schema("edit-plan")
    # Strip $schema and $id since tool input_schemas don't use them.
    adapted = {k: v for k, v in raw.items() if k not in ("$schema", "$id", "title")}
    return adapted


def _llm_edit_plan(ctx: AutopilotContext, duration: float, api_key: str) -> dict[str, Any] | None:
    """Use real edit-strategist via Anthropic tool-use to draft the plan.

    Uses the edit-plan JSON schema as the tool's input_schema so the model's
    output is structurally constrained. Still validates with Ajv after,
    because tool-use compliance isn't 100% enforced. On validation failure,
    does one repair turn with the specific Ajv errors.

    Returns None on any unrecoverable failure (autopilot then falls back).
    """
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return None

    from fandomforge.validation import validate, ValidationError

    try:
        strategist_path = Path(__file__).resolve().parent.parent.parent / "agents" / "edit-strategist.md"
        strategist_content = strategist_path.read_text() if strategist_path.exists() else ""

        import anthropic as _anthropic
        client = _anthropic.Anthropic(api_key=api_key)

        try:
            tool_schema = _tool_input_schema_for_edit_plan()
        except Exception:  # noqa: BLE001
            tool_schema = None

        tools = (
            [
                {
                    "name": "draft_edit_plan",
                    "description": (
                        "Draft a schema-valid FandomForge edit-plan for the given project. "
                        "Every field you include must conform to the input_schema exactly. "
                        "Do not invent extra fields."
                    ),
                    "input_schema": tool_schema,
                }
            ]
            if tool_schema
            else None
        )

        system = [
            {
                "type": "text",
                "text": f"You are the FandomForge edit-strategist. Follow the prompt below.\n\n{strategist_content}",
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": (
                    "You will draft a schema-valid edit-plan for the user's project. "
                    "Call the draft_edit_plan tool with the complete plan as the tool input. "
                    "Respect every constraint in the input_schema: required fields, enums, "
                    "min/max, and additionalProperties: false (do NOT invent fields). "
                    "Pick 3-5 acts appropriate for the song duration. act.number starts at 1. "
                    "start_sec values must be non-decreasing; the last act.end_sec should match the song duration. "
                    "Energy targets should rise through the edit and peak on the main drop."
                ),
            },
        ]

        user_prompt = (
            f"Project slug: {ctx.project_slug}\n"
            f"User prompt / theme: {ctx.prompt or '(none given)'}\n"
            f"Song duration (seconds): {duration:.1f}\n\n"
            "Call draft_edit_plan with a schema-valid edit-plan."
        )

        plan: dict[str, Any] | None = None
        last_errors: list[str] = []
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]

        for attempt in range(2):
            kwargs = {
                "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929"),
                "max_tokens": 2000,
                "system": system,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = {"type": "tool", "name": "draft_edit_plan"}

            response = client.messages.create(**kwargs)

            candidate: dict[str, Any] | None = None
            for block in response.content:
                if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "draft_edit_plan":
                    candidate = dict(block.input) if isinstance(block.input, dict) else None
                    break
            if candidate is None:
                text = "".join(
                    b.text for b in response.content if getattr(b, "type", None) == "text"
                )
                s = text.find("{")
                e = text.rfind("}")
                if s >= 0 and e > s:
                    try:
                        candidate = json.loads(text[s : e + 1])
                    except json.JSONDecodeError:
                        candidate = None

            if candidate is None:
                return None

            candidate["project_slug"] = ctx.project_slug
            candidate["schema_version"] = 1
            candidate.setdefault("generated_at", _now())
            candidate["generator"] = f"autopilot/edit-strategist-llm-attempt{attempt + 1}"

            try:
                validate(candidate, "edit-plan")
                plan = candidate
                break
            except ValidationError as exc:
                last_errors = [str(f)[:200] for f in exc.failures[:10]]
                # Append a repair prompt
                messages.append({"role": "assistant", "content": response.content})
                tool_use_id = None
                for block in response.content:
                    if getattr(block, "type", None) == "tool_use":
                        tool_use_id = getattr(block, "id", None)
                        break
                if tool_use_id:
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": tool_use_id,
                                    "is_error": True,
                                    "content": (
                                        "The plan you produced failed schema validation. "
                                        "Fix these specific issues and call draft_edit_plan again:\n- "
                                        + "\n- ".join(last_errors)
                                    ),
                                }
                            ],
                        }
                    )
                else:
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "That plan failed schema validation with these errors:\n- "
                                + "\n- ".join(last_errors)
                                + "\n\nCall draft_edit_plan again with every issue fixed."
                            ),
                        }
                    )

        return plan
    except Exception:  # noqa: BLE001
        return None


def step_edit_plan(ctx: AutopilotContext) -> AutopilotEvent:
    """Write edit-plan.json. Uses real edit-strategist LLM when credits available, heuristic otherwise."""
    start = time.perf_counter()
    if _artifact_exists_and_valid(ctx, "edit-plan"):
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="edit_plan_stub",
            status="skipped", message="edit-plan.json already valid",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    beat_map_path = ctx.project_dir / "data" / "beat-map.json"
    duration = 90.0
    if beat_map_path.exists():
        try:
            duration = float(json.loads(beat_map_path.read_text()).get("duration_sec", 90.0))
        except Exception:  # noqa: BLE001
            pass

    source: str = "heuristic"
    api_key = _load_anthropic_key()
    plan: dict[str, Any] | None = None
    if api_key:
        plan = _llm_edit_plan(ctx, duration, api_key)
        if plan is not None:
            source = "llm"

    if plan is None:
        plan = _heuristic_edit_plan(ctx, duration)

    # Phase 2.1: arc-architect post-process — replace whatever acts[] the
    # generator produced with cross-type-aware arc structure (pacing,
    # tension_target, arc_role per act). Reads intent.json if present so
    # type-specific templates kick in.
    try:
        intent_path = ctx.project_dir / "data" / "intent.json"
        if intent_path.exists():
            from fandomforge.intelligence.arc_architect import build_acts as _build_acts
            intent = json.loads(intent_path.read_text(encoding="utf-8"))
            arc_acts = _build_acts(
                intent,
                beat_map=json.loads(beat_map_path.read_text(encoding="utf-8"))
                    if beat_map_path.exists() else None,
                target_duration_sec=duration,
            )
            if arc_acts:
                plan["acts"] = arc_acts
    except Exception:  # noqa: BLE001 — fall back to whatever generator emitted
        pass

    # Validate — if the LLM returned something that doesn't validate, fall back.
    try:
        from fandomforge.validation import validate, ValidationError
        validate(plan, "edit-plan")
    except Exception:  # noqa: BLE001
        plan = _heuristic_edit_plan(ctx, duration)
        source = "heuristic_after_llm_failed_validation"
        try:
            validate(plan, "edit-plan")
        except Exception as exc:  # noqa: BLE001
            return AutopilotEvent(
                ts=_now(), run_id=ctx.run_id, step_id="edit_plan_stub",
                status="failed",
                message=f"both LLM and heuristic edit-plans failed validation: {exc}",
                duration_sec=round(time.perf_counter() - start, 3),
            )

    # project-config.json is the source of truth for platform_target +
    # target_duration_sec + edit_type. LLM edit-strategists frequently drift
    # (picking "shorts" when config says "youtube"), so we hard-override
    # here rather than letting the drift land in qa_gate failures. Silent
    # on match; tagged in evidence when we overrode anything.
    overrides_applied: list[str] = []
    proj_cfg_path = ctx.project_dir / "project-config.json"
    if proj_cfg_path.exists():
        try:
            cfg = json.loads(proj_cfg_path.read_text(encoding="utf-8"))
            for key in ("platform_target", "target_duration_sec"):
                cfg_val = cfg.get(key)
                plan_val = plan.get(key)
                if cfg_val is not None and cfg_val != plan_val:
                    plan[key] = cfg_val
                    overrides_applied.append(f"{key}:{plan_val!r}→{cfg_val!r}")
        except (OSError, json.JSONDecodeError):
            pass

    out = ctx.project_dir / "data" / "edit-plan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=2) + "\n")

    msg = f"edit-plan.json drafted via {source}"
    if overrides_applied:
        msg += f" (project-config overrides: {'; '.join(overrides_applied)})"
    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="edit_plan_stub",
        status="ok",
        message=msg,
        evidence={
            "source": source,
            "fandoms": [f.get("name") for f in plan.get("fandoms", []) if isinstance(f, dict)],
            "acts": len(plan.get("acts", [])),
            "project_config_overrides": overrides_applied,
        },
        duration_sec=round(time.perf_counter() - start, 3),
    )


# Backwards-compat alias used by older DAG references.
step_edit_plan_stub = step_edit_plan


def _write_shot_list_md(shot_list: dict[str, Any], target: Path) -> None:
    """Render a shot-list.json into a shot-list.md that parse_shot_list can read."""
    fps = float(shot_list.get("fps") or 24)
    shots = shot_list.get("shots") or []
    by_act: dict[int, list[dict[str, Any]]] = {}
    for s in shots:
        act = int(s.get("act") or 1)
        by_act.setdefault(act, []).append(s)

    lines: list[str] = [
        f"# Shot list — {shot_list.get('project_slug', '')}",
        "",
        "_Auto-generated by autopilot from shot-list.json._",
        "",
    ]

    def fmt_time(sec: float) -> str:
        m = int(sec // 60)
        s = sec - m * 60
        return f"{m:d}:{s:06.3f}"

    running_num = 0
    for act_num in sorted(by_act):
        lines.append(f"## Act {act_num}")
        lines.append("")
        lines.append(
            "| number | song_time | duration | source_id | source_timestamp | hero | description | mood |"
        )
        lines.append("|---|---|---|---|---|---|---|---|")
        for s in sorted(by_act[act_num], key=lambda x: x.get("start_frame", 0)):
            running_num += 1
            start_sec = float(s.get("start_frame") or 0) / fps
            dur_sec = float(s.get("duration_frames") or 0) / fps
            source_id = str(s.get("source_id") or "").strip()
            source_ts = str(s.get("source_timecode") or "")
            mood_tags = s.get("mood_tags") or []
            mood = ",".join(mood_tags[:3]) if mood_tags else ""
            lines.append(
                f"| {running_num} | {fmt_time(start_sec)} | {dur_sec:.2f} | "
                f"`{source_id}` | {source_ts} |  |  | {mood} |"
            )
        lines.append("")

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def step_propose_shots(ctx: AutopilotContext) -> AutopilotEvent:
    start = time.perf_counter()
    if _artifact_exists_and_valid(ctx, "shot-list"):
        md_out = ctx.project_dir / "shot-list.md"
        if not md_out.exists():
            try:
                shot_list = json.loads((ctx.project_dir / "data" / "shot-list.json").read_text())
                _write_shot_list_md(shot_list, md_out)
            except Exception:  # noqa: BLE001
                pass
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="propose_shots",
            status="skipped", message="shot-list.json already valid",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    from fandomforge.intelligence.shot_proposer import propose_for_project
    from fandomforge.validation import validate, ValidationError

    try:
        draft = propose_for_project(ctx.project_slug, project_root=ctx.project_dir.parent.parent)
        validate(draft, "shot-list")
    except (FileNotFoundError, ValidationError) as exc:
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="propose_shots",
            status="failed", message=f"{type(exc).__name__}: {exc}",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    out = ctx.project_dir / "data" / "shot-list.json"
    out.write_text(json.dumps(draft, indent=2) + "\n")

    # Also emit a .md version for the render commands (ff roughcut / ff export-nle)
    md_out = ctx.project_dir / "shot-list.md"
    _write_shot_list_md(draft, md_out)

    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="propose_shots",
        status="ok",
        message=f"shot-list.json drafted with {len(draft['shots'])} shots (+ .md projection)",
        evidence={"shot_count": len(draft["shots"])},
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_sync_plan(ctx: AutopilotContext) -> AutopilotEvent:
    """Build sync-plan.json + complement-plan.json + sfx-plan.json.

    Pulls in reference priors (when the user has ingested playlists) to bias
    shot pacing toward real fandom-edit patterns.
    """
    start = time.perf_counter()
    data = ctx.project_dir / "data"
    shot_list_path = data / "shot-list.json"
    beat_map_path = data / "beat-map.json"
    if not shot_list_path.exists() or not beat_map_path.exists():
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="sync_plan",
            status="skipped",
            message="need shot-list.json + beat-map.json",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    try:
        from fandomforge.intelligence.sync_planner import build_sync_plan, write_sync_plan
        from fandomforge.intelligence.complement_matcher import (
            build_complement_plan, write_complement_plan,
        )
        from fandomforge.intelligence.sfx_engine import build_sfx_plan, write_sfx_plan
        from fandomforge.intelligence.reference_library import (
            load_per_bucket_priors, load_priors,
        )

        shot_list = json.loads(shot_list_path.read_text())
        beat_map = json.loads(beat_map_path.read_text())
        lyrics_path = data / "song-lyrics.json"
        lyrics = json.loads(lyrics_path.read_text()) if lyrics_path.exists() else None

        # Phase 0.5.3 — try per-bucket priors first (matches edit_type from
        # intent.json), fall back to the global priors when no bucket file
        # exists yet. Backwards compatible: legacy projects keep working.
        priors = None
        intent_path = data / "intent.json"
        if intent_path.exists():
            try:
                intent = json.loads(intent_path.read_text(encoding="utf-8"))
                edit_type = intent.get("edit_type")
                if edit_type:
                    bucket_priors = load_per_bucket_priors(edit_type)
                    if bucket_priors:
                        # Wrap to match the reference-priors envelope shape
                        priors = {
                            "tag": f"bucket:{edit_type}/all",
                            "video_count": bucket_priors.get("video_count", 0),
                            "priors": bucket_priors,
                        }
            except (json.JSONDecodeError, OSError):
                pass
        if priors is None:
            priors = load_priors()

        sync = build_sync_plan(
            project_slug=ctx.project_slug,
            beat_map=beat_map,
            shot_list=shot_list,
            lyrics_transcript=lyrics,
            reference_priors=priors,
        )
        write_sync_plan(sync, ctx.project_dir)

        comp = build_complement_plan(project_slug=ctx.project_slug, shot_list=shot_list)
        write_complement_plan(comp, ctx.project_dir)

        # When complement pairs exist, reorder the shot-list so thrown shots
        # are immediately followed by their received counterparts. The render
        # pipeline reads shot-list.md, so we regenerate it from the reordered
        # JSON to keep the downstream flow honest.
        if comp.get("pairs"):
            from fandomforge.intelligence.complement_matcher import apply_pairs_to_shot_list
            reordered = apply_pairs_to_shot_list(shot_list, comp)
            shot_list_path.write_text(json.dumps(reordered, indent=2) + "\n")
            _write_shot_list_md(reordered, ctx.project_dir / "shot-list.md")
            shot_list = reordered  # downstream steps see the new order

        sfx = build_sfx_plan(
            project_slug=ctx.project_slug,
            shot_list=shot_list,
            beat_map=beat_map,
        )
        write_sfx_plan(sfx, ctx.project_dir)
    except Exception as exc:  # noqa: BLE001
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="sync_plan",
            status="failed",
            message=f"sync plan build failed: {type(exc).__name__}: {exc}",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="sync_plan",
        status="ok",
        message=(
            f"sync: {len(sync['song_points'])} points, "
            f"complement: {len(comp['pairs'])} pairs, "
            f"sfx: {len(sfx['events'])} events"
            + (" — used reference priors" if priors else "")
        ),
        evidence={
            "sync_points": len(sync["song_points"]),
            "complement_pairs": len(comp["pairs"]),
            "sfx_events": len(sfx["events"]),
            "priors_tag": priors.get("tag") if priors else None,
        },
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_emotion_arc(ctx: AutopilotContext) -> AutopilotEvent:
    start = time.perf_counter()
    if _artifact_exists_and_valid(ctx, "emotion-arc"):
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="emotion_arc",
            status="skipped", message="emotion-arc.json already valid",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    from fandomforge.intelligence.emotion_arc import infer_for_project
    from fandomforge.validation import validate, ValidationError

    try:
        arc = infer_for_project(ctx.project_slug, project_root=ctx.project_dir.parent.parent)
        validate(arc, "emotion-arc")
    except (FileNotFoundError, ValidationError) as exc:
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="emotion_arc",
            status="failed", message=f"{type(exc).__name__}: {exc}",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    out = ctx.project_dir / "data" / "emotion-arc.json"
    out.write_text(json.dumps(arc, indent=2) + "\n")
    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="emotion_arc",
        status="ok", message=f"emotion-arc.json written with {len(arc['samples'])} samples",
        evidence={"samples": len(arc["samples"])},
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_qa_gate(ctx: AutopilotContext) -> AutopilotEvent:
    start = time.perf_counter()
    if _use_subprocess():
        rc, stdout, stderr = _run_subproc(
            ["ff", "qa", "gate", "--project", str(ctx.project_dir)],
            cwd=ctx.project_dir.parent.parent,
        )
        status = "ok" if rc == 0 else "failed"
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="qa_gate",
            status=status,
            message=f"ff qa gate exit {rc}",
            evidence={
                "exit_code": rc,
                "stdout_tail": stdout[-800:],
                "stderr_tail": stderr[-400:] if stderr else "",
            },
            duration_sec=round(time.perf_counter() - start, 3),
        )

    try:
        from fandomforge.qa import run_gate

        out = ctx.project_dir / "data" / "qa-report.json"
        report = run_gate(ctx.project_dir, overrides={}, stage="pre-export", write_to=out)
    except Exception as exc:  # noqa: BLE001
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="qa_gate",
            status="failed",
            message=f"qa gate crashed: {type(exc).__name__}: {exc}",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    status = "failed" if report.get("status") == "fail" else "ok"
    failed_rules = [r for r in report.get("rules", []) if r.get("status") == "fail"]
    warned_rules = [r for r in report.get("rules", []) if r.get("status") == "warn"]
    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="qa_gate",
        status=status,
        message=(
            f"qa gate: {report.get('status', 'unknown')} "
            f"({len(failed_rules)} failed, {len(warned_rules)} warned)"
        ),
        evidence={
            "gate_status": report.get("status"),
            "failed": [r.get("id") for r in failed_rules],
            "warned": [r.get("id") for r in warned_rules],
        },
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_post_render_review(ctx: AutopilotContext) -> AutopilotEvent:
    """Grade the rendered edit: technical / visual / audio / structural / shot list."""
    start = time.perf_counter()
    video = ctx.project_dir / "exports" / "graded.mp4"
    if not video.exists():
        # No render produced (e.g. no-sources project). Skip.
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="post_render_review",
            status="skipped", message="no graded.mp4 to review",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    try:
        from fandomforge.review import review_rendered_edit
        report = review_rendered_edit(ctx.project_dir)
    except Exception as exc:  # noqa: BLE001
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="post_render_review",
            status="failed",
            message=f"review crashed: {exc}",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    # Persist for other tools (/api/project/[slug]/review etc. future).
    out = ctx.project_dir / "data" / "post-render-review.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.to_dict(), indent=2) + "\n")

    # Phase 5.1 — psych proxy telemetry. Stored every render, never graded.
    try:
        from fandomforge.intelligence.psych_proxies import build_report, write_report
        psych = build_report(ctx.project_dir, video_path=video)
        write_report(psych, ctx.project_dir)
    except Exception:  # noqa: BLE001 — never block the review on telemetry
        pass

    # Autopilot status: warn/pass → ok, fail → failed (surfaces to user)
    status = "failed" if report.overall_verdict == "fail" else "ok"
    message = (
        f"review: grade [{report.grade}] score {report.score:.0f}/100 "
        f"({report.overall}) — {report.ship_recommendation}"
    )
    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="post_render_review",
        status=status,
        message=message,
        evidence={
            "overall": report.overall,
            "overall_verdict": report.overall_verdict,
            "grade": report.grade,
            "score": report.score,
            "dimensions": [
                {
                    "name": d.name,
                    "verdict": d.verdict,
                    "score": d.score,
                    "findings": d.findings,
                }
                for d in report.dimensions
            ],
            "report_path": str(out),
        },
        duration_sec=round(time.perf_counter() - start, 3),
    )


def _has_real_sources(ctx: AutopilotContext) -> bool:
    raw = ctx.project_dir / "raw"
    if not raw.exists():
        return False
    for pattern in ("*.mp4", "*.mov", "*.mkv", "*.webm"):
        if any(raw.glob(pattern)):
            return True
    return False


def step_roughcut(ctx: AutopilotContext) -> AutopilotEvent:
    start = time.perf_counter()
    exports_dir = ctx.project_dir / "exports"
    rough_path = exports_dir / "roughcut.mp4"
    if rough_path.exists() and rough_path.stat().st_size > 0:
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="roughcut",
            status="skipped", message=f"{rough_path.name} already rendered",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    if not _has_real_sources(ctx):
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="roughcut",
            status="skipped",
            message="no source videos in raw/ — skipping render (shot-list has placeholders only)",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    exports_dir.mkdir(parents=True, exist_ok=True)

    # Pass the song so the mp4 has audio. copy_song landed it in assets/
    # as song.<ext>; the orchestrator's path search handles assets/ vs raw/.
    song = _find_song(ctx)
    song_name = song.name if song is not None else None

    # Auto-wire color-plan.json if the artifact exists — the roughcut step
    # should respect the user's per-source color plan without a manual flag.
    color_plan_name: str | None = None
    color_plan_json = ctx.project_dir / "data" / "color-plan.json"
    if color_plan_json.exists():
        color_plan_name = str(color_plan_json)

    if _use_subprocess():
        song_arg = ["--song", song_name] if song_name else []
        color_plan_arg = ["--color-plan", color_plan_name] if color_plan_name else []
        rc, stdout, stderr = _run_subproc(
            ["ff", "roughcut",
             "--project", ctx.project_slug,
             "--output", str(rough_path),
             *song_arg, *color_plan_arg],
            cwd=ctx.project_dir.parent.parent,
        )
        if rc != 0:
            return AutopilotEvent(
                ts=_now(), run_id=ctx.run_id, step_id="roughcut",
                status="failed",
                message=f"ff roughcut exit {rc}",
                evidence={"stderr": stderr[-800:]},
                duration_sec=round(time.perf_counter() - start, 3),
            )
    else:
        try:
            from fandomforge.assembly import ColorPreset, build_rough_cut

            result = build_rough_cut(
                project_dir=ctx.project_dir,
                shot_list_name="shot-list.md",
                song_filename=song_name,
                dialogue_script_json=None,
                color_preset=ColorPreset.TACTICAL,
                color_plan_json=color_plan_name,
                output_name=rough_path.name,
            )
            if not result.success:
                return AutopilotEvent(
                    ts=_now(), run_id=ctx.run_id, step_id="roughcut",
                    status="failed",
                    message=f"roughcut failed: {result.stderr[:400]}",
                    evidence={"stderr": result.stderr[-800:]},
                    duration_sec=round(time.perf_counter() - start, 3),
                )
        except Exception as exc:  # noqa: BLE001
            return AutopilotEvent(
                ts=_now(), run_id=ctx.run_id, step_id="roughcut",
                status="failed",
                message=f"roughcut crashed: {type(exc).__name__}: {exc}",
                duration_sec=round(time.perf_counter() - start, 3),
            )

    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="roughcut",
        status="ok",
        message=f"roughcut rendered to {rough_path.name}",
        evidence={
            "path": str(rough_path),
            "bytes": rough_path.stat().st_size if rough_path.exists() else 0,
            "color_plan_used": color_plan_name,
        },
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_color(ctx: AutopilotContext) -> AutopilotEvent:
    start = time.perf_counter()
    exports_dir = ctx.project_dir / "exports"
    rough_path = exports_dir / "roughcut.mp4"
    graded_path = exports_dir / "graded.mp4"
    if graded_path.exists() and graded_path.stat().st_size > 0:
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="color",
            status="skipped", message=f"{graded_path.name} already graded",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    if not rough_path.exists():
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="color",
            status="skipped",
            message="no roughcut.mp4 — upstream step did not render (likely no real sources)",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    if _use_subprocess():
        rc, stdout, stderr = _run_subproc(
            ["ff", "color",
             "--project", ctx.project_slug,
             "--input", rough_path.name,
             "--output", graded_path.name],
            cwd=ctx.project_dir.parent.parent,
        )
        if rc != 0:
            return AutopilotEvent(
                ts=_now(), run_id=ctx.run_id, step_id="color",
                status="failed",
                message=f"ff color exit {rc}",
                evidence={"stderr": stderr[-800:]},
                duration_sec=round(time.perf_counter() - start, 3),
            )
    else:
        try:
            from fandomforge.assembly import ColorPreset, apply_base_grade

            result = apply_base_grade(
                rough_path,
                graded_path,
                preset=ColorPreset.TACTICAL,
            )
            if not result.success:
                return AutopilotEvent(
                    ts=_now(), run_id=ctx.run_id, step_id="color",
                    status="failed",
                    message=f"color grade failed: {result.stderr[:400]}",
                    evidence={"stderr": result.stderr[-800:]},
                    duration_sec=round(time.perf_counter() - start, 3),
                )
        except Exception as exc:  # noqa: BLE001
            return AutopilotEvent(
                ts=_now(), run_id=ctx.run_id, step_id="color",
                status="failed",
                message=f"color crashed: {type(exc).__name__}: {exc}",
                duration_sec=round(time.perf_counter() - start, 3),
            )
    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="color",
        status="ok",
        message=f"color grade applied → {graded_path.name}",
        evidence={"path": str(graded_path)},
        duration_sec=round(time.perf_counter() - start, 3),
    )


def step_export(ctx: AutopilotContext) -> AutopilotEvent:
    start = time.perf_counter()
    exports_dir = ctx.project_dir / "exports"
    xml_path = exports_dir / f"{ctx.project_slug}.fcpxml"
    if xml_path.exists() and xml_path.stat().st_size > 0:
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="export",
            status="skipped", message=f"{xml_path.name} already exported",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    if not _has_real_sources(ctx):
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="export",
            status="skipped",
            message="no real sources — NLE XML requires real clip paths, skipping",
            duration_sec=round(time.perf_counter() - start, 3),
        )
    if _use_subprocess():
        rc, stdout, stderr = _run_subproc(
            ["ff", "export-nle",
             "--project", ctx.project_slug,
             "--format", "fcpxml",
             "--output-base", xml_path.stem],
            cwd=ctx.project_dir.parent.parent,
        )
        if rc != 0:
            return AutopilotEvent(
                ts=_now(), run_id=ctx.run_id, step_id="export",
                status="failed",
                message=f"ff export-nle exit {rc}",
                evidence={"stderr": stderr[-800:]},
                duration_sec=round(time.perf_counter() - start, 3),
            )
    else:
        try:
            from fandomforge.assembly import parse_shot_list
            from fandomforge.intelligence.nle_export import (
                export_fcpxml,
                shots_to_clips,
            )

            shot_list_candidates = [
                ctx.project_dir / "shot-list.md",
                ctx.project_dir / "plans" / "shot-list.md",
            ]
            shot_path = next((p for p in shot_list_candidates if p.exists()), None)
            if shot_path is None:
                return AutopilotEvent(
                    ts=_now(), run_id=ctx.run_id, step_id="export",
                    status="failed",
                    message="no shot-list.md found for NLE export",
                    duration_sec=round(time.perf_counter() - start, 3),
                )

            shots = parse_shot_list(shot_path)
            clips = shots_to_clips(shots, ctx.project_dir / "raw")
            if not clips:
                return AutopilotEvent(
                    ts=_now(), run_id=ctx.run_id, step_id="export",
                    status="failed",
                    message="no resolvable clips for NLE export",
                    duration_sec=round(time.perf_counter() - start, 3),
                )
            xml_path.parent.mkdir(parents=True, exist_ok=True)
            export_fcpxml(
                clips,
                xml_path,
                fps=24,
                width=1920,
                height=1080,
                title=f"FandomForge — {ctx.project_slug}",
            )
        except Exception as exc:  # noqa: BLE001
            return AutopilotEvent(
                ts=_now(), run_id=ctx.run_id, step_id="export",
                status="failed",
                message=f"export crashed: {type(exc).__name__}: {exc}",
                duration_sec=round(time.perf_counter() - start, 3),
            )

    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="export",
        status="ok",
        message=f"NLE XML exported → {xml_path.name}",
        evidence={"path": str(xml_path)},
        duration_sec=round(time.perf_counter() - start, 3),
    )


# Complete DAG
STEPS: list[Step] = [
    Step("scaffold", "Scaffold project directories",
         lambda ctx: (ctx.project_dir / "data").exists(),
         step_scaffold),
    Step("copy_song", "Copy song into assets/",
         lambda ctx: ctx.song_path is None or (ctx.project_dir / "assets" / f"song{ctx.song_path.suffix}").exists(),
         step_copy_song),
    Step("intent", "Classify intent (edit_type + tone + speakers + duration)",
         lambda ctx: _artifact_exists_and_valid(ctx, "intent"),
         step_intent),
    Step("ingest_sources", "Ingest source videos",
         lambda _ctx: False,  # re-runs cheaply; its own logic checks what's done
         step_ingest_sources),
    Step("profile_sources", "Build per-source visual profiles",
         lambda ctx: not (ctx.project_dir / "data" / "source-catalog.json").exists(),
         step_profile_sources),
    Step("beat_analyze", "ff beat analyze",
         lambda ctx: _artifact_exists_and_valid(ctx, "beat-map"),
         step_beat_analyze),
    Step("zone_classify", "Classify energy zones + bands + transients",
         lambda ctx: _artifact_exists_and_valid(ctx, "energy-zones"),
         step_zone_classify),
    Step("dialogue_windows", "Detect SAFE/RISKY/BLOCKED dialogue windows",
         lambda ctx: _artifact_exists_and_valid(ctx, "dialogue-windows"),
         step_dialogue_windows),
    Step("edit_plan_stub", "Draft edit-plan (stub)",
         lambda ctx: _artifact_exists_and_valid(ctx, "edit-plan"),
         step_edit_plan_stub),
    Step("arc_architect", "Overlay Phase 2.1 pacing/tension/role onto acts",
         lambda _ctx: False,  # cheap; idempotent (skips when fields present)
         step_arc_architect),
    Step("propose_shots", "Propose shot list",
         lambda ctx: _artifact_exists_and_valid(ctx, "shot-list"),
         step_propose_shots),
    Step("dialogue_script_draft",
         "Phase 6 — draft dialogue script (dialogue_narrative only)",
         lambda ctx: (ctx.project_dir / "data" / "dialogue-script.json").exists(),
         step_dialogue_script_draft),
    Step("dialogue_clip_search",
         "Phase 6 — search transcripts for dialogue candidates",
         lambda ctx: (ctx.project_dir / "data" / "dialogue-candidates.json").exists(),
         step_dialogue_clip_search),
    Step("dialogue_placement",
         "Phase 6 — assign dialogue to SAFE windows",
         lambda ctx: (ctx.project_dir / "data" / "dialogue-placement-plan.json").exists(),
         step_dialogue_placement),
    Step("densify_shot_list",
         "Fill gaps between sync-point shots to cover song duration",
         # Cheap, idempotent (step's own logic skips when already densified).
         lambda _ctx: False,
         step_densify_shot_list),
    Step("extract_clip_metadata", "Enrich shot-list with Phase 1.3 metadata",
         lambda _ctx: False,  # always re-run; cheap + idempotent (only fills missing fields)
         step_extract_clip_metadata),
    Step("stamp_color_grade_confidence",
         "Stamp per-shot color_grade_confidence (Phase 3.3)",
         # Always re-run — cheap, idempotent, stamps from source-profiles.
         lambda _ctx: False,
         step_stamp_color_grade_confidence),
    Step("aspect_normalize", "Plan per-clip aspect-ratio normalization",
         lambda _ctx: False,  # cheap; re-runs whenever shot list changes
         step_aspect_normalize),
    Step("emotion_arc", "Infer emotion arc",
         lambda ctx: _artifact_exists_and_valid(ctx, "emotion-arc"),
         step_emotion_arc),
    Step("tension_curve", "Build tension curve from acts + energy + emotion",
         lambda _ctx: False,  # cheap re-run; produces fresh signal each time
         step_tension_curve),
    Step("sync_plan", "Build sync + complement + SFX plans",
         lambda ctx: (ctx.project_dir / "data" / "sync-plan.json").exists()
                     and (ctx.project_dir / "data" / "sfx-plan.json").exists(),
         step_sync_plan),
    Step("qa_gate", "Run QA gate",
         lambda _ctx: False,  # always re-run QA; its output is its own signal
         step_qa_gate),
    Step("roughcut", "Render rough cut",
         lambda ctx: (ctx.project_dir / "exports" / "roughcut.mp4").exists()
                     or not _has_real_sources(ctx),
         step_roughcut),
    Step("color", "Apply color grade",
         lambda ctx: (ctx.project_dir / "exports" / "graded.mp4").exists()
                     or not (ctx.project_dir / "exports" / "roughcut.mp4").exists(),
         step_color),
    Step("export", "Export NLE XML (FCPXML)",
         lambda ctx: (ctx.project_dir / "exports" / f"{ctx.project_slug}.fcpxml").exists()
                     or not _has_real_sources(ctx),
         step_export),
    Step("post_render_review", "Grade the rendered edit",
         # Always re-run — the review is fast and its output (post-render-review.json)
         # is the canonical shippability signal.
         lambda _ctx: False,
         step_post_render_review),
]


def run_autopilot(
    project_slug: str,
    *,
    song_path: Path | None = None,
    source_glob: str | None = None,
    prompt: str = "",
    project_root: Path | None = None,
    verbose: bool = True,
    run_id: str | None = None,
    steps: list[Step] | None = None,
    from_library: bool = False,
    fandom_mix: dict[str, float] | None = None,
) -> dict[str, Any]:
    root = project_root or Path.cwd()
    project_dir = root / "projects" / project_slug
    run_id = run_id or f"apr_{int(time.time()*1000)}"
    ctx = AutopilotContext(
        run_id=run_id,
        project_slug=project_slug,
        project_dir=project_dir,
        song_path=song_path,
        source_glob=source_glob,
        prompt=prompt,
        verbose=verbose,
        from_library=from_library,
        fandom_mix=fandom_mix,
    )

    project_dir.mkdir(parents=True, exist_ok=True)
    _history_dir(project_dir).mkdir(exist_ok=True)
    _append_event(project_dir, AutopilotEvent(
        ts=_now(), run_id=run_id, step_id="_run",
        status="started", message=f"autopilot run {run_id}",
        evidence={
            "project_slug": project_slug,
            "song": str(song_path) if song_path else None,
            "prompt": prompt[:200],
        },
    ))

    step_results: list[dict[str, Any]] = []
    overall_status = "ok"

    for step in (steps or STEPS):
        if step.check_done(ctx):
            event = AutopilotEvent(
                ts=_now(), run_id=run_id, step_id=step.id,
                status="skipped", message="already done",
            )
            ctx.record(event)
            step_results.append(event.to_dict())
            continue
        ctx.record(AutopilotEvent(
            ts=_now(), run_id=run_id, step_id=step.id,
            status="started", message=step.label,
        ))
        try:
            event = step.run(ctx)
        except Exception as exc:  # noqa: BLE001
            event = AutopilotEvent(
                ts=_now(), run_id=run_id, step_id=step.id,
                status="failed", message=f"{type(exc).__name__}: {exc}",
            )
        ctx.record(event)
        step_results.append(event.to_dict())
        if event.status == "failed":
            overall_status = "failed"
            break

    _append_event(project_dir, AutopilotEvent(
        ts=_now(), run_id=run_id, step_id="_run",
        status="ended", message=f"autopilot run {run_id} ended",
        evidence={"overall_status": overall_status, "steps": len(step_results)},
    ))

    return {
        "run_id": run_id,
        "project_slug": project_slug,
        "overall_status": overall_status,
        "steps": step_results,
    }


def estimate_cost(
    project_slug: str,
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Rough estimate of wall time and token cost before a run starts."""
    root = project_root or Path.cwd()
    project_dir = root / "projects" / project_slug
    raw_dir = project_dir / "raw"
    sources = list(raw_dir.glob("*.mp4")) + list(raw_dir.glob("*.mov")) + list(raw_dir.glob("*.mkv"))
    source_bytes = sum(p.stat().st_size for p in sources if p.exists())

    # Heuristic: 1 MB = 1 sec processing
    est_seconds = max(15, int(source_bytes / 1_000_000))
    est_tokens_in = 2_000 + 500 * len(sources)
    est_tokens_out = 400
    est_cost_usd = (est_tokens_in * 0.003 + est_tokens_out * 0.015) / 1000

    return {
        "project_slug": project_slug,
        "source_count": len(sources),
        "source_bytes": source_bytes,
        "estimated_wall_time_sec": est_seconds,
        "estimated_tokens": {
            "input": est_tokens_in,
            "output": est_tokens_out,
        },
        "estimated_cost_usd": round(est_cost_usd, 3),
        "notes": (
            "Rough heuristic. LLM costs only accrue if an expert-chat step is "
            "enabled (currently uses a heuristic edit-plan stub — $0 for that step)."
        ),
    }


__all__ = [
    "AutopilotContext",
    "AutopilotEvent",
    "Step",
    "STEPS",
    "run_autopilot",
    "estimate_cost",
]
