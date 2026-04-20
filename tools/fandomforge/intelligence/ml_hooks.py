"""ML/ComfyUI hook layer (Phase 8).

INTERFACE-ONLY stub. Phase 8 ships the contract every ML capability
plugs into so existing code can call out to ML when available, and
gracefully degrade to heuristic when not. Real model integration
(ComfyUI, face detection, AI upscale, style transfer) is INTENTIONALLY
NOT shipped here — that's a separate effort that needs GPU memory
budgeting, model version pinning, and dependency management.

Per amendment A8: "ship deterministic Phase 3 first, earn the right to
add ML later." This module is the seam.

Three capability families defined here:

1. **Face detection** — Phase 3.1 aspect-ratio arbiter calls this when
   it needs a real safe-zone (face polygon per frame) instead of the
   default center-weighted 80% rect. ComfyUI + InsightFace or
   MediaPipe Face Detection.

2. **AI upscale** — Phase 3.2 quality_filler can call this for D-tier
   sources when --allow-dtier is set and the user wants upscale. ESRGAN
   variants via ComfyUI work-fine for 480p→1080p, slow at 4K.

3. **Style transfer** — Phase 3.3 unifying-filter pass can call this as
   a last-resort to push outlier sources toward the project's signature.
   StableDiffusion img2img with low denoise strength + style LoRA.

All three are CONTRACTS, not implementations. The default backend is
NoOpBackend which logs the request and returns the input unchanged so
the surrounding code paths don't break.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


logger = logging.getLogger(__name__)


# ---------- Contract types ----------


@dataclass
class FaceBox:
    """A detected face bounding box, normalized 0-1 coordinates."""
    x: float
    y: float
    w: float
    h: float
    confidence: float


@dataclass
class FaceDetectionResult:
    frame_path: Path
    faces: list[FaceBox] = field(default_factory=list)
    backend: str = "no-op"

    def safe_zone(self) -> dict[str, float] | None:
        """Compose all detected faces into a single safe-zone rect that
        contains them with 10% padding. Returns None when no faces."""
        if not self.faces:
            return None
        pad = 0.05
        x0 = max(0.0, min(f.x for f in self.faces) - pad)
        y0 = max(0.0, min(f.y for f in self.faces) - pad)
        x1 = min(1.0, max(f.x + f.w for f in self.faces) + pad)
        y1 = min(1.0, max(f.y + f.h for f in self.faces) + pad)
        return {"x": x0, "y": y0, "w": max(0.05, x1 - x0), "h": max(0.05, y1 - y0)}


@dataclass
class UpscaleRequest:
    input_path: Path
    output_path: Path
    target_resolution: tuple[int, int] = (1920, 1080)


@dataclass
class StyleTransferRequest:
    input_path: Path
    output_path: Path
    target_signature: dict[str, Any]  # source-profile-shaped target
    denoise_strength: float = 0.25  # 0 = no change, 1 = full repaint


@dataclass
class MLHookResult:
    """What every backend returns. ok=False with a reason means the
    caller should fall back to its heuristic path."""
    ok: bool
    reason: str = ""
    output_path: Path | None = None
    backend: str = "no-op"


class MLBackend(Protocol):
    """Every Phase 8 backend implements this interface."""
    name: str
    available: bool

    def detect_faces(self, frame_path: Path) -> FaceDetectionResult: ...
    def upscale(self, request: UpscaleRequest) -> MLHookResult: ...
    def style_transfer(self, request: StyleTransferRequest) -> MLHookResult: ...


class NoOpBackend:
    """Default backend — logs the request, returns gracefully so existing
    heuristic code paths take over. This keeps the engine working until
    a real ML backend is plugged in."""
    name = "no-op"
    available = False

    def detect_faces(self, frame_path: Path) -> FaceDetectionResult:
        logger.debug("ML face-detect requested but no backend available; returning empty")
        return FaceDetectionResult(frame_path=frame_path, faces=[], backend=self.name)

    def upscale(self, request: UpscaleRequest) -> MLHookResult:
        logger.debug("ML upscale requested but no backend available")
        return MLHookResult(
            ok=False,
            reason="no ML backend available — install ComfyUI + ESRGAN",
            backend=self.name,
        )

    def style_transfer(self, request: StyleTransferRequest) -> MLHookResult:
        logger.debug("ML style-transfer requested but no backend available")
        return MLHookResult(
            ok=False,
            reason="no ML backend available — install ComfyUI + SD style LoRA",
            backend=self.name,
        )


# ---------- Backend resolver ----------


_active_backend: MLBackend | None = None


def get_backend() -> MLBackend:
    """Return the active ML backend. Defaults to NoOpBackend.

    Future: detect ComfyUI at COMFYUI_PATH env var, return a ComfyUIBackend
    instance that bridges over its REST API. Detect via:
      - COMFYUI_PATH (filesystem) — local install
      - COMFYUI_URL (http://host:port) — remote
    """
    global _active_backend
    if _active_backend is not None:
        return _active_backend
    # Future: detection logic here. For now always NoOp.
    _active_backend = NoOpBackend()
    return _active_backend


def set_backend(backend: MLBackend) -> None:
    """Override the active backend (for tests + future plugin loading)."""
    global _active_backend
    _active_backend = backend


# ---------- High-level convenience wrappers ----------


def detect_faces_in_frame(frame_path: Path) -> FaceDetectionResult:
    return get_backend().detect_faces(frame_path)


def request_upscale(
    input_path: Path,
    output_path: Path,
    *,
    target_resolution: tuple[int, int] = (1920, 1080),
) -> MLHookResult:
    return get_backend().upscale(UpscaleRequest(
        input_path=input_path, output_path=output_path,
        target_resolution=target_resolution,
    ))


def request_style_transfer(
    input_path: Path,
    output_path: Path,
    target_signature: dict[str, Any],
    *,
    denoise_strength: float = 0.25,
) -> MLHookResult:
    return get_backend().style_transfer(StyleTransferRequest(
        input_path=input_path, output_path=output_path,
        target_signature=target_signature, denoise_strength=denoise_strength,
    ))


__all__ = [
    "FaceBox",
    "FaceDetectionResult",
    "MLBackend",
    "MLHookResult",
    "NoOpBackend",
    "StyleTransferRequest",
    "UpscaleRequest",
    "detect_faces_in_frame",
    "get_backend",
    "request_style_transfer",
    "request_upscale",
    "set_backend",
]
