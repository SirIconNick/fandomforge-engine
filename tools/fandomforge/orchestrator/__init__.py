"""Autonomous orchestrator — thermal-gated task queue runner.

Drives the outstanding engine work (whisper lyric alignment, render
verification, code patches, content discovery) without user intervention.
Polls system load, backs off when the machine is hot, resumes when cool,
survives terminal close via launchd.

Entry point: `ff orchestrator run` — see tools/fandomforge/orchestrator/runner.py.
"""

from fandomforge.orchestrator.queue import Queue, Task, TaskStatus
from fandomforge.orchestrator.thermal import ThermalGate, ThermalState
from fandomforge.orchestrator.runner import OrchestratorRunner

__all__ = [
    "Queue",
    "Task",
    "TaskStatus",
    "ThermalGate",
    "ThermalState",
    "OrchestratorRunner",
]
