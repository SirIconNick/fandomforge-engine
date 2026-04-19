"""Tests for the webhook dispatcher."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fandomforge.integrations.webhooks import fire


def _write_config(tmp_path: Path, slug: str, endpoints: list[dict]) -> None:
    proj = tmp_path / "projects" / slug
    proj.mkdir(parents=True)
    (proj / "webhooks.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_slug": slug,
                "endpoints": endpoints,
            }
        )
    )


def test_fire_returns_empty_when_no_config(tmp_path: Path):
    (tmp_path / "projects" / "empty").mkdir(parents=True)
    results = fire("pipeline.started", project_slug="empty", payload={}, project_root=tmp_path)
    assert results == []


def test_fire_dispatches_to_matching_endpoint(tmp_path: Path):
    slug = "p1"
    _write_config(tmp_path, slug, [
        {
            "id": "ep1",
            "url": "http://example.test/hook",
            "events": ["pipeline.started", "qa.gate.failed"],
            "enabled": True,
            "secret": "test-secret",
        }
    ])
    posted_requests = []

    class FakeResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass

    def fake_urlopen(req, timeout):  # noqa: ARG001
        posted_requests.append(req)
        return FakeResponse()

    with patch("urllib.request.urlopen", fake_urlopen):
        results = fire(
            "pipeline.started",
            project_slug=slug,
            payload={"run_id": "r1"},
            project_root=tmp_path,
        )

    assert len(results) == 1
    assert results[0].status == "sent"
    assert results[0].http_status == 200
    assert len(posted_requests) == 1
    req = posted_requests[0]
    sig = req.get_header("X-fandomforge-signature")
    assert sig and sig.startswith("sha256=")
    expected = "sha256=" + hmac.new(
        b"test-secret", req.data, hashlib.sha256
    ).hexdigest()
    assert sig == expected


def test_fire_skips_disabled_endpoint(tmp_path: Path):
    slug = "p2"
    _write_config(tmp_path, slug, [
        {"id": "off", "url": "http://x", "events": ["pipeline.started"], "enabled": False},
    ])
    results = fire("pipeline.started", project_slug=slug, payload={}, project_root=tmp_path)
    assert len(results) == 1
    assert results[0].status == "skipped"


def test_fire_skips_endpoints_not_subscribed_to_event(tmp_path: Path):
    slug = "p3"
    _write_config(tmp_path, slug, [
        {"id": "ep", "url": "http://x", "events": ["qa.gate.failed"], "enabled": True},
    ])
    results = fire("pipeline.started", project_slug=slug, payload={}, project_root=tmp_path)
    assert results == []


def test_fire_handles_http_error(tmp_path: Path):
    slug = "p4"
    _write_config(tmp_path, slug, [
        {"id": "ep", "url": "http://x", "events": ["pipeline.started"], "enabled": True},
    ])
    import urllib.error
    def fake_urlopen(req, timeout):  # noqa: ARG001
        raise urllib.error.HTTPError("http://x", 500, "boom", {}, None)

    with patch("urllib.request.urlopen", fake_urlopen):
        results = fire("pipeline.started", project_slug=slug, payload={}, project_root=tmp_path)
    assert len(results) == 1
    assert results[0].status == "failed"
    assert results[0].http_status == 500
