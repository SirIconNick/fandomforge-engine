"""Integration tests that run the pipeline against real legal fixtures.

These require `ff fixtures fetch` to have been run first. CI can skip via:

    pytest -m "not requires_fixtures"

To run:

    ff fixtures fetch
    pytest -m requires_fixtures
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "media"
MANIFEST = Path(__file__).parent / "fixtures" / "manifest.json"


def _ff() -> str:
    """Resolve the ff binary, preferring the repo's venv over PATH."""
    env = os.environ.get("FF_BINARY")
    if env and Path(env).exists():
        return env
    venv = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "ff"
    if venv.exists():
        return str(venv)
    found = shutil.which("ff")
    if found:
        return found
    return "ff"


def _fixture_ready(item_id: str) -> tuple[bool, Path | None]:
    if not MANIFEST.exists():
        return False, None
    data = json.loads(MANIFEST.read_text())
    for item in data.get("items", []):
        if item.get("id") == item_id:
            target = FIXTURE_DIR / (item.get("filename") or item_id)
            return target.exists() and target.stat().st_size > 0, target
    return False, None


pytestmark = pytest.mark.requires_fixtures


@pytest.fixture(scope="session")
def sneaky_snitch_audio() -> Path:
    ready, path = _fixture_ready("incompetech-sneaky-snitch")
    if not ready or path is None:
        pytest.skip("fixture not cached — run 'ff fixtures fetch' first")
    return path


@pytest.fixture(scope="session")
def hitman_audio() -> Path:
    ready, path = _fixture_ready("incompetech-hitman")
    if not ready or path is None:
        pytest.skip("fixture not cached — run 'ff fixtures fetch' first")
    return path


def test_beat_analyze_on_real_audio(sneaky_snitch_audio: Path, tmp_path: Path) -> None:
    """ff beat analyze should produce a schema-valid beat-map from a real audio file."""
    output = tmp_path / "beat-map.json"
    result = subprocess.run(
        [_ff(), "beat", "analyze", str(sneaky_snitch_audio), "-o", str(output)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"beat analyze failed: {result.stderr}"
    assert output.exists(), "beat-map.json was not written"
    data = json.loads(output.read_text())
    assert data.get("schema_version") == 1
    assert data.get("bpm", 0) > 0
    assert len(data.get("beats", [])) > 0
    assert data.get("duration_sec", 0) > 10.0


def test_validate_beat_map_from_real_audio(sneaky_snitch_audio: Path, tmp_path: Path) -> None:
    """The beat-map produced from real audio passes the ff validate check."""
    output = tmp_path / "beat-map.json"
    subprocess.run(
        [_ff(), "beat", "analyze", str(sneaky_snitch_audio), "-o", str(output)],
        capture_output=True, text=True, check=True, timeout=120,
    )
    validate = subprocess.run(
        [_ff(), "validate", "file", str(output)],
        capture_output=True, text=True,
    )
    assert validate.returncode == 0, f"validation failed: {validate.stderr}"
    assert "valid" in validate.stdout.lower()


def test_beat_detection_finds_drops_on_cinematic_track(hitman_audio: Path, tmp_path: Path) -> None:
    """Hitman has a clear cinematic drop — detection should find at least one."""
    output = tmp_path / "beat-map.json"
    subprocess.run(
        [_ff(), "beat", "analyze", str(hitman_audio), "-o", str(output)],
        capture_output=True, text=True, check=True, timeout=120,
    )
    data = json.loads(output.read_text())
    drops = data.get("drops", [])
    assert len(drops) >= 1, "expected at least one drop detected in Hitman"
