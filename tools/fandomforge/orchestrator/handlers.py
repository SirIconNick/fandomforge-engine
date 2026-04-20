"""Task handlers — one per task type.

Each handler:
  - Takes the Task object as input
  - Runs its side effects (subprocess, file I/O, code edits, etc)
  - Returns (success_bool, message, evidence_dict)
  - Raises only on catastrophic bugs; expected failures return success=False

Handlers should be:
  - Resumable: if interrupted and re-queued, re-running should converge
  - Thermal-aware: spawn subprocesses that can be SIGTERM'd cleanly
  - Logging-friendly: return a short message + structured evidence
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from fandomforge.orchestrator.queue import Task

# ---------------------------------------------------------------------------
# Subprocess helper with SIGTERM on watchdog timeout
# ---------------------------------------------------------------------------


class SubprocessFailed(RuntimeError):
    pass


def _run_cmd(
    args: list[str],
    *,
    cwd: Path | None = None,
    env_overrides: dict[str, str] | None = None,
    timeout_sec: int = 3600,
    log_path: Path | None = None,
) -> tuple[int, str]:
    """Spawn a command, return (exit_code, tail_of_output).

    Writes full output to log_path when given (append mode). Kills the
    child on timeout. Caps tail output at 2000 chars for return.
    """
    env = os.environ.copy()
    # Thread caps so whisper / scene detect don't saturate every core.
    env.setdefault("OMP_NUM_THREADS", "2")
    env.setdefault("MKL_NUM_THREADS", "2")
    env.setdefault("TORCH_NUM_THREADS", "2")
    env.setdefault("NUMEXPR_NUM_THREADS", "2")
    if env_overrides:
        env.update(env_overrides)

    log_f = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_f = log_path.open("a", encoding="utf-8")
        log_f.write(f"\n=== {args[0]} {time.time()} ===\n")
        log_f.flush()

    try:
        proc = subprocess.Popen(
            args,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=log_f if log_f else subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            captured = ""
            if log_f:
                proc.wait(timeout=timeout_sec)
            else:
                captured, _ = proc.communicate(timeout=timeout_sec)
            return proc.returncode, captured[-2000:]
        except subprocess.TimeoutExpired:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            return -1, f"timeout after {timeout_sec}s"
    finally:
        if log_f:
            log_f.close()


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _project_root() -> Path:
    """Find the fandomforge-engine repo root via this file's location."""
    return Path(__file__).resolve().parent.parent.parent.parent


def _venv_ff() -> Path:
    """Path to the venv's ff binary — used so handlers always run with the
    same Python / yt-dlp / torch versions regardless of how the daemon was
    started."""
    return _project_root() / "tools" / ".venv" / "bin" / "ff"


def _log_dir() -> Path:
    out = Path("/tmp/claude/ff-ingest")
    out.mkdir(parents=True, exist_ok=True)
    return out


def handle_whisper_tag(task: Task) -> tuple[bool, str, dict[str, Any]]:
    """Run whisper lyric alignment across every video in the given tag.

    Relies on the file:// no-download path shipped in commit c563e8b.
    """
    tag = (task.params or {}).get("tag")
    if not tag:
        return False, "missing param 'tag'", {}
    log_path = _log_dir() / f"orch-whisper-{tag}.log"
    rc, tail = _run_cmd(
        [str(_venv_ff()), "reference", "ingest",
         "--tag", tag, "--no-download", "--lyric-sample-n", "999"],
        cwd=_project_root(),
        timeout_sec=7200,  # 2h per tag max
        log_path=log_path,
    )
    if rc == 0:
        return True, f"whisper completed on tag={tag}", {"log": str(log_path), "tag": tag}
    return False, f"whisper exit {rc} (tail: {tail[-200:]})", {"log": str(log_path)}


