"""Integration tests for the ingest pipeline.

These tests synthesize a tiny video with ffmpeg and run the full ingest. They
are gated on ffmpeg being available — skipped otherwise. They exercise the
real code path: schema-validated writes, source-catalog upserts, cached reruns.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from fandomforge.ingest import ingest_source
from fandomforge.validation import validate


FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg binary required for ingest tests")


def _make_test_video(path: Path, duration: int = 5) -> None:
    """Synthesize a tiny video with an audible tone so ingest has something to
    probe, transcribe, and embed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc2=duration={duration}:size=320x180:rate=24",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-c:a", "aac", "-b:a", "96k",
        "-shortest",
        str(path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_ingest_probe_only(tmp_path: Path) -> None:
    """With every ML step disabled, ingest still probes media and writes
    source-catalog.json that validates against the schema."""
    video = tmp_path / "raw" / "clip.mp4"
    _make_test_video(video)

    project = tmp_path / "project"
    report = ingest_source(
        video_path=video,
        project_dir=project,
        fandom="Test",
        title="Synth",
        year=2026,
        run_transcript=False,
        run_scenes=False,
        run_clip=False,
        run_characters=False,
    )
    assert report.succeeded
    catalog = json.loads((project / "data" / "source-catalog.json").read_text())
    validate(catalog, "source-catalog")
    assert len(catalog["sources"]) == 1
    src = catalog["sources"][0]
    assert src["fandom"] == "Test"
    assert src["title"] == "Synth"
    assert src["media"]["has_audio"] is True
    assert src["media"]["width"] == 320
    assert src["media"]["height"] == 180


def test_ingest_scenes_writes_valid_artifact(tmp_path: Path) -> None:
    try:
        import scenedetect  # type: ignore  # noqa: F401
    except ImportError:
        pytest.skip("scenedetect not installed")

    video = tmp_path / "raw" / "clip.mp4"
    _make_test_video(video, duration=6)
    project = tmp_path / "project"

    report = ingest_source(
        video_path=video,
        project_dir=project,
        fandom="Test",
        run_transcript=False,
        run_scenes=True,
        run_clip=False,
        run_characters=False,
    )
    assert report.succeeded
    derived = list((project / "derived").iterdir())
    assert derived
    scenes_path = derived[0] / "scenes.json"
    assert scenes_path.exists()
    data = json.loads(scenes_path.read_text())
    validate(data, "scenes")
    assert data["detector"] == "adaptive"


def test_ingest_upsert_replaces_same_source(tmp_path: Path) -> None:
    """Re-ingesting the same file upserts — no duplicate catalog entries."""
    video = tmp_path / "raw" / "clip.mp4"
    _make_test_video(video)
    project = tmp_path / "project"
    for _ in range(2):
        ingest_source(
            video_path=video,
            project_dir=project,
            fandom="Test",
            run_transcript=False,
            run_scenes=False,
            run_clip=False,
            run_characters=False,
        )
    catalog = json.loads((project / "data" / "source-catalog.json").read_text())
    assert len(catalog["sources"]) == 1


def test_ingest_flags_low_resolution_and_missing_audio(tmp_path: Path) -> None:
    """Low-res and no-audio videos get flagged in source-catalog.json."""
    video = tmp_path / "raw" / "tiny-no-audio.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=duration=2:size=320x240:rate=24",
            "-an",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            str(video),
        ],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    project = tmp_path / "project"
    report = ingest_source(
        video_path=video,
        project_dir=project,
        fandom="Test",
        run_transcript=False,
        run_scenes=False,
        run_clip=False,
        run_characters=False,
    )
    assert report.succeeded
    catalog = json.loads((project / "data" / "source-catalog.json").read_text())
    flags = catalog["sources"][0]["flags"]
    codes = {f["code"] for f in flags}
    assert "low_resolution" in codes
    assert "no_audio_track" in codes
