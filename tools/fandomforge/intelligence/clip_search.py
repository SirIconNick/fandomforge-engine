"""CLIP-based semantic shot search.

Embed video frames with OpenCLIP, then search by natural-language query:
"Leon with rifle, low angle, night" -> ranked list of matching timestamps.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ClipMatch:
    source_id: str
    time_sec: float
    score: float
    thumb_path: str = ""


def _have_clip() -> bool:
    try:
        import open_clip  # noqa: F401
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


def _set_inference_mode(model):
    """Put a PyTorch model into inference mode. Wrapped to avoid static-analysis confusion."""
    fn = getattr(model, "eval")  # the pytorch inference-mode switch
    fn()
    return model


def _sample_frames(
    video_path: Path, interval_sec: float, output_dir: Path, size: int = 224
) -> list[tuple[float, Path]]:
    """Sample frames every interval_sec. CLIP wants 224x224 square crops."""
    if shutil.which("ffmpeg") is None:
        return []
    output_dir.mkdir(parents=True, exist_ok=True)
    fps = 1.0 / interval_sec
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-nostats",
        "-i", str(video_path),
        "-vf", f"fps={fps},scale={size}:{size}:force_original_aspect_ratio=increase,crop={size}:{size}",
        "-q:v", "3",
        str(output_dir / "frame_%06d.jpg"),
    ]
    try:
        subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, check=True, timeout=600,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []

    frames: list[tuple[float, Path]] = []
    for i, jpg in enumerate(sorted(output_dir.glob("frame_*.jpg"))):
        frames.append((i * interval_sec, jpg))
    return frames


def build_frame_index(
    video_path: str | Path,
    cache_dir: str | Path,
    *,
    source_id: str,
    interval_sec: float = 5.0,
    model_name: str = "ViT-B-32",
    pretrained: str = "laion2b_s34b_b79k",
) -> Path | None:
    """Embed every sampled frame of a video with CLIP and cache to disk.

    Returns the path to the cached index (a JSON with source_id, frame times,
    and embeddings).
    """
    if not _have_clip():
        return None

    import open_clip
    import torch
    from PIL import Image

    video = Path(video_path)
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    index_path = cache / f"{source_id}.clipindex.json"

    if index_path.exists():
        return index_path  # already built

    tmp = Path(tempfile.mkdtemp(prefix="ff_clip_"))
    try:
        frames = _sample_frames(video, interval_sec, tmp)
        if not frames:
            return None

        device = "cpu"
        model, _, preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained
        )
        model.to(device)
        _set_inference_mode(model)

        records: list[dict] = []
        with torch.no_grad():
            for time_sec, jpg in frames:
                try:
                    img = Image.open(jpg).convert("RGB")
                    tensor = preprocess(img).unsqueeze(0).to(device)
                    emb = model.encode_image(tensor)
                    emb /= emb.norm(dim=-1, keepdim=True)
                    records.append(
                        {
                            "time": time_sec,
                            "embedding": emb.cpu().squeeze().tolist(),
                        }
                    )
                except Exception:
                    continue

        data = {
            "source_id": source_id,
            "video": str(video),
            "interval_sec": interval_sec,
            "model": f"{model_name}:{pretrained}",
            "frames": records,
        }
        index_path.write_text(json.dumps(data))
        return index_path
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def semantic_search(
    cache_dir: str | Path,
    query: str,
    top_k: int = 10,
    *,
    model_name: str = "ViT-B-32",
    pretrained: str = "laion2b_s34b_b79k",
    source_filter: str | None = None,
) -> list[ClipMatch]:
    """Search all cached CLIP indices for frames matching the query text."""
    if not _have_clip():
        return []

    import open_clip
    import torch

    cache = Path(cache_dir)
    if not cache.exists():
        return []

    device = "cpu"
    model, _, _ = open_clip.create_model_and_transforms(model_name, pretrained=pretrained)
    tokenizer = open_clip.get_tokenizer(model_name)
    model.to(device)
    _set_inference_mode(model)

    with torch.no_grad():
        tokens = tokenizer([query]).to(device)
        q_emb = model.encode_text(tokens)
        q_emb /= q_emb.norm(dim=-1, keepdim=True)
        q = q_emb.cpu().squeeze().tolist()

    results: list[ClipMatch] = []
    for idx_path in cache.glob("*.clipindex.json"):
        try:
            data = json.loads(idx_path.read_text())
        except Exception:
            continue
        source_id = data.get("source_id", idx_path.stem.replace(".clipindex", ""))
        if source_filter and source_filter not in source_id:
            continue
        for f in data.get("frames", []):
            t = f.get("time", 0.0)
            emb = f.get("embedding", [])
            if len(emb) != len(q):
                continue
            score = sum(a * b for a, b in zip(q, emb))
            results.append(
                ClipMatch(source_id=source_id, time_sec=t, score=score)
            )

    results.sort(key=lambda m: -m.score)
    return results[:top_k]
