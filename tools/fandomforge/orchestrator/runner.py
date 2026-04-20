"""Main daemon loop — the glue between Queue, ThermalGate, and Handlers.

Execution model:
  while True:
    reading = thermal_gate.poll()
    if emergency → back off, doubled cool-down
    if throttle  → sleep poll_interval, re-check
    if cool      → claim next runnable task, dispatch handler, mark result
  sleep cool_down_sec between tasks

Launched via `ff orchestrator run` or launchd KeepAlive. Ctrl-C / SIGTERM
cleanly requeues whatever was running.
"""

from __future__ import annotations

import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any

from fandomforge.orchestrator.handlers import dispatch
from fandomforge.orchestrator.queue import Queue, Task, TaskStatus, default_queue_path
from fandomforge.orchestrator.thermal import ThermalGate, ThermalState

logger = logging.getLogger(__name__)


class OrchestratorRunner:
    """The daemon loop. Safe to construct even when the queue is empty."""

    def __init__(
        self,
        *,
        queue_path: Path | None = None,
        thermal_gate: ThermalGate | None = None,
        poll_interval_sec: int = 30,
    ) -> None:
        self.queue = Queue(queue_path or default_queue_path())
        self.thermal = thermal_gate or ThermalGate()
        self.poll_interval_sec = poll_interval_sec
        self._shutdown = False
        self._current_task: Task | None = None

    def _install_signal_handlers(self) -> None:
        def _stop(_signum: int, _frame: Any) -> None:
            self._shutdown = True
            # Requeue any in-flight task
            if self._current_task is not None:
                self.queue.mark_thermal_killed(self._current_task.id)
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

    def run_one(self) -> str:
        """Run exactly one iteration of the loop. Used for tests + one-shot invocations.

        Returns a status string:
          "emergency"  — thermal emergency, backed off
          "throttle"   — too warm to start, slept
          "idle"       — no runnable task (empty or all blocked/done)
          "ok:<task.id>"     — completed successfully
          "fail:<task.id>"   — handler returned failure
        """
        reading = self.thermal.poll()
        if reading.state == ThermalState.EMERGENCY:
            logger.warning("thermal EMERGENCY load=%.2f — backing off", reading.load_1m)
            time.sleep(self.thermal.cool_down_sec * 2)
            return "emergency"
        if reading.state == ThermalState.THROTTLE:
            logger.info("thermal THROTTLE load=%.2f — sleeping %ds",
                        reading.load_1m, self.thermal.cool_down_sec)
            time.sleep(self.thermal.cool_down_sec)
            return "throttle"

        task = self.queue.claim_next()
        if task is None:
            logger.info("idle — no runnable tasks")
            time.sleep(self.poll_interval_sec)
            return "idle"

        self._current_task = task
        logger.info("running task id=%s type=%s", task.id, task.type)
        ok, msg, evidence = dispatch(task)
        logger.info("task %s → %s: %s", task.id, "ok" if ok else "fail", msg)
        if ok:
            self.queue.mark_done(task.id)
            self._current_task = None
            return f"ok:{task.id}"
        self.queue.mark_failed(task.id, msg)
        self._current_task = None
        return f"fail:{task.id}"

    def run(self) -> None:
        """Block until shutdown. Poll + dispatch forever."""
        self._install_signal_handlers()
        logger.info("orchestrator starting — queue=%s  gate=(start<%.1f, kill>%.1f)",
                    self.queue.path, self.thermal.start_lt, self.thermal.kill_gt)
        consecutive_idle = 0
        while not self._shutdown:
            status = self.run_one()
            if status == "idle":
                consecutive_idle += 1
                # Long-idle sleep: ramp up to 5 min when queue stays empty,
                # avoid busy-looping on the load check
                if consecutive_idle > 5:
                    time.sleep(min(300, 60 * consecutive_idle))
            else:
                consecutive_idle = 0
                # Successful cool-down between tasks
                if status.startswith("ok:") or status.startswith("fail:"):
                    time.sleep(self.thermal.cool_down_sec)
        logger.info("orchestrator shutdown clean")


def configure_logging(log_path: Path | None = None) -> None:
    """Set up file-backed logging at INFO."""
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers: list[logging.Handler] = []
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path)
        fh.setFormatter(logging.Formatter(fmt))
        handlers.append(fh)
    # Always log to stdout too (launchd captures this)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter(fmt))
    handlers.append(sh)
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in handlers:
        root.addHandler(h)
    root.setLevel(logging.INFO)
