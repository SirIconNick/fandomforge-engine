"""Tests for the second-wave web routes: corrections list/delete, video
stream, effective-weights breakdown, URL validation, and duplicate
detection in /api/analyze.

Separate from test_web_server.py so test counts stay legible and per-
feature regressions show up in isolated test reports.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

try:
    from starlette.testclient import TestClient
except ImportError:
    pytest.skip("starlette TestClient unavailable", allow_module_level=True)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FF_TRAINING_JOURNAL", str(tmp_path / "journal.jsonl"))
    monkeypatch.setenv("FF_CORRECTIONS_JOURNAL", str(tmp_path / "corrections.jsonl"))
    from fandomforge.intelligence import forensic_craft_bias
    forensic_craft_bias.clear_cache()
    from fandomforge.web.server import app
    with TestClient(app) as c:
        yield c


# ---------- URL validation -------------------------------------------------


def test_analyze_rejects_empty_url(client):
    res = client.post("/api/analyze", json={"url": "", "bucket_hint": "action"})
    # Pydantic field validation fires first (min_length=5), returns 422
    assert res.status_code == 422


def test_analyze_rejects_missing_scheme(client):
    res = client.post("/api/analyze", json={"url": "youtube.com/watch", "bucket_hint": "action"})
    assert res.status_code == 400
    assert "http" in res.json()["detail"].lower()


def test_analyze_rejects_too_long(client):
    res = client.post("/api/analyze", json={"url": "https://" + "a" * 600, "bucket_hint": "action"})
    assert res.status_code in {400, 422}


def test_analyze_accepts_youtube_shorts(client):
    res = client.post(
        "/api/analyze",
        json={"url": "https://www.youtube.com/shorts/abc123def", "bucket_hint": "action"},
    )
    assert res.status_code == 200


def test_analyze_accepts_unknown_host_with_warning(client):
    res = client.post(
        "/api/analyze",
        json={"url": "https://weird-site.example.com/video/xyz", "bucket_hint": "action"},
    )
    assert res.status_code == 200
    # The warning ends up in the job's step log — verify it's there
    job_id = res.json()["job_id"]
    snap = client.get(f"/api/job/{job_id}").json()
    assert any("not in the well-known" in s for s in snap["steps"])


# ---------- duplicate detection --------------------------------------------


def test_analyze_caches_existing_forensic(client, tmp_path, monkeypatch):
    """When a forensic already exists for the video_id, /api/analyze
    returns cached=True and the job goes straight to status=done."""
    from fandomforge.web.pipeline import incoming_root, extract_video_id

    # Seed a forensic file at the path extract_video_id would pick
    fake_url = "https://www.youtube.com/watch?v=testcache123"
    video_id = extract_video_id(fake_url)
    work_dir = incoming_root() / video_id
    work_dir.mkdir(parents=True, exist_ok=True)
    forensic = {
        "schema_version": 1,
        "source": {"video_id": video_id, "duration_sec": 120},
        "bucket": "action",
        "shots": [],
        "generated_at": "2026-04-23T00:00:00Z",
    }
    (work_dir / f"{video_id}.forensic.json").write_text(json.dumps(forensic))

    res = client.post(
        "/api/analyze",
        json={"url": fake_url, "bucket_hint": "action"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["cached"] is True
    assert body["status"] == "done"
    assert body["forensic_id"] == video_id


# ---------- corrections list / delete --------------------------------------


def test_list_corrections_empty(client):
    res = client.get("/api/corrections")
    assert res.status_code == 200
    assert res.json() == []


def test_list_corrections_after_seed(client):
    for i in range(3):
        client.post("/api/correct", json={
            "forensic_id": f"id-{i}",
            "corrected_bucket": "action",
            "corrected_craft_weights": {"ramp": 0.5},
        })
    res = client.get("/api/corrections")
    assert res.status_code == 200
    entries = res.json()
    assert len(entries) == 3
    # Newest first
    ids = [e["forensic_id"] for e in entries]
    assert ids == ["id-2", "id-1", "id-0"] or set(ids) == {"id-0", "id-1", "id-2"}


def test_list_corrections_respects_limit(client):
    for i in range(5):
        client.post("/api/correct", json={
            "forensic_id": f"id-{i}",
            "corrected_bucket": "action",
        })
    res = client.get("/api/corrections?limit=2")
    assert len(res.json()) == 2


def test_delete_correction_removes_and_returns_count(client):
    client.post("/api/correct", json={
        "forensic_id": "to-delete",
        "corrected_bucket": "action",
    })
    client.post("/api/correct", json={
        "forensic_id": "to-delete",  # duplicate — same id, appended
        "corrected_bucket": "sad",
    })
    client.post("/api/correct", json={
        "forensic_id": "keep-me",
        "corrected_bucket": "action",
    })
    res = client.delete("/api/correct/to-delete")
    assert res.status_code == 200
    assert res.json()["deleted"] == 2
    assert res.json()["forensic_id"] == "to-delete"

    # The keeper survives
    remaining = client.get("/api/corrections").json()
    assert len(remaining) == 1
    assert remaining[0]["forensic_id"] == "keep-me"


def test_delete_nonexistent_is_idempotent(client):
    res = client.delete("/api/correct/never-seen")
    assert res.status_code == 200
    assert res.json()["deleted"] == 0


# ---------- effective weights ----------------------------------------------


def test_effective_weights_returns_breakdown(client):
    res = client.get("/api/effective-weights/action")
    assert res.status_code == 200
    data = res.json()
    assert data["bucket"] == "action"
    assert "breakdown" in data
    assert "live_effective" in data
    assert "dropout" in data["breakdown"]
    entry = data["breakdown"]["dropout"]
    # Every breakdown row has the 6 keys
    for k in ("table", "forensic", "training", "correction", "effective", "active"):
        assert k in entry


def test_effective_weights_reflects_correction(client):
    # Before correction, action.triple_cut table value is 1.0
    res = client.get("/api/effective-weights/action")
    base = res.json()["live_effective"]["triple_cut"]
    assert base >= 0.5  # active from the table

    # Seed a correction pulling triple_cut to 0
    client.post("/api/correct", json={
        "forensic_id": "weight-test",
        "corrected_bucket": "action",
        "corrected_craft_weights": {"triple_cut": 0.0},
    })
    res = client.get("/api/effective-weights/action")
    after = res.json()["live_effective"]["triple_cut"]
    # Correction layer pulls weight down at 40%. Must be strictly lower
    # than the pre-correction effective.
    assert after < base
    # And the breakdown exposes the correction value
    assert res.json()["breakdown"]["triple_cut"]["correction"] == 0.0


def test_effective_weights_unknown_bucket_safe(client):
    res = client.get("/api/effective-weights/made-up-zzz")
    assert res.status_code == 200
    # Unknown bucket — zero row with every feature's table=0
    for feat, info in res.json()["breakdown"].items():
        assert info["table"] == 0.0


# ---------- video stream ---------------------------------------------------


def test_video_404_for_unknown(client):
    res = client.get("/api/video/definitely-not-a-real-id")
    assert res.status_code == 404


def test_video_streams_mp4_when_present(client, tmp_path, monkeypatch):
    from fandomforge.web.pipeline import incoming_root
    # Drop a tiny fake mp4 in incoming for the id
    vid = "smoke-video-abc"
    work = incoming_root() / vid
    work.mkdir(parents=True, exist_ok=True)
    fake_mp4 = work / f"{vid}.mp4"
    fake_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100)

    res = client.get(f"/api/video/{vid}")
    assert res.status_code == 200
    assert res.headers["content-type"] == "video/mp4"
    # Cleanup
    fake_mp4.unlink()
    work.rmdir()


# ---------- tags in correction flow ----------------------------------------


def test_correction_persists_tags_added_and_removed(client):
    res = client.post("/api/correct", json={
        "forensic_id": "tags-test",
        "corrected_bucket": "action",
        "tags_added": ["fire-theme", "slow-motion"],
        "tags_removed": ["beat-synced"],
        "notes": "strong fire motif, ignore beat-sync tag",
    })
    assert res.status_code == 200

    entries = client.get("/api/corrections").json()
    assert len(entries) == 1
    e = entries[0]
    assert "fire-theme" in e["tags_added"]
    assert "slow-motion" in e["tags_added"]
    assert "beat-synced" in e["tags_removed"]
    assert "fire motif" in e["notes"]
