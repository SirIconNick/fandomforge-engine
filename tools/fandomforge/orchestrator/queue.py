"""Queue persistence — JSON file at $FF_REFERENCES_DIR/orchestrator-queue.json.

Lock-free single-writer model: only one orchestrator runs at a time
(enforced by launchd KeepAlive). Queue writes are atomic via tmpfile +
rename so a crash mid-write never corrupts the state.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class Task:
    id: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)
    status: str = TaskStatus.PENDING.value
    retries: int = 0
    max_retries: int = 3
    thermal_kills: int = 0
    blocked_by: list[str] = field(default_factory=list)
    last_run_at: str | None = None
    last_error: str | None = None
    added_at: str = field(default_factory=lambda: _now_iso())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Task":
        return cls(
            id=d["id"],
            type=d["type"],
            params=dict(d.get("params") or {}),
            status=d.get("status", TaskStatus.PENDING.value),
            retries=int(d.get("retries", 0)),
            max_retries=int(d.get("max_retries", 3)),
            thermal_kills=int(d.get("thermal_kills", 0)),
            blocked_by=list(d.get("blocked_by") or []),
            last_run_at=d.get("last_run_at"),
            last_error=d.get("last_error"),
            added_at=d.get("added_at") or _now_iso(),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_queue_path() -> Path:
    env = os.environ.get("FF_REFERENCES_DIR")
    root = Path(env) if env else Path.home() / ".fandomforge" / "references"
    return root / "orchestrator-queue.json"


class Queue:
    """JSON-persisted queue. Safe across restarts because all mutating ops
    write to a tmpfile and atomically rename on success."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_queue_path()
        self.tasks: list[Task] = []
        self._load()

    # ---------- persistence ----------

    def _load(self) -> None:
        if not self.path.exists():
            self.tasks = []
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.tasks = []
            return
        self.tasks = [Task.from_dict(t) for t in (payload.get("tasks") or [])]

    def _save(self) -> None:
        payload = {
            "schema_version": 1,
            "generated_at": _now_iso(),
            "tasks": [t.to_dict() for t in self.tasks],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # atomic write
        fd, tmp = tempfile.mkstemp(
            dir=self.path.parent, prefix=self.path.name, suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.write("\n")
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ---------- operations ----------

    def add(self, task: Task) -> None:
        """Append a task. Refuses to add a duplicate id (caller should use
        update() if they want to replace)."""
        if any(t.id == task.id for t in self.tasks):
            raise ValueError(f"task id already exists: {task.id}")
        self.tasks.append(task)
        self._save()

    def extend(self, tasks: list[Task]) -> None:
        """Bulk add. Silently drops duplicates."""
        existing = {t.id for t in self.tasks}
        for t in tasks:
            if t.id in existing:
                continue
            self.tasks.append(t)
            existing.add(t.id)
        self._save()

    def get(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def claim_next(self) -> Task | None:
        """Pick the first pending task whose blocked_by dependencies are
        all DONE. Mark RUNNING and persist.

        Returns None when nothing is runnable (empty / all done / all blocked).
        """
        done_ids = {t.id for t in self.tasks if t.status == TaskStatus.DONE.value}
        for t in self.tasks:
            if t.status != TaskStatus.PENDING.value:
                continue
            if t.blocked_by and not set(t.blocked_by).issubset(done_ids):
                continue
            t.status = TaskStatus.RUNNING.value
            t.last_run_at = _now_iso()
            self._save()
            return t
        return None

    def mark_done(self, task_id: str) -> None:
        t = self.get(task_id)
        if t is None:
            return
        t.status = TaskStatus.DONE.value
        t.last_error = None
        self._save()

    def mark_failed(self, task_id: str, error: str) -> None:
        """Failed tasks get retried until max_retries, then pinned failed."""
        t = self.get(task_id)
        if t is None:
            return
        t.retries += 1
        t.last_error = error[:500]
        if t.retries >= t.max_retries:
            t.status = TaskStatus.FAILED.value
        else:
            t.status = TaskStatus.PENDING.value
        self._save()

    def mark_thermal_killed(self, task_id: str) -> None:
        """Thermal kills don't count against max_retries — the task was fine,
        the machine was just hot. Requeue as pending."""
        t = self.get(task_id)
        if t is None:
            return
        t.thermal_kills += 1
        t.status = TaskStatus.PENDING.value
        self._save()

    def summary(self) -> dict[str, int]:
        out = {s.value: 0 for s in TaskStatus}
        for t in self.tasks:
            out[t.status] = out.get(t.status, 0) + 1
        return out

    def clear_pending(self) -> int:
        """Drop all pending tasks. Keeps done/failed for audit. Returns count removed."""
        before = len(self.tasks)
        self.tasks = [t for t in self.tasks if t.status != TaskStatus.PENDING.value]
        self._save()
        return before - len(self.tasks)
