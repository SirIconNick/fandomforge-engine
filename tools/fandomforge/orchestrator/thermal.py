"""Thermal gate — reads macOS load averages via `uptime` and classifies
the current system state into one of three zones:

    COOL       — start or continue work
    THROTTLE   — don't start new tasks; sleep and re-check
    EMERGENCY  — kill any running child, back off, double the next cool-down

Uses load avg as a proxy for real CPU temp. Real thermal sensors on Apple
Silicon need SMC access which requires sudo or osx-cpu-temp; load avg is
a good-enough signal for a user-space daemon that just needs to play nice.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from enum import Enum


class ThermalState(str, Enum):
    COOL = "cool"
    THROTTLE = "throttle"
    EMERGENCY = "emergency"


@dataclass
class ThermalReading:
    load_1m: float
    load_5m: float
    load_15m: float
    state: ThermalState


# Default thresholds. Tunable via env or constructor kwargs.
DEFAULT_START_LT = 5.0
DEFAULT_KILL_GT = 8.0
DEFAULT_COOL_DOWN_SEC = 120


class ThermalGate:
    """Polls uptime load avg. start_lt and kill_gt are the two thresholds.

    Usage:
        gate = ThermalGate()
        state = gate.poll().state
        if state == ThermalState.COOL:
            ... run task ...
    """

    def __init__(
        self,
        *,
        start_lt: float | None = None,
        kill_gt: float | None = None,
        cool_down_sec: int | None = None,
    ) -> None:
        self.start_lt = float(
            start_lt
            if start_lt is not None
            else os.environ.get("FF_ORCH_LOAD_START_LT", DEFAULT_START_LT)
        )
        self.kill_gt = float(
            kill_gt
            if kill_gt is not None
            else os.environ.get("FF_ORCH_LOAD_KILL_GT", DEFAULT_KILL_GT)
        )
        self.cool_down_sec = int(
            cool_down_sec
            if cool_down_sec is not None
            else os.environ.get("FF_ORCH_COOL_DOWN_SEC", DEFAULT_COOL_DOWN_SEC)
        )

    @staticmethod
    def _parse_uptime(out: str) -> tuple[float, float, float]:
        """Pull the three load averages out of `uptime`'s output.
        Format varies by locale; the three trailing floats are always there."""
        m = re.search(r"load averages?:\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)", out)
        if not m:
            return 0.0, 0.0, 0.0
        return float(m.group(1)), float(m.group(2)), float(m.group(3))

    def _read_load(self) -> tuple[float, float, float]:
        try:
            proc = subprocess.run(
                ["uptime"], capture_output=True, text=True, check=True, timeout=5,
            )
            return self._parse_uptime(proc.stdout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            # Fallback to zeros so orchestrator keeps running rather than bricking
            # on an unreadable load signal.
            return 0.0, 0.0, 0.0

    def poll(self) -> ThermalReading:
        """One-shot read. Maps load_1m into a ThermalState zone."""
        l1, l5, l15 = self._read_load()
        if l1 > self.kill_gt:
            state = ThermalState.EMERGENCY
        elif l1 > self.start_lt:
            state = ThermalState.THROTTLE
        else:
            state = ThermalState.COOL
        return ThermalReading(load_1m=l1, load_5m=l5, load_15m=l15, state=state)

    def wait_until_cool(self, *, max_wait_sec: int = 7200, poll_sec: int = 30) -> ThermalReading:
        """Block until the gate is COOL, capped by max_wait_sec. Returns the
        latest reading (may still be hot if we timed out)."""
        started = time.time()
        while True:
            r = self.poll()
            if r.state == ThermalState.COOL:
                return r
            if time.time() - started >= max_wait_sec:
                return r
            time.sleep(poll_sec)
