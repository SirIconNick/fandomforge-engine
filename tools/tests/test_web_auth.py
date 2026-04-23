"""Tests for the FF_API_KEY auth middleware."""

from __future__ import annotations

import os

import pytest

try:
    from starlette.testclient import TestClient
except ImportError:
    pytest.skip("starlette TestClient unavailable", allow_module_level=True)


def _fresh_app():
    """Re-import the server so the app picks up the current env vars."""
    import importlib
    import fandomforge.web.server as server_mod
    importlib.reload(server_mod)
    return server_mod.app


@pytest.fixture(autouse=True)
def _isolate_journals(tmp_path, monkeypatch):
    monkeypatch.setenv("FF_TRAINING_JOURNAL", str(tmp_path / "journal.jsonl"))
    monkeypatch.setenv("FF_CORRECTIONS_JOURNAL", str(tmp_path / "corrections.jsonl"))
    yield


def test_open_access_when_key_unset(monkeypatch):
    monkeypatch.delenv("FF_API_KEY", raising=False)
    app = _fresh_app()
    with TestClient(app) as c:
        assert c.get("/api/buckets").status_code == 200
        assert c.get("/api/summary").status_code == 200


def test_api_requires_key_when_set(monkeypatch):
    monkeypatch.setenv("FF_API_KEY", "test-secret-123")
    app = _fresh_app()
    with TestClient(app) as c:
        # No key → 401
        r = c.get("/api/buckets")
        assert r.status_code == 401
        assert "api key" in r.json()["detail"].lower()


def test_api_accepts_key_via_header(monkeypatch):
    monkeypatch.setenv("FF_API_KEY", "test-secret-123")
    app = _fresh_app()
    with TestClient(app) as c:
        r = c.get("/api/buckets", headers={"X-API-Key": "test-secret-123"})
        assert r.status_code == 200


def test_api_accepts_key_via_query(monkeypatch):
    monkeypatch.setenv("FF_API_KEY", "test-secret-123")
    app = _fresh_app()
    with TestClient(app) as c:
        r = c.get("/api/buckets?api_key=test-secret-123")
        assert r.status_code == 200


def test_wrong_key_rejected(monkeypatch):
    monkeypatch.setenv("FF_API_KEY", "right-key")
    app = _fresh_app()
    with TestClient(app) as c:
        r = c.get("/api/buckets", headers={"X-API-Key": "wrong-key"})
        assert r.status_code == 401


def test_home_and_static_always_open(monkeypatch):
    monkeypatch.setenv("FF_API_KEY", "test-secret")
    app = _fresh_app()
    with TestClient(app) as c:
        assert c.get("/").status_code == 200
        # static files go through the StaticFiles mount
        r = c.get("/static/app.css")
        assert r.status_code in {200, 304}


def test_health_always_open(monkeypatch):
    monkeypatch.setenv("FF_API_KEY", "test-secret")
    app = _fresh_app()
    with TestClient(app) as c:
        r = c.get("/api/health")
        assert r.status_code == 200
        assert r.json()["auth_required"] is True


def test_health_reports_auth_not_required_when_open(monkeypatch):
    monkeypatch.delenv("FF_API_KEY", raising=False)
    app = _fresh_app()
    with TestClient(app) as c:
        r = c.get("/api/health")
        assert r.status_code == 200
        assert r.json()["auth_required"] is False


def test_empty_key_env_var_treated_as_unset(monkeypatch):
    """FF_API_KEY='' should be open-access, not require the empty string."""
    monkeypatch.setenv("FF_API_KEY", "")
    app = _fresh_app()
    with TestClient(app) as c:
        assert c.get("/api/buckets").status_code == 200


def test_post_correct_also_requires_key(monkeypatch):
    monkeypatch.setenv("FF_API_KEY", "lockme")
    app = _fresh_app()
    with TestClient(app) as c:
        r = c.post("/api/correct", json={
            "forensic_id": "x", "corrected_bucket": "action",
        })
        assert r.status_code == 401
        # With key → goes through
        r = c.post(
            "/api/correct",
            json={"forensic_id": "x", "corrected_bucket": "action"},
            headers={"X-API-Key": "lockme"},
        )
        assert r.status_code == 200


def test_delete_correction_requires_key(monkeypatch):
    monkeypatch.setenv("FF_API_KEY", "lockme")
    app = _fresh_app()
    with TestClient(app) as c:
        r = c.delete("/api/correct/some-id")
        assert r.status_code == 401
