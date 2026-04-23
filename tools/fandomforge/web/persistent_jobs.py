"""SQLite-backed job store — drop-in replacement for the in-memory
JobStore when ``FF_JOB_STORE=sqlite`` (or the server is running public).

The in-memory store in ``jobs.py`` loses state on process restart, which
is fine for local dev but a bad UX when your laptop sleeps briefly mid-
analysis or the cloudflared tunnel reconnects and takes uvicorn with it.

This store matches the in-memory store's public surface:
* ``.create(url, bucket_hint) → Job``
* ``.get(job_id) → Job | None``
* ``.update(job_id, **changes) → None``
* ``.append_step(job_id, message) → None``
* ``.snapshot(job_id) → dict | None``
* ``.list_recent(limit) → list[dict]``

Steps are stored as a JSON array in the ``steps`` column; every append
rewrites the column. Acceptable because: step count caps at 200 per job,
appends are infrequent (dozens per job), and SQLite's WAL mode makes
these writes fast and crash-safe.

Location: ``<repo>/.cache/ff/jobs.sqlite`` (override with
``FF_JOBS_DB``).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import fields as _fields
from pathlib import Path
from typing import Any

from fandomforge.web.jobs import Job

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    bucket_hint TEXT NOT NULL,
    status TEXT NOT NULL,
    steps TEXT NOT NULL DEFAULT '[]',
    forensic_id TEXT NOT NULL DEFAULT '',
    forensic TEXT,
    analysis TEXT,
    error TEXT NOT NULL DEFAULT '',
    started_at REAL NOT NULL,
    finished_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_started ON jobs(started_at DESC);
"""

_STATUSES = {"queued", "running", "done", "failed"}


def _resolve_db_path() -> Path:
    override = os.environ.get("FF_JOBS_DB", "").strip()
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent / ".cache" / "ff" / "jobs.sqlite"
    return Path.cwd() / ".cache" / "ff" / "jobs.sqlite"


class SQLiteJobStore:
    """Thread-safe SQLite-backed job store."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _resolve_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._local = threading.local()
        with self._conn() as c:
            c.executescript(_SCHEMA)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")

    def _conn(self) -> sqlite3.Connection:
        """Per-thread connection — sqlite3 objects aren't thread-safe
        across threads by default."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self._db_path),
                timeout=10.0,
                isolation_level=None,  # autocommit
            )
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    # ---------- write path ------------------------------------------------

    def create(self, url: str, bucket_hint: str = "multifandom") -> Job:
        job_id = uuid.uuid4().hex[:12]
        now = time.time()
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO jobs (job_id, url, bucket_hint, status, started_at) "
                "VALUES (?, ?, ?, 'queued', ?)",
                (job_id, url, bucket_hint, now),
            )
        return Job(
            job_id=job_id,
            url=url,
            bucket_hint=bucket_hint,
            status="queued",
            started_at=now,
        )

    def update(self, job_id: str, **changes: Any) -> None:
        if not changes:
            return
        sets: list[str] = []
        values: list[Any] = []
        for k, v in changes.items():
            if k == "status" and v not in _STATUSES:
                logger.warning("refused unknown status %r for job %s", v, job_id)
                continue
            if k in ("forensic", "analysis") and v is not None:
                v = json.dumps(v, ensure_ascii=False)
            sets.append(f"{k} = ?")
            values.append(v)
        if not sets:
            return
        values.append(job_id)
        with self._lock, self._conn() as c:
            c.execute(
                f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = ?",
                values,
            )

    def append_step(self, job_id: str, message: str) -> None:
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT steps FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return
            try:
                steps = json.loads(row["steps"])
            except json.JSONDecodeError:
                steps = []
            steps.append(message)
            if len(steps) > 200:
                steps = steps[-200:]
            c.execute(
                "UPDATE jobs SET steps = ? WHERE job_id = ?",
                (json.dumps(steps, ensure_ascii=False), job_id),
            )

    # ---------- read path -------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._row_to_job(row) if row else None

    def snapshot(self, job_id: str) -> dict[str, Any] | None:
        job = self.get(job_id)
        if job is None:
            return None
        return {
            "job_id": job.job_id,
            "url": job.url,
            "bucket_hint": job.bucket_hint,
            "status": job.status,
            "steps": list(job.steps),
            "forensic_id": job.forensic_id,
            "analysis": job.analysis,
            "error": job.error,
            "elapsed_sec": round(
                (job.finished_at or time.time()) - job.started_at, 2
            ),
        }

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._conn() as c:
            rows = c.execute(
                "SELECT job_id, url, bucket_hint, status, forensic_id, started_at "
                "FROM jobs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "job_id": r["job_id"],
                "url": r["url"],
                "status": r["status"],
                "forensic_id": r["forensic_id"],
                "bucket_hint": r["bucket_hint"],
                "started_at": r["started_at"],
            }
            for r in rows
        ]

    # ---------- helpers ---------------------------------------------------

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        steps: list[str] = []
        try:
            steps = json.loads(row["steps"]) or []
        except (json.JSONDecodeError, KeyError):
            pass
        forensic = _maybe_json(row["forensic"])
        analysis = _maybe_json(row["analysis"])
        return Job(
            job_id=row["job_id"],
            url=row["url"],
            bucket_hint=row["bucket_hint"],
            status=row["status"],
            steps=steps,
            forensic_id=row["forensic_id"] or "",
            forensic=forensic,
            analysis=analysis,
            error=row["error"] or "",
            started_at=row["started_at"],
            finished_at=row["finished_at"] or 0.0,
        )


def _maybe_json(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def resolve_job_store():
    """Pick the job store backend from ``FF_JOB_STORE``.

    * ``sqlite`` (default when FF_JOB_STORE is set) → SQLiteJobStore
    * anything else / unset → in-memory (fast, process-local)
    """
    backend = os.environ.get("FF_JOB_STORE", "").lower().strip()
    if backend == "sqlite":
        return SQLiteJobStore()
    from fandomforge.web.jobs import store as memory_store
    return memory_store
