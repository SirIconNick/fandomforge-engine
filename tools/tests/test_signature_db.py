"""Tests for the visual signature DB (Phase 3.4)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fandomforge.intelligence.signature_db import (
    add_profile,
    bootstrap_from_project,
    flag_deviations,
    get_signature,
    list_signatures,
    project_signature_summary,
)


@pytest.fixture(autouse=True)
def isolate_signatures(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FF_SIGNATURES_DIR", str(tmp_path / "sigs"))
    yield


def _profile(sid: str, **fields) -> dict:
    base = {
        "schema_version": 1,
        "source_id": sid,
        "source_type": "live_action",
        "era_bucket": "post-2020",
        "quality_tier": "B",
        "aspect_ratio_native": "16:9",
        "framerate_native": 24,
        "resolution_native": {"width": 1920, "height": 1080},
        "letterbox_detected": False,
        "pillarbox_detected": False,
        "frames_sampled": 30,
        "generated_at": "2026-04-19T00:00:00Z",
        "generator": "test",
    }
    base.update(fields)
    return base


class TestAddAndGet:
    def test_round_trip(self):
        add_profile(_profile("alpha", saturation_avg=0.5))
        sig = get_signature("alpha")
        assert sig is not None
        assert sig["source_id"] == "alpha"
        assert sig["saturation_avg"] == 0.5

    def test_get_unknown_returns_none(self):
        assert get_signature("does-not-exist") is None


class TestList:
    def setup_method(self):
        for sid, st, era in [
            ("a", "anime", "2010-2020"),
            ("b", "anime", "post-2020"),
            ("c", "live_action", "post-2020"),
        ]:
            add_profile(_profile(sid, source_type=st, era_bucket=era))

    def test_list_all(self):
        assert len(list_signatures()) == 3

    def test_filter_by_source_type(self):
        anime = list_signatures(source_type="anime")
        assert len(anime) == 2

    def test_filter_by_era_bucket(self):
        post_2020 = list_signatures(era_bucket="post-2020")
        assert len(post_2020) == 2


class TestSummaryAndFlagging:
    def test_summary_computes_stats(self):
        profiles = [
            _profile("a", saturation_avg=0.5),
            _profile("b", saturation_avg=0.5),
            _profile("c", saturation_avg=0.5),
        ]
        summary = project_signature_summary(profiles)
        assert summary["saturation_avg"]["mean"] == 0.5
        assert summary["saturation_avg"]["stddev"] == 0.0

    def test_outlier_flagged_above_2_sigma(self):
        # 3 sources at 0.5, 1 outlier at 0.95
        profiles = [_profile(f"src{i}", saturation_avg=0.5) for i in range(3)]
        profiles.append(_profile("outlier", saturation_avg=0.95))
        flags = flag_deviations(profiles, sigma_threshold=1.5)
        assert any(f.source_id == "outlier" and f.metric == "saturation_avg" for f in flags)

    def test_no_flags_when_uniform(self):
        profiles = [_profile(f"src{i}", saturation_avg=0.5) for i in range(5)]
        flags = flag_deviations(profiles)
        assert flags == []


class TestBootstrap:
    def test_bootstrap_from_project_dir(self, tmp_path: Path):
        project_dir = tmp_path / "demo"
        (project_dir / "data" / "source-profiles").mkdir(parents=True)
        for i in range(3):
            p = _profile(f"src{i}", saturation_avg=0.4 + i * 0.1)
            (project_dir / "data" / "source-profiles" / f"src{i}.json").write_text(
                __import__("json").dumps(p)
            )
        n = bootstrap_from_project(project_dir)
        assert n == 3
        assert len(list_signatures()) == 3
