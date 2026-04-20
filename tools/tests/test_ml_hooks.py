"""Tests for the Phase 8 ML hook contract layer (interface-only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from fandomforge.intelligence.ml_hooks import (
    FaceBox,
    FaceDetectionResult,
    MLHookResult,
    NoOpBackend,
    StyleTransferRequest,
    UpscaleRequest,
    detect_faces_in_frame,
    get_backend,
    request_style_transfer,
    request_upscale,
    set_backend,
)


@pytest.fixture(autouse=True)
def reset_backend():
    """Reset to default NoOp before each test."""
    set_backend(NoOpBackend())
    yield


class TestNoOpBackend:
    def test_face_detection_returns_empty(self, tmp_path: Path):
        result = detect_faces_in_frame(tmp_path / "frame.png")
        assert result.faces == []
        assert result.backend == "no-op"
        assert result.safe_zone() is None

    def test_upscale_returns_not_ok(self, tmp_path: Path):
        result = request_upscale(tmp_path / "in.mp4", tmp_path / "out.mp4")
        assert result.ok is False
        assert "no ML backend" in result.reason

    def test_style_transfer_returns_not_ok(self, tmp_path: Path):
        result = request_style_transfer(
            tmp_path / "in.mp4", tmp_path / "out.mp4",
            target_signature={"saturation_avg": 0.4},
        )
        assert result.ok is False


class TestFaceBoxAndSafeZone:
    def test_safe_zone_pads_around_single_face(self, tmp_path: Path):
        result = FaceDetectionResult(
            frame_path=tmp_path / "frame.png",
            faces=[FaceBox(x=0.4, y=0.3, w=0.2, h=0.3, confidence=0.92)],
        )
        sz = result.safe_zone()
        assert sz is not None
        # Padded by ~5% so x ≤ 0.4, x+w ≥ 0.6
        assert sz["x"] <= 0.4
        assert sz["x"] + sz["w"] >= 0.6

    def test_safe_zone_bounds_two_faces(self, tmp_path: Path):
        result = FaceDetectionResult(
            frame_path=tmp_path / "frame.png",
            faces=[
                FaceBox(x=0.1, y=0.2, w=0.2, h=0.3, confidence=0.9),
                FaceBox(x=0.7, y=0.4, w=0.2, h=0.3, confidence=0.85),
            ],
        )
        sz = result.safe_zone()
        # Encloses both
        assert sz["x"] <= 0.1
        assert sz["x"] + sz["w"] >= 0.9


class TestBackendOverride:
    def test_set_backend_swaps_active(self):
        class CustomBackend:
            name = "custom"
            available = True

            def detect_faces(self, p):
                return FaceDetectionResult(frame_path=p, faces=[
                    FaceBox(x=0.5, y=0.5, w=0.1, h=0.1, confidence=1.0),
                ], backend=self.name)

            def upscale(self, req):
                return MLHookResult(ok=True, output_path=req.output_path, backend=self.name)

            def style_transfer(self, req):
                return MLHookResult(ok=True, output_path=req.output_path, backend=self.name)

        set_backend(CustomBackend())
        assert get_backend().name == "custom"
        result = detect_faces_in_frame(Path("/tmp/x.png"))
        assert result.backend == "custom"
        assert len(result.faces) == 1
