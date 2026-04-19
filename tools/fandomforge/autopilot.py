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
    catalog = ctx.project_dir / "data" / "catalog.json"
    if not raw.exists():
        return AutopilotEvent(
            ts=_now(), run_id=ctx.run_id, step_id="ingest_sources",
            status="skipped", message="no raw/ folder yet",
            duration_sec=round(time.perf_counter() - start, 3),
        )

    videos: list[Path] = []
    for pattern in ("*.mp4", "*.mov", "*.mkv", "*.webm"):
        videos.extend(raw.glob(pattern))

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
    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="beat_analyze",
        status="ok", message="beat-map.json written",
        evidence={"path": str(out)},
        duration_sec=round(time.perf_counter() - start, 3),
    )


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

    out = ctx.project_dir / "data" / "edit-plan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=2) + "\n")

    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="edit_plan_stub",
        status="ok",
        message=f"edit-plan.json drafted via {source}",
        evidence={
            "source": source,
            "fandoms": [f.get("name") for f in plan.get("fandoms", []) if isinstance(f, dict)],
            "acts": len(plan.get("acts", [])),
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
    rc, stdout, stderr = _run_subproc(
        ["ff", "qa", "gate", "--project", ctx.project_slug],
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
    song_arg: list[str] = []
    song = _find_song(ctx)
    if song is not None:
        song_arg = ["--song", song.name]

    rc, stdout, stderr = _run_subproc(
        ["ff", "roughcut",
         "--project", ctx.project_slug,
         "--output", str(rough_path),
         *song_arg],
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
    return AutopilotEvent(
        ts=_now(), run_id=ctx.run_id, step_id="roughcut",
        status="ok",
        message=f"roughcut rendered to {rough_path.name}",
        evidence={
            "path": str(rough_path),
            "bytes": rough_path.stat().st_size if rough_path.exists() else 0,
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
    rc, stdout, stderr = _run_subproc(
        ["ff", "color",
         "--project", ctx.project_slug,
         "--input", str(rough_path),
         "--output", str(graded_path)],
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
    rc, stdout, stderr = _run_subproc(
        ["ff", "export-nle",
         "--project", ctx.project_slug,
         "--format", "fcpxml",
         "--output-base", str(xml_path.with_suffix(""))],
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
    Step("ingest_sources", "Ingest source videos",
         lambda _ctx: False,  # re-runs cheaply; its own logic checks what's done
         step_ingest_sources),
    Step("beat_analyze", "ff beat analyze",
         lambda ctx: _artifact_exists_and_valid(ctx, "beat-map"),
         step_beat_analyze),
    Step("edit_plan_stub", "Draft edit-plan (stub)",
         lambda ctx: _artifact_exists_and_valid(ctx, "edit-plan"),
         step_edit_plan_stub),
    Step("propose_shots", "Propose shot list",
         lambda ctx: _artifact_exists_and_valid(ctx, "shot-list"),
         step_propose_shots),
    Step("emotion_arc", "Infer emotion arc",
         lambda ctx: _artifact_exists_and_valid(ctx, "emotion-arc"),
         step_emotion_arc),
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
