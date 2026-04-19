"""Schema round-trip validation tests.

For every schema in fandomforge.schemas.SCHEMA_IDS:

- There MUST be a `tests/fixtures/schemas/good/<id>.json` that validates.
- There MUST be a `tests/fixtures/schemas/broken/<id>.json` that fails with at
  least one specific error we can name (no generic 'something is wrong' passes).

These tests lock the contract between every pipeline stage. If a schema changes,
the fixtures must change with it, or CI breaks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fandomforge.schemas import SCHEMA_IDS, load_schema
from fandomforge.validation import (
    ValidationError,
    infer_schema_id,
    validate,
    validate_and_write,
    validate_file,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "schemas"
GOOD_DIR = FIXTURE_ROOT / "good"
BROKEN_DIR = FIXTURE_ROOT / "broken"


# ---------------------------------------------------------------------------
# Parametrization helpers
# ---------------------------------------------------------------------------


def _good_fixture_path(schema_id: str) -> Path:
    return GOOD_DIR / f"{schema_id}.json"


def _broken_fixture_path(schema_id: str) -> Path:
    return BROKEN_DIR / f"{schema_id}.json"


# ---------------------------------------------------------------------------
# Schema file existence + loadability
# ---------------------------------------------------------------------------


def test_every_schema_loads():
    for sid in SCHEMA_IDS:
        schema = load_schema(sid)
        assert "$id" in schema, f"schema {sid} missing $id"
        assert schema.get("$schema", "").startswith(
            "https://json-schema.org/draft/2020-12"
        ), f"schema {sid} must declare Draft 2020-12"


def test_every_schema_is_strict_additionalproperties_false():
    """Every top-level object schema should set additionalProperties: false."""
    for sid in SCHEMA_IDS:
        schema = load_schema(sid)
        if schema.get("type") != "object":
            continue
        assert schema.get("additionalProperties") is False, (
            f"schema {sid} must use additionalProperties: false at root"
        )


# ---------------------------------------------------------------------------
# Good fixtures must exist and must validate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("schema_id", SCHEMA_IDS)
def test_good_fixture_exists(schema_id: str):
    path = _good_fixture_path(schema_id)
    assert path.exists(), (
        f"Missing good fixture for schema '{schema_id}' at {path}. "
        f"Every schema needs a passing reference example."
    )


@pytest.mark.parametrize("schema_id", SCHEMA_IDS)
def test_good_fixture_validates(schema_id: str):
    path = _good_fixture_path(schema_id)
    if not path.exists():
        pytest.skip(f"good fixture missing for {schema_id}")
    data = json.loads(path.read_text())
    # Should not raise.
    validate(data, schema_id)


# ---------------------------------------------------------------------------
# Broken fixtures must exist and must fail
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("schema_id", SCHEMA_IDS)
def test_broken_fixture_exists(schema_id: str):
    path = _broken_fixture_path(schema_id)
    assert path.exists(), (
        f"Missing broken fixture for schema '{schema_id}' at {path}. "
        f"Every schema needs a failure reference example."
    )


@pytest.mark.parametrize("schema_id", SCHEMA_IDS)
def test_broken_fixture_fails_validation(schema_id: str):
    path = _broken_fixture_path(schema_id)
    if not path.exists():
        pytest.skip(f"broken fixture missing for {schema_id}")
    data = json.loads(path.read_text())
    with pytest.raises(ValidationError) as excinfo:
        validate(data, schema_id)
    assert len(excinfo.value.failures) >= 1, (
        f"broken fixture for {schema_id} should produce at least one failure"
    )


# ---------------------------------------------------------------------------
# Schema id inference
# ---------------------------------------------------------------------------


def test_infer_schema_id_known_names():
    assert infer_schema_id("beat-map.json") == "beat-map"
    assert infer_schema_id("project-config.yaml") == "project-config"
    assert infer_schema_id("some/path/shot-list.json") == "shot-list"
    assert infer_schema_id("data/scenes.json") == "scenes"


def test_infer_schema_id_unknown_raises():
    with pytest.raises(KeyError):
        infer_schema_id("my-random-output.json")


# ---------------------------------------------------------------------------
# validate_file and validate_and_write
# ---------------------------------------------------------------------------


def test_validate_file_good_passes(tmp_path: Path):
    data = json.loads((GOOD_DIR / "beat-map.json").read_text())
    target = tmp_path / "beat-map.json"
    target.write_text(json.dumps(data))
    validate_file(target)  # auto-infer
    validate_file(target, "beat-map")  # explicit


def test_validate_file_broken_raises(tmp_path: Path):
    data = json.loads((BROKEN_DIR / "beat-map.json").read_text())
    target = tmp_path / "beat-map.json"
    target.write_text(json.dumps(data))
    with pytest.raises(ValidationError):
        validate_file(target)


def test_validate_and_write_is_atomic(tmp_path: Path):
    target = tmp_path / "out" / "beat-map.json"
    data = json.loads((GOOD_DIR / "beat-map.json").read_text())
    result = validate_and_write(data, "beat-map", target)
    assert result == target
    assert target.exists()
    # Round-trip — file is actual JSON and re-validates.
    reloaded = json.loads(target.read_text())
    validate(reloaded, "beat-map")


def test_validate_and_write_never_creates_file_when_invalid(tmp_path: Path):
    target = tmp_path / "beat-map.json"
    bad_data = json.loads((BROKEN_DIR / "beat-map.json").read_text())
    with pytest.raises(ValidationError):
        validate_and_write(bad_data, "beat-map", target)
    assert not target.exists(), (
        "validate_and_write must not leave a partial file when validation fails"
    )


# ---------------------------------------------------------------------------
# Real beat.py output validates
# ---------------------------------------------------------------------------


def test_real_beat_analyze_output_is_schema_valid(tmp_path: Path):
    """Run the real beat pipeline on the bundled demo audio and confirm the
    full payload (beat map + drops + buildups + breakdowns + energy curve)
    validates against beat-map.schema.json."""
    demo = Path(__file__).parent.parent.parent / "assets" / "demo" / "test-120bpm-with-drop.wav"
    if not demo.exists():
        pytest.skip(f"demo audio missing: {demo}")

    from fandomforge.audio import analyze_beats, compute_energy_curve, detect_drops
    from fandomforge.audio.drops import detect_breakdowns, detect_buildups

    bm = analyze_beats(demo)
    drops = detect_drops(demo)
    buildups = detect_buildups(demo, drops)
    breakdowns = detect_breakdowns(demo)
    curve = compute_energy_curve(demo)

    payload = {
        "schema_version": 1,
        **bm.to_dict(),
        "downbeat_source": "librosa-heuristic",
        "drops": [d.to_dict() for d in drops],
        "buildups": [b.to_dict() for b in buildups],
        "breakdowns": [bd.to_dict() for bd in breakdowns],
        "energy_curve": [[t, e] for t, e in curve],
    }
    validate(payload, "beat-map")