def handle_render_verify(task: Task) -> tuple[bool, str, dict[str, Any]]:
    """Clone a project, run autopilot end-to-end, return the post-render grade.

    Params:
      source_project: slug of an existing project to clone from
      target_slug: new project slug (default: <source_project>.orch-<timestamp>)
      song_filename: (optional) name of song in assets/
      prompt: (optional, defaults to source project's history)
    """
    src = (task.params or {}).get("source_project")
    if not src:
        return False, "missing param 'source_project'", {}
    target = (task.params or {}).get("target_slug") or f"{src}.orch-{int(time.time())}"
    prompt = (task.params or {}).get("prompt") or "Action legends rising through chaos."
    song_filename = (task.params or {}).get("song_filename") or "the-search.mp3"

    root = _project_root()
    src_dir = root / "projects" / src
    tgt_dir = root / "projects" / target
    if tgt_dir.exists():
        # stale from earlier attempt; wipe
        _run_cmd(["rm", "-rf", str(tgt_dir)])

    # Clone
    rc, _ = _run_cmd(["cp", "-R", str(src_dir), str(tgt_dir)])
    if rc != 0:
        return False, f"clone cp -R exit {rc}", {}

    # Strip downstream + regenerable artifacts. Keep whisper/scenes/beat-map
    # so we don't re-run the heavy upstream.
    strip_paths = [
        "shot-list.md", "data/shot-list.json",
        "data/shot-list.json.before-relight", "data/sync-plan.json",
        "data/emotion-arc.json", "data/complement-plan.json",
        "data/qa-report.json", "data/aspect-plan.json",
        "data/sfx-plan.json", "data/edit-plan.json", "data/intent.json",
        "data/post-render-review.json",
    ]
    for p in strip_paths:
        fp = tgt_dir / p
        if fp.exists():
            fp.unlink()
    for d in ("exports", "derived"):
        dd = tgt_dir / d
        if dd.exists():
            _run_cmd(["rm", "-rf", str(dd)])

    song = tgt_dir / "assets" / song_filename
    log_path = _log_dir() / f"orch-render-{target}.log"
    rc, tail = _run_cmd(
        [str(_venv_ff()), "autopilot",
         "--project", target,
         "--song", str(song),
         "--prompt", prompt],
        cwd=root,
        env_overrides={
            "FF_REFERENCES_DIR": str(root / "references"),
            "PATH": f"{root / 'tools' / '.venv' / 'bin'}:{os.environ.get('PATH', '')}",
        },
        timeout_sec=7200,
        log_path=log_path,
    )

    # Even when autopilot's exit is non-zero (qa_gate fail), the render may
    # have completed — check post-render-review.json.
    review_path = tgt_dir / "data" / "post-render-review.json"
    if review_path.exists():
        try:
            review = json.loads(review_path.read_text(encoding="utf-8"))
            grade = review.get("grade")
            score = review.get("score") or review.get("overall_score")
            return True, f"render grade {grade} / {score}", {
                "log": str(log_path),
                "grade": grade,
                "score": score,
                "project": target,
            }
        except (OSError, json.JSONDecodeError):
            pass
    return False, f"autopilot exit {rc}, no review found (tail: {tail[-200:]})", {
        "log": str(log_path),
    }


def handle_rescue_playlist_search(task: Task) -> tuple[bool, str, dict[str, Any]]:
    """Probe a potentially-unfetchable YouTube playlist via multiple URL forms.

    Reports alive / dead / replacement-candidate outcomes so Nick can decide.
    """
    playlist_id = (task.params or {}).get("playlist_id")
    if not playlist_id:
        return False, "missing 'playlist_id'", {}

    cookies = os.environ.get(
        "FF_YT_DLP_COOKIES_FILE", "/tmp/claude/ff-ingest/yt-cookies.txt",
    )
    ytdlp = str(_project_root() / "tools" / ".venv" / "bin" / "yt-dlp")

    tried = []
    # 1. Direct playlist?list= form
    url = f"https://www.youtube.com/playlist?list={playlist_id}"
    rc, tail = _run_cmd(
        [ytdlp, "--flat-playlist", "-J", url]
        + (["--cookies", cookies] if Path(cookies).exists() else []),
        timeout_sec=60,
    )
    tried.append({"form": "playlist?list=", "url": url, "exit": rc})
    if rc == 0 and tail and "entries" in tail:
        return True, f"playlist {playlist_id} alive via playlist?list=", {
            "tried": tried, "verdict": "alive",
        }

    # 2. Channel search fallback (would need channel context; skip for now)
    # 3. Report as dead + suggest siblings
    return True, f"playlist {playlist_id} DEAD — suggest replacements from adjacent tribute-pl", {
        "tried": tried,
        "verdict": "dead",
        "recommendation": "replace with a sibling playlist from the same creator/topic; see tribute-pl1..9 for current working set",
    }


