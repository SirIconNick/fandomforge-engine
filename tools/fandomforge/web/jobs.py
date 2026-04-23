"""In-memory job store for the web UI's async analyze endpoint.

The forensic pipeline is slow (download + CLIP + whisper + beat
analysis can take minutes per video). The HTTP client can't block that
long without hitting timeouts and giving zero progress feedback, so the
web layer runs analyses in a background thread and the UI polls a job
status endpoint. This module is the thread-safe dict that holds job
state between the POST that starts work and the GETs that poll it.

Process-local — restarts lose in-flight jobs. That's fine because the
forensic_id is derived from the video URL and writing the forensic JSON
to disk is the real persistence layer.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_STATUSES = {"queued", "running", "done", "failed"}


@dataclass
class Job:
    job_id: str
    url: str
    bucket_hint: str = "multifandom"
    status: str = "queued"
    steps: list[str] = field(default_factory=list)
    forensic_id: str = ""
    forensic: dict[str, Any] | None = None
    analysis: dict[str, Any] | None = None
    error: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, url: str, bucket_hint: str = "multifandom") -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(job_id=job_id, url=url, bucket_hint=bucket_hint)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for k, v in changes.items():
                if k == "status" and v not in _STATUSES:
                    logger.warning("refused unknown status %r for job %s", v, job_id)
                    continue
                setattr(job, k, v)

    def append_step(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.steps.append(message)
            if len(job.steps) > 200:
                job.steps = job.steps[-200:]

    def snapshot(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
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
        with self._lock:
            jobs = sorted(
                self._jobs.values(),
                key=lambda j: j.started_at,
                reverse=True,
            )[:limit]
        return [
            {
                "job_id": j.job_id,
                "url": j.url,
                "status": j.status,
                "forensic_id": j.forensic_id,
                "bucket_hint": j.bucket_hint,
                "started_at": j.started_at,
            }
            for j in jobs
        ]


store = JobStore()
