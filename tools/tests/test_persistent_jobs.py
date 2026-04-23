"""Tests for the SQLite-backed persistent job store."""

from __future__ import annotations

import threading
import time

import pytest

from fandomforge.web.persistent_jobs import SQLiteJobStore, resolve_job_store


@pytest.fixture
def store(tmp_path):
    return SQLiteJobStore(db_path=tmp_path / "jobs.sqlite")


def test_create_and_read_roundtrip(store):
    job = store.create("https://www.youtube.com/watch?v=abc", "action")
    assert job.job_id
    assert job.status == "queued"

    got = store.get(job.job_id)
    assert got is not None
    assert got.url == "https://www.youtube.com/watch?v=abc"
    assert got.bucket_hint == "action"


def test_update_persists_fields(store):
    job = store.create("https://x.com", "sad")
    store.update(job.job_id, status="running", forensic_id="abc-123")
    snap = store.snapshot(job.job_id)
    assert snap["status"] == "running"
    assert snap["forensic_id"] == "abc-123"


def test_update_serializes_forensic_and_analysis(store):
    job = store.create("https://x.com", "action")
    forensic = {"shots": [{"start": 0}], "bucket": "action"}
    analysis = {"bucket": "action", "projected_score": 87.5}
    store.update(job.job_id, forensic=forensic, analysis=analysis)
    snap = store.snapshot(job.job_id)
    assert snap["analysis"] == analysis
    # Get() also returns the deserialized forensic
    got = store.get(job.job_id)
    assert got.forensic == forensic


def test_update_refuses_unknown_status(store):
    job = store.create("https://x.com", "action")
    store.update(job.job_id, status="bogus-status")
    snap = store.snapshot(job.job_id)
    # Stayed queued — the bogus status was rejected
    assert snap["status"] == "queued"


def test_append_step_accumulates(store):
    job = store.create("https://x.com", "action")
    for i in range(5):
        store.append_step(job.job_id, f"step {i}")
    snap = store.snapshot(job.job_id)
    assert len(snap["steps"]) == 5
    assert snap["steps"][0] == "step 0"
    assert snap["steps"][-1] == "step 4"


def test_append_step_caps_at_200(store):
    job = store.create("https://x.com", "action")
    for i in range(250):
        store.append_step(job.job_id, f"step {i}")
    snap = store.snapshot(job.job_id)
    assert len(snap["steps"]) == 200
    # Oldest dropped, newest kept
    assert snap["steps"][-1] == "step 249"
    assert snap["steps"][0] == "step 50"


def test_snapshot_missing_returns_none(store):
    assert store.snapshot("never-created") is None
    assert store.get("never-created") is None


def test_list_recent_newest_first(store):
    ids = []
    for i in range(3):
        j = store.create(f"https://x.com/{i}", "action")
        ids.append(j.job_id)
        time.sleep(0.01)  # ensure monotonic started_at
    recent = store.list_recent()
    assert len(recent) == 3
    # Newest first
    assert recent[0]["job_id"] == ids[-1]
    assert recent[-1]["job_id"] == ids[0]


def test_list_recent_respects_limit(store):
    for i in range(10):
        store.create(f"https://x.com/{i}", "action")
    assert len(store.list_recent(limit=3)) == 3


def test_persistence_across_instances(tmp_path):
    db = tmp_path / "jobs.sqlite"
    s1 = SQLiteJobStore(db_path=db)
    job = s1.create("https://x.com", "action")
    s1.append_step(job.job_id, "step 1")

    # Fresh instance pointing at the same file
    s2 = SQLiteJobStore(db_path=db)
    snap = s2.snapshot(job.job_id)
    assert snap is not None
    assert snap["url"] == "https://x.com"
    assert "step 1" in snap["steps"]


def test_concurrent_writes_dont_corrupt(store):
    """Run 8 threads each creating + updating jobs. Post-run: every job
    should be readable and have the final status we set."""
    def worker(n):
        j = store.create(f"https://x.com/{n}", "action")
        for i in range(20):
            store.append_step(j.job_id, f"t{n}-{i}")
        store.update(j.job_id, status="done", forensic_id=f"fid-{n}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    recent = store.list_recent(limit=100)
    assert len(recent) == 8
    for r in recent:
        assert r["status"] == "done"
        snap = store.snapshot(r["job_id"])
        assert len(snap["steps"]) == 20


def test_resolve_job_store_defaults_to_memory(monkeypatch):
    monkeypatch.delenv("FF_JOB_STORE", raising=False)
    s = resolve_job_store()
    # Memory store has a `_jobs` dict field; SQLite has `_db_path`.
    assert hasattr(s, "_jobs")


def test_resolve_job_store_sqlite(monkeypatch, tmp_path):
    monkeypatch.setenv("FF_JOB_STORE", "sqlite")
    monkeypatch.setenv("FF_JOBS_DB", str(tmp_path / "jobs.sqlite"))
    s = resolve_job_store()
    assert hasattr(s, "_db_path")
