"""Smoke tests for the paste-link web UI.

Verifies that the FastAPI app boots, the home page renders, the summary
and buckets endpoints return real corpus data, and the correction
round-trip persists into the corrections journal + flows through into
``craft_weights_for``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

try:
    from starlette.testclient import TestClient
except ImportError:
    pytest.skip("starlette TestClient unavailable", allow_module_level=True)


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Redirect journal/corrections writes into the tmp path so tests
    # never pollute real ~/.fandomforge or repo-level .cache.
    monkeypatch.setenv("FF_TRAINING_JOURNAL", str(tmp_path / "journal.jsonl"))
    monkeypatch.setenv("FF_CORRECTIONS_JOURNAL", str(tmp_path / "corrections.jsonl"))
    # Also invalidate any cached bias so each test starts clean
    from fandomforge.intelligence import forensic_craft_bias
    forensic_craft_bias.clear_cache()
    from fandomforge.web.server import app
    with TestClient(app) as c:
        yield c


def test_home_returns_html(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "FandomForge" in res.text
    assert "Analyze a video" in res.text


def test_health_endpoint(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "incoming_root" in data
    assert "references_dir" in data


def test_summary_endpoint_shape(client):
    res = client.get("/api/summary")
    assert res.status_code == 200
    data = res.json()
    assert "total_forensics" in data
    assert "per_bucket" in data
    assert "training" in data
    assert "corrections" in data


def test_buckets_endpoint_lists_corpus(client):
    res = client.get("/api/buckets")
    assert res.status_code == 200
    buckets = res.json()
    # If the repo has references/, it should have bucket reports
    if buckets:
        for b in buckets:
            assert "name" in b
            assert "sample_size" in b
            assert "consensus_craft_weights" in b


def test_correction_roundtrip_persists_and_biases(client, monkeypatch):
    # Submit a correction
    payload = {
        "forensic_id": "test-forensic-abc",
        "url": "https://example.com/test",
        "original_bucket": "multifandom",
        "corrected_bucket": "action",
        "corrected_craft_weights": {"ramp": 1.0, "triple_cut": 0.5},
        "notes": "test correction",
    }
    res = client.post("/api/correct", json=payload)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert "action" in body["message"]

    # Verify it landed in the tmp corrections journal
    from fandomforge.intelligence.corrections_journal import (
        iter_corrections,
        latest_correction_for,
    )
    entries = list(iter_corrections())
    assert len(entries) >= 1
    latest = latest_correction_for("test-forensic-abc")
    assert latest is not None
    assert latest.corrected_bucket == "action"
    assert latest.corrected_craft_weights["triple_cut"] == 0.5

    # Verify the bias layer picks it up. triple_cut was 1.0 in the action
    # table; a correction of 0.5 at 40% blend should pull it down noticeably.
    from fandomforge.intelligence.forensic_craft_bias import (
        corrections_suggestion,
        clear_cache,
    )
    clear_cache()
    sugg = corrections_suggestion("action")
    assert sugg is not None
    assert sugg["triple_cut"] == pytest.approx(0.5, abs=1e-3)

    # And through the full stack
    monkeypatch.setenv("FF_FORENSIC_BIAS", "0")
    monkeypatch.setenv("FF_TRAINING_BIAS", "0")
    monkeypatch.setenv("FF_CORRECTIONS_BIAS", "1")
    # Re-import so env vars take effect for this computation path
    from fandomforge.config import craft_weights_for
    weights = craft_weights_for("action")
    # table was 1.0, correction 0.5, blend 40% = 0.6 * 1.0 + 0.4 * 0.5 = 0.80
    assert weights["triple_cut"] == pytest.approx(0.80, abs=1e-2)


def test_correction_bias_disabled_via_env(client, monkeypatch):
    # Seed a correction first
    payload = {
        "forensic_id": "test-forensic-xyz",
        "url": "https://example.com/y",
        "corrected_bucket": "action",
        "corrected_craft_weights": {"ramp": 0.0},
    }
    client.post("/api/correct", json=payload)

    monkeypatch.setenv("FF_CORRECTIONS_BIAS", "0")
    monkeypatch.setenv("FF_FORENSIC_BIAS", "0")
    monkeypatch.setenv("FF_TRAINING_BIAS", "0")
    from fandomforge.intelligence.forensic_craft_bias import clear_cache
    clear_cache()
    from fandomforge.config import craft_weights_for
    weights = craft_weights_for("action")
    # When corrections bias is disabled, action.ramp stays at table value (1.0)
    assert weights["ramp"] == pytest.approx(1.0, abs=1e-3)


def test_unknown_bucket_returns_404(client):
    res = client.get("/api/bucket/does-not-exist-12345")
    assert res.status_code == 404


def test_analyze_endpoint_queues_job(client):
    res = client.post(
        "/api/analyze",
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "bucket_hint": "multifandom"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "job_id" in data
    assert data["status"] in {"queued", "running"}

    # Poll once — should return a snapshot for the known job
    snap = client.get(f"/api/job/{data['job_id']}")
    assert snap.status_code == 200
    snap_data = snap.json()
    assert snap_data["job_id"] == data["job_id"]
    assert snap_data["url"].startswith("https://")


def test_recent_jobs_endpoint(client):
    res = client.get("/api/recent")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_correction_missing_bucket_rejects(client):
    res = client.post(
        "/api/correct",
        json={"forensic_id": "x", "corrected_bucket": ""},
    )
    # FastAPI/pydantic should reject empty corrected_bucket (min_length=1)
    assert res.status_code == 422
