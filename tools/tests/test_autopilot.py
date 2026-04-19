"""Tests for the autopilot orchestrator.

These tests exercise the DAG unit by unit (check_done semantics, event stream,
idempotent resume) without actually calling ffmpeg/librosa. The full
integration run lives in test_integration_real_media under the
requires_fixtures marker.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fandomforge.autopilot import (
    AutopilotContext, AutopilotEvent, Step, STEPS,
    estimate_cost, run_autopilot,
)


def _make_ctx(tmp_path: Path, **kw) -> AutopilotContext:
    slug = "test_auto"
    project_dir = tmp_path / "projects" / slug
    project_dir.mkdir(parents=True, exist_ok=True)
    return AutopilotContext(
        run_id="test_run",
        project_slug=slug,
        project_dir=project_dir,
        song_path=kw.get("song_path"),
        source_glob=None,
        prompt=kw.get("prompt", "test"),
        verbose=False,
    )


def test_dag_has_expected_step_order():
    ids = [s.id for s in STEPS]
    assert ids[0] == "scaffold"
    assert "qa_gate" in ids
    qa_idx = ids.index("qa_gate")
    render_stages = ids[qa_idx + 1:]
    assert "roughcut" in render_stages
    assert "color" in render_stages
    assert "export" in render_stages
    for earlier in ["beat_analyze", "propose_shots", "emotion_arc"]:
        assert earlier in ids[:qa_idx + 1]


def test_events_are_written_to_journal(tmp_path: Path):
    slug = "simple"
    project_dir = tmp_path / "projects" / slug
    project_dir.mkdir(parents=True)

    # Simple synthetic DAG that doesn't hit disk subprocesses
    def check_done(_ctx): return False
    def run_step(ctx):
        return AutopilotEvent(
            ts="now", run_id=ctx.run_id, step_id="noop",
            status="ok", message="test",
        )

    step = Step("noop", "no-op", check_done, run_step)

    result = run_autopilot(
        slug,
        project_root=tmp_path,
        verbose=False,
        steps=[step],
    )
    assert result["overall_status"] == "ok"

    journal = project_dir / ".history" / "autopilot.jsonl"
    assert journal.exists()
    lines = [json.loads(l) for l in journal.read_text().splitlines() if l.strip()]
    step_ids = [e["step_id"] for e in lines]
    assert "_run" in step_ids  # start + end of run
    assert "noop" in step_ids


def test_failed_step_stops_the_dag(tmp_path: Path):
    slug = "failing"

    def ok_step(ctx):
        return AutopilotEvent(ts="t", run_id=ctx.run_id, step_id="first", status="ok", message="")
    def fail_step(ctx):
        return AutopilotEvent(ts="t", run_id=ctx.run_id, step_id="second", status="failed", message="boom")
    def never_step(ctx):
        return AutopilotEvent(ts="t", run_id=ctx.run_id, step_id="third", status="ok", message="should not run")

    steps = [
        Step("first", "first", lambda _: False, ok_step),
        Step("second", "second", lambda _: False, fail_step),
        Step("third", "third", lambda _: False, never_step),
    ]

    result = run_autopilot(slug, project_root=tmp_path, verbose=False, steps=steps)
    assert result["overall_status"] == "failed"
    step_ids_run = [s["step_id"] for s in result["steps"] if s["status"] != "started"]
    assert "third" not in step_ids_run


def test_skipped_step_when_already_done(tmp_path: Path):
    slug = "resumable"

    ran = {"count": 0}

    def check_true(_ctx): return True
    def never_run(ctx):
        ran["count"] += 1
        return AutopilotEvent(ts="t", run_id=ctx.run_id, step_id="done_step", status="ok", message="")

    step = Step("done_step", "done step", check_true, never_run)

    result = run_autopilot(slug, project_root=tmp_path, verbose=False, steps=[step])
    assert result["overall_status"] == "ok"
    assert ran["count"] == 0  # skipped, never actually ran
    step_event = next(s for s in result["steps"] if s["step_id"] == "done_step")
    assert step_event["status"] == "skipped"


def test_estimate_cost_returns_sane_values(tmp_path: Path):
    slug = "est"
    project_dir = tmp_path / "projects" / slug
    (project_dir / "raw").mkdir(parents=True)
    (project_dir / "raw" / "clip1.mp4").write_bytes(b"x" * 10_000_000)  # 10 MB

    est = estimate_cost(slug, project_root=tmp_path)
    assert est["source_count"] == 1
    assert est["source_bytes"] == 10_000_000
    assert est["estimated_wall_time_sec"] >= 10
    assert est["estimated_cost_usd"] >= 0
    assert "notes" in est


def test_estimate_cost_with_no_sources(tmp_path: Path):
    slug = "empty"
    (tmp_path / "projects" / slug).mkdir(parents=True)
    est = estimate_cost(slug, project_root=tmp_path)
    assert est["source_count"] == 0
    assert est["estimated_wall_time_sec"] >= 15  # floor
