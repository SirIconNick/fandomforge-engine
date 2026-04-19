"""Smoke test — Leon baseline artifacts still reproducible.

These tests don't run the full pipeline (too slow for CI) but verify that
the frozen Stage-0 baseline artifacts under projects/leon-badass-monologue/
baselines/leon-v2/ are loadable and match expected invariants.

They guard against silent regressions where a pipeline change breaks the
known-good Leon reference.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LEON_PROJ = REPO_ROOT / "projects" / "leon-badass-monologue"
LEON_BASELINE = LEON_PROJ / "baselines" / "leon-v2"
DEAN_PROJ = REPO_ROOT / "projects" / "dean-winchester-renegades"
DEAN_BASELINE = DEAN_PROJ / "baselines" / "dean-v1"


# ---------------------------------------------------------------------------
# Leon Stage-0 baseline
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not LEON_BASELINE.exists(),
    reason="Leon baseline not present (Stage 0 not captured yet)",
)
def test_leon_baseline_stats_loadable() -> None:
    stats_path = LEON_BASELINE / "baseline-stats.json"
    assert stats_path.exists(), "Leon baseline-stats.json missing"
    stats = json.loads(stats_path.read_text())
    # Library size should be substantial for Leon
    assert stats["library"]["total_scenes"] > 2000
    # Per-era breakdown present
    assert "by_era" in stats["shot_library_db"]
    # Dialogue spine captured
    assert stats["dialogue"]["wav_count"] >= 30


@pytest.mark.skipif(
    not LEON_BASELINE.exists(),
    reason="Leon baseline not present",
)
def test_leon_baseline_mp4_exists() -> None:
    # Accept any of the likely baseline filenames
    candidates = [
        LEON_BASELINE / "leon-layered-v2-FINAL.mp4",
        LEON_BASELINE / "final.mp4",
    ]
    assert any(c.exists() for c in candidates), \
        f"No Leon baseline mp4 in {LEON_BASELINE}"


# ---------------------------------------------------------------------------
# Dean Stage-4 baseline (generalization proof)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not DEAN_BASELINE.exists(),
    reason="Dean baseline not captured yet (Stage 4 not run)",
)
def test_dean_baseline_passes_every_qa_gate() -> None:
    stats = json.loads((DEAN_BASELINE / "baseline-stats.json").read_text())
    gates = stats["qa_gates"]
    assert gates["audio"] is True
    assert gates["visual"] is True
    assert gates["pacing"] is True
    assert gates["structural"] is True
    assert gates["narrative"] is True


@pytest.mark.skipif(
    not DEAN_BASELINE.exists(),
    reason="Dean baseline not captured yet",
)
def test_dean_baseline_lufs_within_spec() -> None:
    stats = json.loads((DEAN_BASELINE / "baseline-stats.json").read_text())
    lufs = stats["audio"]["integrated_lufs"]
    assert -17.0 <= lufs <= -12.0
    peak = stats["audio"]["true_peak_dbfs"]
    assert peak <= -1.0, "True peak above YouTube safe threshold"


@pytest.mark.skipif(
    not DEAN_BASELINE.exists(),
    reason="Dean baseline not captured yet",
)
def test_dean_baseline_character_match_is_majority() -> None:
    stats = json.loads((DEAN_BASELINE / "baseline-stats.json").read_text())
    # Primary-character attribution should dominate in a character-focused edit
    pct = stats["library"]["character_match_pct"]
    assert pct >= 70


# ---------------------------------------------------------------------------
# Layered plan sanity
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not DEAN_BASELINE.exists(),
    reason="Dean baseline not present",
)
def test_dean_layered_plan_has_shots_and_vo() -> None:
    plan = json.loads((DEAN_BASELINE / "layered-plan.json").read_text())
    assert plan["validation_passed"] is True
    assert len(plan["shots"]) >= 20
    assert len(plan["dialogue_lines"]) >= 3
    # Every placed VO line should have a wav_path that was actually used
    placed = [d for d in plan["dialogue_lines"] if d.get("placement_sec") is not None]
    assert len(placed) >= 3


@pytest.mark.skipif(
    not DEAN_BASELINE.exists(),
    reason="Dean baseline not present",
)
def test_dean_config_frozen_in_baseline_matches_current() -> None:
    """If someone rewrote Dean's config post-baseline, flag it so we know why."""
    import yaml
    baseline_cfg = yaml.safe_load((DEAN_BASELINE / "project-config.yaml").read_text())
    current_cfg = yaml.safe_load((DEAN_PROJ / "project-config.yaml").read_text())
    # Core identity fields must be stable — drift here means baseline is invalid
    assert baseline_cfg.get("character") == current_cfg.get("character")
    assert baseline_cfg.get("song") == current_cfg.get("song")
