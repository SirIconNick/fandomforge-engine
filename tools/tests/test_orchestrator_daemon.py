"""Tests for the autonomous orchestrator daemon —
thermal gate, queue persistence, handler dispatch, runner loop."""

from __future__ import annotations

from pathlib import Path

import pytest

from fandomforge.orchestrator.queue import Queue, Task, TaskStatus
from fandomforge.orchestrator.thermal import ThermalGate, ThermalState
from fandomforge.orchestrator.handlers import dispatch, HANDLERS
from fandomforge.orchestrator.runner import OrchestratorRunner


class TestThermalGate:
    def test_parse_uptime_mac_format(self):
        sample = " 15:24  up 1 day, 21:16, 4 users, load averages: 3.05 3.76 4.39"
        l1, l5, l15 = ThermalGate._parse_uptime(sample)
        assert l1 == 3.05
        assert l5 == 3.76
        assert l15 == 4.39

    def test_parse_uptime_linux_format(self):
        sample = " 03:14:32 up 12 days,  2:34,  3 users,  load average: 0.52, 0.34, 0.20"
        l1, l5, l15 = ThermalGate._parse_uptime(sample)
        assert l1 == 0.52
        assert l5 == 0.34
        assert l15 == 0.20

    def test_parse_uptime_garbage_returns_zeros(self):
        l1, l5, l15 = ThermalGate._parse_uptime("not uptime output")
        assert (l1, l5, l15) == (0.0, 0.0, 0.0)

    def test_state_classification(self, monkeypatch):
        gate = ThermalGate(start_lt=5.0, kill_gt=8.0)
        for load, expected in [
            (0.0, ThermalState.COOL),
            (4.9, ThermalState.COOL),
            (5.01, ThermalState.THROTTLE),
            (7.99, ThermalState.THROTTLE),
            (8.1, ThermalState.EMERGENCY),
            (99.0, ThermalState.EMERGENCY),
        ]:
            monkeypatch.setattr(gate, "_read_load", lambda L=load: (L, L, L))
            reading = gate.poll()
            assert reading.state == expected, (
                f"load={load} expected {expected} got {reading.state}"
            )

    def test_env_override_thresholds(self, monkeypatch):
        monkeypatch.setenv("FF_ORCH_LOAD_START_LT", "3.0")
        monkeypatch.setenv("FF_ORCH_LOAD_KILL_GT", "6.0")
        gate = ThermalGate()
        assert gate.start_lt == 3.0
        assert gate.kill_gt == 6.0


class TestQueue:
    def test_empty_queue_roundtrips(self, tmp_path: Path):
        q = Queue(tmp_path / "q.json")
        assert q.tasks == []
        assert q.claim_next() is None

    def test_add_and_persist(self, tmp_path: Path):
        p = tmp_path / "q.json"
        q = Queue(p)
        q.add(Task(id="t1", type="whisper_tag", params={"tag": "x"}))
        q2 = Queue(p)
        assert len(q2.tasks) == 1
        assert q2.tasks[0].id == "t1"
        assert q2.tasks[0].params == {"tag": "x"}

    def test_claim_next_picks_pending(self, tmp_path: Path):
        q = Queue(tmp_path / "q.json")
        q.add(Task(id="a", type="whisper_tag"))
        q.add(Task(id="b", type="render_verify"))
        picked = q.claim_next()
        assert picked is not None and picked.id == "a"
        assert picked.status == TaskStatus.RUNNING.value
        picked2 = q.claim_next()
        assert picked2 is not None and picked2.id == "b"

    def test_claim_respects_blocked_by(self, tmp_path: Path):
        q = Queue(tmp_path / "q.json")
        q.add(Task(id="a", type="whisper_tag"))
        q.add(Task(id="b", type="render_verify", blocked_by=["a"]))
        picked = q.claim_next()
        assert picked is not None and picked.id == "a"
        assert q.claim_next() is None  # b still blocked (a is RUNNING)
        q.mark_done("a")
        picked2 = q.claim_next()
        assert picked2 is not None and picked2.id == "b"

    def test_failed_task_retries_until_max(self, tmp_path: Path):
        q = Queue(tmp_path / "q.json")
        q.add(Task(id="a", type="whisper_tag", max_retries=2))
        q.claim_next()
        q.mark_failed("a", "err1")
        assert q.get("a").status == TaskStatus.PENDING.value
        assert q.get("a").retries == 1
        q.claim_next()
        q.mark_failed("a", "err2")
        assert q.get("a").status == TaskStatus.FAILED.value

    def test_thermal_kill_no_retry_penalty(self, tmp_path: Path):
        q = Queue(tmp_path / "q.json")
        q.add(Task(id="a", type="whisper_tag", max_retries=1))
        q.claim_next()
        q.mark_thermal_killed("a")
        assert q.get("a").status == TaskStatus.PENDING.value
        assert q.get("a").retries == 0
        assert q.get("a").thermal_kills == 1

    def test_clear_pending_keeps_done(self, tmp_path: Path):
        q = Queue(tmp_path / "q.json")
        q.add(Task(id="a", type="whisper_tag"))
        q.add(Task(id="b", type="render_verify"))
        q.claim_next()
        q.mark_done("a")
        removed = q.clear_pending()
        assert removed == 1
        assert len(q.tasks) == 1
        assert q.tasks[0].id == "a"

    def test_extend_dedup(self, tmp_path: Path):
        q = Queue(tmp_path / "q.json")
        q.add(Task(id="a", type="whisper_tag"))
        q.extend([
            Task(id="a", type="whisper_tag"),
            Task(id="b", type="render_verify"),
        ])
        assert len(q.tasks) == 2
        assert {t.id for t in q.tasks} == {"a", "b"}