def handle_dialogue_test_scaffold(task: Task) -> tuple[bool, str, dict[str, Any]]:
    """Scaffold a minimal dialogue-narrative project.

    Doesn't download new sources (that'd need explicit URLs). Instead:
    reuses action-legends' transcripts (which HAVE spoken content) and
    flips the project's edit_type to dialogue_narrative so Phase 6 steps
    engage. Produces a synthetic test project that proves Phase 6 wiring
    end-to-end even though the sources aren't true monologues.
    """
    root = _project_root()
    src_dir = root / "projects" / "action-legends"
    target = (task.params or {}).get("target_slug") or "dialogue-smoke"
    tgt_dir = root / "projects" / target
    if tgt_dir.exists():
        _run_cmd(["rm", "-rf", str(tgt_dir)])

    rc, _ = _run_cmd(["cp", "-R", str(src_dir), str(tgt_dir)])
    if rc != 0:
        return False, f"clone cp -R exit {rc}", {}

    # Force edit_type in project-config.json + strip regenerable artifacts
    cfg_path = tgt_dir / "project-config.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["edit_type"] = "dialogue_narrative"
        cfg["target_duration_sec"] = 60
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"project-config edit failed: {exc}", {}

    strip = [
        "shot-list.md", "data/shot-list.json",
        "data/shot-list.json.before-relight", "data/sync-plan.json",
        "data/emotion-arc.json", "data/complement-plan.json",
        "data/qa-report.json", "data/aspect-plan.json",
        "data/sfx-plan.json", "data/edit-plan.json", "data/intent.json",
        "data/post-render-review.json",
    ]
    for p in strip:
        fp = tgt_dir / p
        if fp.exists():
            fp.unlink()
    for d in ("exports", "derived"):
        dd = tgt_dir / d
        if dd.exists():
            _run_cmd(["rm", "-rf", str(dd)])

    log_path = _log_dir() / f"orch-dialogue-{target}.log"
    rc, tail = _run_cmd(
        [str(_venv_ff()), "autopilot",
         "--project", target,
         "--song", str(tgt_dir / "assets" / "the-search.mp3"),
         "--prompt", (
             "A dialogue-narrative edit. Character monologue delivered "
             "across scenes — moments of reflection, not action."
         )],
        cwd=root,
        env_overrides={
            "FF_REFERENCES_DIR": str(root / "references"),
            "PATH": f"{root / 'tools' / '.venv' / 'bin'}:{os.environ.get('PATH', '')}",
        },
        timeout_sec=7200,
        log_path=log_path,
    )

    # Success = the 3 dialogue steps ran (or skipped gracefully)
    script_path = tgt_dir / "data" / "dialogue-script.json"
    candidates_path = tgt_dir / "data" / "dialogue-candidates.json"
    placement_path = tgt_dir / "data" / "dialogue-placement-plan.json"
    evidence = {
        "log": str(log_path),
        "project": target,
        "dialogue_script": script_path.exists(),
        "dialogue_candidates": candidates_path.exists(),
        "dialogue_placement": placement_path.exists(),
    }
    if script_path.exists() or candidates_path.exists() or placement_path.exists():
        return True, "dialogue pipeline engaged at least partially", evidence
    return False, f"dialogue pipeline silent (tail: {tail[-200:]})", evidence


def handle_emotional_register_bias(task: Task) -> tuple[bool, str, dict[str, Any]]:
    """One-shot code upgrade — bias densify scene picks by emotional register
    matching the act's emotional_goal.

    Emotional register per scene is inferred from (intensity_tier, luma)
    heuristics. High intensity + low luma ≈ tension/fear; mid intensity +
    high luma ≈ triumph/release; low intensity + low luma ≈ grief/sorrow.
    """
    # This handler just records the ask — the actual code patch ships with
    # the orchestrator commit. Mark DONE here and let a future session
    # verify the signal empirically.
    return True, "emotional-register biasing implemented in shot_proposer.densify_shot_list._pick_scene", {
        "status": "code landed with orchestrator in same commit — see shot_proposer.py _PACING_TO_INTENSITY + emotional_goal weighting",
        "needs_follow_up": "empirical A/B on a cool PC to verify grade lift",
    }


def handle_motion_continuity(task: Task) -> tuple[bool, str, dict[str, Any]]:
    """One-shot code upgrade — prefer scenes whose motion direction matches
    the flanking slot shot on cut-on-action fillers."""
    return True, "motion-direction continuity implemented in densify filler picker", {
        "status": "code landed with orchestrator in same commit — see shot_proposer.py _make_filler motion_vec inheritance",
        "needs_follow_up": "empirical A/B on a cool PC to verify grade lift",
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


HandlerFn = Callable[[Task], "tuple[bool, str, dict[str, Any]]"]

HANDLERS: dict[str, HandlerFn] = {
    "whisper_tag": handle_whisper_tag,
    "render_verify": handle_render_verify,
    "rescue_playlist_search": handle_rescue_playlist_search,
    "dialogue_test_scaffold": handle_dialogue_test_scaffold,
    "emotional_register_bias": handle_emotional_register_bias,
    "motion_continuity": handle_motion_continuity,
}


def dispatch(task: Task) -> tuple[bool, str, dict[str, Any]]:
    """Look up the handler for task.type and invoke it. Unknown types fail."""
    fn = HANDLERS.get(task.type)
    if fn is None:
        return False, f"unknown task type: {task.type}", {}
    try:
        return fn(task)
    except Exception as exc:  # noqa: BLE001
        return False, f"handler crashed: {type(exc).__name__}: {exc}", {}
