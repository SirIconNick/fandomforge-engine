"""Motion-cut classifier tests — pure-logic slice (OpenCV optional)."""

from __future__ import annotations

from fandomforge.intelligence.reference_motion import (
    MOTION_KINDS,
    _classify_cut,
)


class TestClassifyCut:
    def test_same_direction_is_match(self) -> None:
        out = (10.0, 2.0)  # moving slightly right-ish
        in_ = (20.0, 2.0)
        assert _classify_cut(out, in_) == "match_cut"

    def test_inverse_direction_is_impact(self) -> None:
        out = (0.0, 2.0)    # moving right
        in_ = (180.0, 2.0)  # moving left
        assert _classify_cut(out, in_) == "impact_cut"

    def test_both_still_is_neutral(self) -> None:
        out = (0.0, 0.1)
        in_ = (45.0, 0.2)
        assert _classify_cut(out, in_) == "neutral"

    def test_orthogonal_is_disconnected(self) -> None:
        out = (0.0, 2.0)
        in_ = (90.0, 2.0)
        assert _classify_cut(out, in_) == "disconnected"


class TestMotionKinds:
    def test_kinds_unique_and_complete(self) -> None:
        assert len(MOTION_KINDS) == 4
        assert set(MOTION_KINDS) == {"match_cut", "impact_cut", "neutral", "disconnected"}
