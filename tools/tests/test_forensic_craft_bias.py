"""Tests for forensic-corpus → craft-weights feedback."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fandomforge.intelligence.forensic_craft_bias import (
    blend_weights,
    forensic_craft_suggestion,
    clear_cache,
)


@pytest.fixture(autouse=True)
def _clear() -> None:
    clear_cache()
    yield
    clear_cache()


def _write_bucket_report(root: Path, bucket: str, consensus: dict[str, float]) -> Path:
    bdir = root / bucket
    bdir.mkdir(parents=True, exist_ok=True)
    path = bdir / "bucket-report.json"
    path.write_text(
        json.dumps({"consensus_craft_weights": consensus}),
        encoding="utf-8",
    )
    return path


class TestForensicCraftSuggestion:
    def test_no_report_returns_none(self, tmp_path: Path) -> None:
        out = forensic_craft_suggestion("action", references_dir=str(tmp_path))
        assert out is None

    def test_empty_bucket_returns_none(self, tmp_path: Path) -> None:
        out = forensic_craft_suggestion("", references_dir=str(tmp_path))
        assert out is None

    def test_reads_consensus_from_report(self, tmp_path: Path) -> None:
        _write_bucket_report(tmp_path, "action", {"dropout": 0.9, "ramp": 0.7})
        out = forensic_craft_suggestion("action", references_dir=str(tmp_path))
        assert out == {"dropout": 0.9, "ramp": 0.7}

    def test_corrupt_json_returns_none(self, tmp_path: Path) -> None:
        bdir = tmp_path / "action"
        bdir.mkdir(parents=True)
        (bdir / "bucket-report.json").write_text("not json", encoding="utf-8")
        out = forensic_craft_suggestion("action", references_dir=str(tmp_path))
        assert out is None


class TestBlendWeights:
    def test_no_forensic_row_passes_through(self) -> None:
        table = {"dropout": 1.0, "ramp": 0.5}
        assert blend_weights(table, None) == {"dropout": 1.0, "ramp": 0.5}

    def test_blend_at_20_pct(self) -> None:
        table = {"dropout": 0.0, "ramp": 1.0}
        forensic = {"dropout": 1.0, "ramp": 0.0}
        out = blend_weights(table, forensic, forensic_weight=0.20)
        # 0.8*0.0 + 0.2*1.0 = 0.2
        assert out["dropout"] == 0.2
        # 0.8*1.0 + 0.2*0.0 = 0.8
        assert out["ramp"] == 0.8

    def test_features_not_in_forensic_stay_at_table_value(self) -> None:
        table = {"dropout": 0.0, "unused_feature": 0.5}
        forensic = {"dropout": 1.0}
        out = blend_weights(table, forensic)
        assert out["unused_feature"] == 0.5


class TestCraftWeightsIntegrationWithForensicBias:
    def test_forensic_bias_nudges_craft_weights(self, tmp_path: Path, monkeypatch) -> None:
        """End-to-end: writing a bucket-report.json shifts craft_weights_for."""
        from fandomforge.config import craft_weights_for
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("FF_TRAINING_BIAS", "0")  # isolate forensic-only
        monkeypatch.setenv("FF_FORENSIC_BIAS", "1")
        clear_cache()

        # Sad bucket has dropout=0 in the hand-tuned table.
        # Forensic says 1.0 for dropout — blend should yield 0.2.
        _write_bucket_report(tmp_path / "references", "sad",
                             {"dropout": 1.0, "ramp": 0.5})
        weights = craft_weights_for("sad")
        # 0.8 * 0.0 + 0.2 * 1.0 = 0.2
        assert 0.15 <= weights["dropout"] <= 0.25

    def test_forensic_bias_disabled_via_env(self, tmp_path: Path, monkeypatch) -> None:
        from fandomforge.config import craft_weights_for
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("FF_TRAINING_BIAS", "0")
        monkeypatch.setenv("FF_FORENSIC_BIAS", "0")
        clear_cache()

        _write_bucket_report(tmp_path / "references", "sad",
                             {"dropout": 1.0})
        weights = craft_weights_for("sad")
        # Disabled → table value wins (sad=0 for dropout)
        assert weights["dropout"] == 0.0
