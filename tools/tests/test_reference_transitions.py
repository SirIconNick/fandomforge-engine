"""Tests for the transition classifier's pure-logic helpers.

Full end-to-end classification requires real video frames; those paths are
exercised during corpus re-analysis. Here we test the classifier's decision
logic on constructed numpy arrays."""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")
import numpy as np  # noqa: E402

from fandomforge.intelligence.reference_transitions import (
    TRANSITION_KINDS,
    _classify_pair,
)


def _frame(color: tuple[float, float, float], size: int = 16) -> np.ndarray:
    arr = np.zeros((size, size, 3), dtype=np.float32)
    arr[..., 0] = color[0]
    arr[..., 1] = color[1]
    arr[..., 2] = color[2]
    return arr


def _textured_frame(size: int = 32, horizontal_blur: bool = False) -> np.ndarray:
    # Random texture; optionally blur along horizontal axis to simulate whip
    np.random.seed(42)
    arr = np.random.rand(size, size, 3).astype(np.float32)
    if horizontal_blur:
        # Average across rows to kill horizontal variance
        for i in range(size):
            arr[i] = arr[i].mean(axis=0, keepdims=True)
    return arr


class TestClassifyPair:
    def test_flash_cut_from_near_white(self) -> None:
        out = _frame((0.95, 0.95, 0.95))
        in_ = _textured_frame()
        assert _classify_pair(out, in_, 0.95, 0.5, gap_sec=0.0) == "flash_cut"

    def test_dual_dark_is_dissolve(self) -> None:
        out = _frame((0.02, 0.02, 0.02))
        in_ = _frame((0.03, 0.03, 0.03))
        assert _classify_pair(out, in_, 0.02, 0.03, gap_sec=0.0) == "dissolve"

    def test_identical_frames_read_as_dissolve(self) -> None:
        f = _textured_frame()
        # Identical frames have max histogram correlation → dissolve
        result = _classify_pair(f.copy(), f.copy(), 0.5, 0.5, gap_sec=0.0)
        assert result == "dissolve"

    def test_different_frames_read_as_hard_cut(self) -> None:
        # Two radically different frames — low histogram correlation
        out = _frame((0.8, 0.1, 0.1))  # red
        in_ = _frame((0.1, 0.1, 0.8))  # blue
        assert _classify_pair(out, in_, 0.3, 0.3, gap_sec=0.0) == "hard_cut"


class TestTransitionKinds:
    def test_kinds_are_unique(self) -> None:
        assert len(TRANSITION_KINDS) == len(set(TRANSITION_KINDS))

    def test_kinds_include_expected(self) -> None:
        assert "hard_cut" in TRANSITION_KINDS
        assert "dissolve" in TRANSITION_KINDS
        assert "flash_cut" in TRANSITION_KINDS
        assert "whip_pan" in TRANSITION_KINDS
        assert "speed_ramp" in TRANSITION_KINDS