class TestHandlerRegistry:
    def test_all_expected_handlers_registered(self):
        required = {
            "whisper_tag", "render_verify", "rescue_playlist_search",
            "dialogue_test_scaffold", "emotional_register_bias",
            "motion_continuity",
        }
        assert required.issubset(set(HANDLERS.keys()))

    def test_unknown_type_returns_false(self):
        t = Task(id="x", type="does_not_exist")
        ok, msg, _ = dispatch(t)
        assert ok is False
        assert "unknown" in msg.lower()

    def test_missing_required_params_returns_false(self):
        t = Task(id="x", type="whisper_tag", params={})
        ok, msg, _ = dispatch(t)
        assert ok is False
        assert "tag" in msg.lower()

    def test_code_upgrade_handlers_idempotent_success(self):
        for ttype in ("emotional_register_bias", "motion_continuity"):
            ok, _, _ = dispatch(Task(id=f"t-{ttype}", type=ttype))
            assert ok is True


class TestRunner:
    def test_run_one_idle_when_queue_empty(self, tmp_path, monkeypatch):
        gate = ThermalGate(start_lt=5.0, kill_gt=8.0)
        monkeypatch.setattr(gate, "_read_load", lambda: (0.0, 0.0, 0.0))
        r = OrchestratorRunner(queue_path=tmp_path / "q.json", thermal_gate=gate)
        monkeypatch.setattr("time.sleep", lambda _s: None)
        assert r.run_one() == "idle"

    def test_run_one_throttles_when_hot(self, tmp_path, monkeypatch):
        gate = ThermalGate(start_lt=5.0, kill_gt=8.0, cool_down_sec=0)
        monkeypatch.setattr(gate, "_read_load", lambda: (6.0, 6.0, 6.0))
        r = OrchestratorRunner(queue_path=tmp_path / "q.json", thermal_gate=gate)
        monkeypatch.setattr("time.sleep", lambda _s: None)
        r.queue.add(Task(id="a", type="whisper_tag", params={"tag": "x"}))
        status = r.run_one()
        assert status == "throttle"
        assert r.queue.get("a").status == TaskStatus.PENDING.value

    def test_run_one_emergency_when_very_hot(self, tmp_path, monkeypatch):
        gate = ThermalGate(start_lt=5.0, kill_gt=8.0, cool_down_sec=0)
        monkeypatch.setattr(gate, "_read_load", lambda: (9.5, 8.5, 7.0))
        r = OrchestratorRunner(queue_path=tmp_path / "q.json", thermal_gate=gate)
        monkeypatch.setattr("time.sleep", lambda _s: None)
        assert r.run_one() == "emergency"

    def test_run_one_runs_successful_task(self, tmp_path, monkeypatch):
        gate = ThermalGate(start_lt=5.0, kill_gt=8.0, cool_down_sec=0)
        monkeypatch.setattr(gate, "_read_load", lambda: (0.5, 0.5, 0.5))
        r = OrchestratorRunner(queue_path=tmp_path / "q.json", thermal_gate=gate)
        monkeypatch.setattr("time.sleep", lambda _s: None)
        r.queue.add(Task(id="cb", type="emotional_register_bias"))
        status = r.run_one()
        assert status == "ok:cb"
        assert r.queue.get("cb").status == TaskStatus.DONE.value
