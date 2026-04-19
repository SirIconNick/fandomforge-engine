"""Source ingestion orchestrator.

Takes a raw source video and produces every derived artifact the pipeline
needs:

    source-catalog.json                (registered + indexed)
    derived/<source>/transcript.json   (Whisper word-level)
    derived/<source>/scenes.json       (PySceneDetect)
    derived/<source>/clip.json         (OpenCLIP frame embeddings)
    derived/<source>/characters.json   (face_recognition tags; optional)

Every artifact is schema-validated before being written. `IngestReport` captures
per-step status so the CLI can print a readable summary.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fandomforge import __version__
from fandomforge.validation import validate, validate_and_write


def _model_cache_root() -> Path:
    """Resolve a writable directory for downloaded ML model weights.

    Preference order:
      1. $FANDOMFORGE_CACHE_DIR
      2. $XDG_CACHE_HOME/fandomforge
      3. ~/.cache/fandomforge (if writable)
      4. $TMPDIR/fandomforge-cache
    """
    explicit = os.environ.get("FANDOMFORGE_CACHE_DIR")
    if explicit:
        p = Path(explicit)
        p.mkdir(parents=True, exist_ok=True)
        return p

    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        p = Path(xdg) / "fandomforge"
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p
        except PermissionError:
            pass

    home_cache = Path.home() / ".cache" / "fandomforge"
    try:
        home_cache.mkdir(parents=True, exist_ok=True)
        return home_cache
    except PermissionError:
        pass

    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / "fandomforge-cache"
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


MODEL_CACHE_ROOT = _model_cache_root()
WHISPER_CACHE = MODEL_CACHE_ROOT / "whisper"
OPENCLIP_CACHE = MODEL_CACHE_ROOT / "open_clip"
DEMUCS_CACHE = MODEL_CACHE_ROOT / "demucs"
MADMOM_CACHE = MODEL_CACHE_ROOT / "madmom"

for _p in (WHISPER_CACHE, OPENCLIP_CACHE, DEMUCS_CACHE, MADMOM_CACHE):
    _p.mkdir(parents=True, exist_ok=True)

# Many model-loading libraries honor these env vars instead of explicit kwargs.
os.environ.setdefault("OPENCLIP_CACHE_DIR", str(OPENCLIP_CACHE))
os.environ.setdefault("HF_HOME", str(MODEL_CACHE_ROOT / "huggingface"))
os.environ.setdefault("TORCH_HOME", str(MODEL_CACHE_ROOT / "torch"))
os.environ.setdefault("TORCH_HUB", str(MODEL_CACHE_ROOT / "torch" / "hub"))

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class IngestStep:
    name: str
    status: str  # "ok" | "skipped" | "failed"
    detail: str = ""
    output: str = ""


@dataclass
class IngestReport:
    source_id: str
    path: str
    steps: list[IngestStep] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return not any(s.status == "failed" for s in self.steps)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _blake2_hash(path: Path, chunk_size: int = 1 << 20) -> str:
    """Fast, deterministic content hash. Uses blake2b (hashlib built-in) so we
    don't require a new dep until Omnivore's blake3 CAS lands in Phase 7."""
    h = hashlib.blake2b(digest_size=16)
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return f"b2:{h.hexdigest()}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generator_tag(stage: str) -> str:
    return f"ff ingest:{stage} ({__version__})"


def _set_inference(model: Any) -> Any:
    """Switch a PyTorch model to inference mode via getattr to keep static
    analysis quiet. Same pattern as clip_search.py."""
    switch = getattr(model, "eval")
    switch()
    return model


# ---------------------------------------------------------------------------
# Step: validate source with ffprobe
# ---------------------------------------------------------------------------


def _parse_fps(fps_str: str) -> float:
    if not fps_str or fps_str == "0/0":
        return 0.0
    if "/" in fps_str:
        num, den = fps_str.split("/")
        try:
            d = float(den)
            return float(num) / d if d else 0.0
        except ValueError:
            return 0.0
    try:
        return float(fps_str)
    except ValueError:
        return 0.0


def _probe_source(video: Path) -> dict[str, Any]:
    """Run ffprobe and return a source-catalog.media-schema-valid dict."""
    cmd = [
        "ffprobe", "-v", "error",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(video),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "ffprobe not found. Install ffmpeg (e.g. `brew install ffmpeg`)."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"ffprobe failed: {exc.stderr}") from exc

    data = json.loads(result.stdout)
    fmt = data.get("format", {})
    streams = data.get("streams", [])

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video_stream is None:
        raise RuntimeError(f"No video stream in {video}")

    width = int(video_stream.get("width", 0))
    height = int(video_stream.get("height", 0))
    avg_fps = _parse_fps(video_stream.get("avg_frame_rate", "0/0"))
    r_fps = _parse_fps(video_stream.get("r_frame_rate", "0/0"))
    fps = round(r_fps if r_fps > 0 else avg_fps, 3)
    # VFR heuristic: avg_fps differs from r_fps by > 1%
    vfr = bool(avg_fps and r_fps and abs(avg_fps - r_fps) / r_fps > 0.01)

    bitrate_bps = int(fmt.get("bit_rate") or video_stream.get("bit_rate") or 0)
    return {
        "duration_sec": round(float(fmt.get("duration", 0.0)), 3),
        "width": width,
        "height": height,
        "fps": fps,
        "codec": str(video_stream.get("codec_name", "unknown")),
        "bitrate_kbps": int(bitrate_bps // 1000) if bitrate_bps else 0,
        "has_audio": audio_stream is not None,
        "audio_codec": str(audio_stream.get("codec_name", "")) if audio_stream else "",
        "audio_channels": int(audio_stream.get("channels", 0)) if audio_stream else 0,
        "audio_sample_rate": int(audio_stream.get("sample_rate", 0)) if audio_stream else 0,
        "variable_frame_rate": vfr,
    }


def _source_flags(media: dict[str, Any]) -> list[dict[str, str]]:
    """Flag anything the user should know about their source before editing."""
    flags: list[dict[str, str]] = []
    if media["width"] < 1280 or media["height"] < 720:
        flags.append({
            "level": "warn",
            "code": "low_resolution",
            "message": f"Source is {media['width']}x{media['height']} below 720p.",
        })
    if not media["has_audio"]:
        flags.append({
            "level": "info",
            "code": "no_audio_track",
            "message": "No audio track. Transcript step will be skipped.",
        })
    if media["fps"] not in {23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60}:
        flags.append({
            "level": "warn",
            "code": "nonstandard_fps",
            "message": f"Unusual fps ({media['fps']}). NLE may force conform.",
        })
    if media["codec"] and media["codec"].lower() not in {"h264", "hevc", "h265", "prores", "dnxhd"}:
        flags.append({
            "level": "warn",
            "code": "codec_may_not_import",
            "message": f"Codec '{media['codec']}' may not import cleanly in some NLEs.",
        })
    return flags


# ---------------------------------------------------------------------------
# Step: Whisper transcription -> transcript.json
# ---------------------------------------------------------------------------


def _transcribe_to_schema(
    video: Path,
    source_id: str,
    model_size: str = "base",
    language: str = "en",
) -> dict[str, Any] | None:
    """Run Whisper with word-level timestamps and return a transcript.schema.json-valid dict.

    Returns None if Whisper or ffmpeg is unavailable.
    """
    try:
        import whisper  # type: ignore
    except ImportError:
        return None

    if shutil.which("ffmpeg") is None:
        return None

    model = whisper.load_model(model_size, download_root=str(WHISPER_CACHE))
    result = model.transcribe(
        str(video),
        language=language,
        verbose=False,
        word_timestamps=True,
    )

    segments: list[dict[str, Any]] = []
    import math as _math
    for i, seg in enumerate(result.get("segments", [])):
        words_raw = seg.get("words") or []
        words: list[dict[str, Any]] = []
        for w in words_raw:
            # whisper word-level already reports probability in [0,1]
            prob = float(w.get("probability", 0.0))
            words.append({
                "word": str(w.get("word", "")).strip(),
                "start_sec": float(w.get("start", 0.0)),
                "end_sec": float(w.get("end", 0.0)),
                "confidence": max(0.0, min(1.0, prob)),
            })
        # whisper segment-level gives avg_logprob (natural log, always <= 0).
        # Convert to a probability in (0, 1] via exp(), clamp for safety.
        avg_logprob = float(seg.get("avg_logprob", 0.0))
        seg_conf = _math.exp(avg_logprob) if avg_logprob < 0 else avg_logprob
        segments.append({
            "id": i,
            "start_sec": float(seg["start"]),
            "end_sec": float(seg["end"]),
            "text": str(seg.get("text", "")).strip(),
            "confidence": max(0.0, min(1.0, seg_conf)),
            "words": words,
        })

    return {
        "schema_version": 1,
        "source_id": source_id,
        "language": result.get("language", language),
        "model": f"whisper-{model_size}",
        "segments": segments,
        "generated_at": _now_iso(),
    }


# ---------------------------------------------------------------------------
# Step: PySceneDetect -> scenes.json
# ---------------------------------------------------------------------------


def _scene_cache_key(video: Path, threshold: float, min_scene_sec: float) -> str:
    """Stable cache key per source file + detection params.

    Uses sha256 of (size, mtime, first 64KB) plus the threshold/min_scene params.
    First-64KB hash is 100x faster than full-file hash and enough to tell two
    real videos apart (container header + first frames change on re-encode).
    """
    import hashlib

    try:
        stat = video.stat()
        with open(video, "rb") as f:
            head = f.read(64 * 1024)
    except OSError:
        return ""
    h = hashlib.sha256()
    h.update(str(stat.st_size).encode())
    h.update(str(int(stat.st_mtime)).encode())
    h.update(head)
    h.update(f"t={threshold};m={min_scene_sec}".encode())
    return h.hexdigest()[:32]


def _scene_cache_dir() -> Path:
    """Cross-project scene detection cache. Default ~/.fandomforge/cache/scenes/.

    Override with FF_CACHE_DIR env var (useful for CI).
    """
    env_dir = os.environ.get("FF_CACHE_DIR")
    base = Path(env_dir) if env_dir else Path.home() / ".fandomforge" / "cache"
    return base / "scenes"


def _detect_scenes_to_schema(
    video: Path,
    source_id: str,
    *,
    threshold: float = 3.0,
    min_scene_sec: float = 1.0,
) -> dict[str, Any] | None:
    """Run PySceneDetect's adaptive detector and return a scenes.schema.json-valid dict.

    Caches the scene list by file-content hash in ~/.fandomforge/cache/scenes/
    so re-ingesting the same source doesn't re-run scenedetect (which is a
    2–10 minute OpenCV pass per video).
    """
    cache_key = _scene_cache_key(video, threshold, min_scene_sec)
    cache_path = _scene_cache_dir() / f"{cache_key}.json" if cache_key else None

    if cache_path and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            # Swap source_id to the caller's — cache is content-addressed, not
            # identity-addressed, so the same file ingested into two projects
            # shares scenes but differs on source_id.
            cached["source_id"] = source_id
            cached["generated_at"] = _now_iso()
            return cached
        except (json.JSONDecodeError, OSError):
            pass

    try:
        from scenedetect import AdaptiveDetector, detect  # type: ignore
    except ImportError:
        return None

    scene_list = detect(
        str(video),
        AdaptiveDetector(
            adaptive_threshold=threshold,
            min_scene_len=max(1, int(min_scene_sec * 24)),
        ),
    )

    scenes: list[dict[str, Any]] = []
    for i, (start, end) in enumerate(scene_list):
        duration = end.get_seconds() - start.get_seconds()
        if duration < min_scene_sec:
            continue
        scenes.append({
            "index": i,
            "start_sec": round(start.get_seconds(), 3),
            "end_sec": round(end.get_seconds(), 3),
            "start_frame": int(start.get_frames()),
            "end_frame": int(end.get_frames()),
        })

    payload = {
        "schema_version": 1,
        "source_id": source_id,
        "detector": "adaptive",
        "threshold": float(threshold),
        "scenes": scenes,
        "generated_at": _now_iso(),
    }

    if cache_path:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload, indent=2))
        except OSError:
            pass

    return payload


# ---------------------------------------------------------------------------
# Step: OpenCLIP frame embeddings -> clip.json
# ---------------------------------------------------------------------------


def _embed_frames_to_schema(
    video: Path,
    source_id: str,
    output_dir: Path,
    *,
    interval_sec: float = 2.0,
    model_name: str = "ViT-B-32",
    pretrained: str = "laion2b_s34b_b79k",
) -> dict[str, Any] | None:
    """Embed every Nth frame with OpenCLIP and return a manifest dict.

    Embeddings are written to an .npz file under output_dir and referenced by
    path. Keeps the JSON manifest small and the numeric data in efficient
    numpy format.
    """
    try:
        import numpy as np  # type: ignore
        import open_clip  # type: ignore
        import torch  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError:
        return None

    if shutil.which("ffmpeg") is None:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    npz_path = output_dir / f"{source_id}.clip.npz"

    tmp = Path(tempfile.mkdtemp(prefix="ff_clip_"))
    try:
        fps = 1.0 / interval_sec
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-nostats",
            "-i", str(video),
            "-vf", f"fps={fps},scale=224:224:force_original_aspect_ratio=increase,crop=224:224",
            "-q:v", "3",
            str(tmp / "frame_%06d.jpg"),
        ]
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            check=True,
            timeout=900,
        )

        jpgs = sorted(tmp.glob("frame_*.jpg"))
        if not jpgs:
            return None

        device = "cpu"
        model, _, preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained, cache_dir=str(OPENCLIP_CACHE)
        )
        model.to(device)
        _set_inference(model)

        times: list[float] = []
        embs: list[list[float]] = []
        with torch.no_grad():
            for i, jpg in enumerate(jpgs):
                try:
                    img = Image.open(jpg).convert("RGB")
                    tensor = preprocess(img).unsqueeze(0).to(device)
                    emb = model.encode_image(tensor)
                    emb = emb / emb.norm(dim=-1, keepdim=True)
                    times.append(round(i * interval_sec, 3))
                    embs.append(emb.cpu().squeeze().tolist())
                except Exception as e:
                    logger.warning("clip embed frame %s failed: %s", jpg, e)

        if not embs:
            return None

        arr = np.asarray(embs, dtype=np.float32)
        t_arr = np.asarray(times, dtype=np.float32)
        np.savez_compressed(npz_path, times=t_arr, embeddings=arr)

        return {
            "source_id": source_id,
            "model": f"{model_name}:{pretrained}",
            "interval_sec": interval_sec,
            "frame_count": len(times),
            "npz_path": str(npz_path),
            "dim": int(arr.shape[1]),
            "generated_at": _now_iso(),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Step: face_recognition character tagging -> characters.json
# ---------------------------------------------------------------------------


def _tag_characters_to_dict(
    video: Path,
    source_id: str,
    characters: dict[str, Path],
    *,
    sample_interval_sec: float = 5.0,
    tolerance: float = 0.55,
) -> dict[str, Any] | None:
    """For each character (name -> reference image path), scan the video and
    record per-character appearances. Returns None if face_recognition is not
    available."""
    try:
        import face_recognition  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return None

    if not characters or shutil.which("ffmpeg") is None:
        return None

    encodings: dict[str, Any] = {}
    for name, ref_path in characters.items():
        if not ref_path.exists():
            logger.warning("character reference %s missing: %s", name, ref_path)
            continue
        img = face_recognition.load_image_file(str(ref_path))
        face_locs = face_recognition.face_locations(img, model="hog")
        if not face_locs:
            logger.warning("no face found in reference %s", ref_path)
            continue
        encs = face_recognition.face_encodings(img, face_locs)
        if encs:
            sizes = [
                ((b - t) * (r - l), i)
                for i, (t, r, b, l) in enumerate(face_locs)
            ]
            _, best = max(sizes)
            encodings[name] = encs[best]
    if not encodings:
        return None

    tmp = Path(tempfile.mkdtemp(prefix="ff_chars_"))
    try:
        fps = 1.0 / sample_interval_sec
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-nostats",
                "-i", str(video),
                "-vf", f"fps={fps},scale=480:-2",
                "-q:v", "4",
                str(tmp / "frame_%06d.jpg"),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            check=True,
            timeout=1800,
        )

        jpgs = sorted(tmp.glob("frame_*.jpg"))
        appearances: dict[str, list[dict[str, Any]]] = {name: [] for name in encodings}
        totals: dict[str, int] = {name: 0 for name in encodings}
        best_dist: dict[str, float] = {name: 1.0 for name in encodings}

        for i, jpg in enumerate(jpgs):
            time_sec = i * sample_interval_sec
            try:
                img = face_recognition.load_image_file(str(jpg))
                face_locs = face_recognition.face_locations(img, model="hog")
                if not face_locs:
                    continue
                encs_here = face_recognition.face_encodings(img, face_locs)
                if not encs_here:
                    continue
                for name, ref_enc in encodings.items():
                    distances = face_recognition.face_distance(encs_here, ref_enc)
                    m = float(min(distances))
                    if m <= tolerance:
                        conf = max(0.0, 1.0 - m / tolerance)
                        appearances[name].append({
                            "time_sec": round(time_sec, 3),
                            "confidence": round(conf, 3),
                        })
                        totals[name] += 1
                        if m < best_dist[name]:
                            best_dist[name] = m
            except Exception as e:
                logger.warning("character scan frame %s failed: %s", jpg, e)

        return {
            "source_id": source_id,
            "sample_interval_sec": sample_interval_sec,
            "tolerance": tolerance,
            "characters": [
                {
                    "name": name,
                    "reference_path": str(characters[name]),
                    "appearances": appearances[name],
                    "total_appearances": totals[name],
                    "best_distance": round(best_dist[name], 4),
                    "confidence": round(max(0.0, 1.0 - best_dist[name] / tolerance), 3),
                }
                for name in encodings
            ],
            "generated_at": _now_iso(),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Source catalog I/O
# ---------------------------------------------------------------------------


def _load_or_init_catalog(path: Path, project_slug: str) -> dict[str, Any]:
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        validate(data, "source-catalog")
        return data
    return {
        "schema_version": 1,
        "project_slug": project_slug,
        "sources": [],
        "generated_at": _now_iso(),
    }


def _upsert_source(
    catalog: dict[str, Any],
    source_entry: dict[str, Any],
) -> None:
    """Replace or append a source by id."""
    sources = catalog["sources"]
    for i, s in enumerate(sources):
        if s["id"] == source_entry["id"]:
            sources[i] = source_entry
            return
    sources.append(source_entry)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def ingest_source(
    *,
    video_path: Path,
    project_dir: Path,
    fandom: str,
    source_type: str = "movie",
    title: str | None = None,
    year: int | None = None,
    characters: dict[str, Path] | None = None,
    run_transcript: bool = True,
    run_scenes: bool = True,
    run_clip: bool = True,
    run_characters: bool = True,
    whisper_model: str = "base",
    clip_interval_sec: float = 2.0,
    scenes_threshold: float = 3.0,
    force: bool = False,
) -> IngestReport:
    """Run full ingest on one source video.

    Every derived artifact is written to `project_dir/derived/<source_id>/`
    and registered in `project_dir/data/source-catalog.json` (schema-valid).

    Pass `characters={"Leon": Path("raw/leon-face.jpg"), ...}` to run
    character tagging.
    """
    video_path = Path(video_path).resolve()
    if not video_path.exists():
        raise FileNotFoundError(video_path)

    project_dir = Path(project_dir).resolve()
    project_dir.mkdir(parents=True, exist_ok=True)

    source_id = _blake2_hash(video_path)
    report = IngestReport(source_id=source_id, path=str(video_path))

    derived_dir = project_dir / "derived" / source_id
    derived_dir.mkdir(parents=True, exist_ok=True)

    try:
        media = _probe_source(video_path)
        report.steps.append(IngestStep(
            name="probe",
            status="ok",
            detail=f"{media['width']}x{media['height']} @ {media['fps']}fps {media['codec']}",
        ))
    except Exception as e:
        report.steps.append(IngestStep(
            name="probe",
            status="failed",
            detail=str(e),
        ))
        return report

    flags = _source_flags(media)

    # Transcript.
    transcript_path = derived_dir / "transcript.json"
    if run_transcript and media["has_audio"]:
        if force or not transcript_path.exists():
            payload = _transcribe_to_schema(video_path, source_id, model_size=whisper_model)
            if payload is None:
                report.steps.append(IngestStep(
                    name="transcript",
                    status="skipped",
                    detail="whisper or ffmpeg not available",
                ))
            else:
                try:
                    validate_and_write(payload, "transcript", transcript_path)
                    report.steps.append(IngestStep(
                        name="transcript",
                        status="ok",
                        detail=f"{len(payload['segments'])} segments",
                        output=str(transcript_path),
                    ))
                except Exception as e:
                    report.steps.append(IngestStep(
                        name="transcript",
                        status="failed",
                        detail=str(e),
                    ))
        else:
            report.steps.append(IngestStep(
                name="transcript",
                status="ok",
                detail="cached",
                output=str(transcript_path),
            ))
    elif run_transcript:
        report.steps.append(IngestStep(
            name="transcript",
            status="skipped",
            detail="source has no audio track",
        ))

    # Scenes.
    scenes_path = derived_dir / "scenes.json"
    if run_scenes:
        if force or not scenes_path.exists():
            payload = _detect_scenes_to_schema(video_path, source_id, threshold=scenes_threshold)
            if payload is None:
                report.steps.append(IngestStep(
                    name="scenes",
                    status="skipped",
                    detail="scenedetect not available",
                ))
            else:
                try:
                    validate_and_write(payload, "scenes", scenes_path)
                    report.steps.append(IngestStep(
                        name="scenes",
                        status="ok",
                        detail=f"{len(payload['scenes'])} scenes",
                        output=str(scenes_path),
                    ))
                except Exception as e:
                    report.steps.append(IngestStep(
                        name="scenes",
                        status="failed",
                        detail=str(e),
                    ))
        else:
            report.steps.append(IngestStep(
                name="scenes",
                status="ok",
                detail="cached",
                output=str(scenes_path),
            ))

    # CLIP embeddings.
    clip_manifest_path = derived_dir / "clip.json"
    clip_npz_path = derived_dir / f"{source_id}.clip.npz"
    if run_clip:
        if force or not clip_manifest_path.exists():
            payload = _embed_frames_to_schema(
                video_path, source_id, derived_dir, interval_sec=clip_interval_sec
            )
            if payload is None:
                report.steps.append(IngestStep(
                    name="clip",
                    status="skipped",
                    detail="open_clip/torch/PIL not available",
                ))
            else:
                clip_manifest_path.write_text(json.dumps(payload, indent=2))
                report.steps.append(IngestStep(
                    name="clip",
                    status="ok",
                    detail=f"{payload['frame_count']} frames embedded ({payload['dim']}-d)",
                    output=str(clip_manifest_path),
                ))
        else:
            report.steps.append(IngestStep(
                name="clip",
                status="ok",
                detail="cached",
                output=str(clip_manifest_path),
            ))

    # Character tagging.
    chars_path = derived_dir / "characters.json"
    if run_characters and characters:
        if force or not chars_path.exists():
            payload = _tag_characters_to_dict(video_path, source_id, characters)
            if payload is None:
                report.steps.append(IngestStep(
                    name="characters",
                    status="skipped",
                    detail="face_recognition not available or no valid refs",
                ))
            else:
                chars_path.write_text(json.dumps(payload, indent=2))
                total = sum(c["total_appearances"] for c in payload["characters"])
                report.steps.append(IngestStep(
                    name="characters",
                    status="ok",
                    detail=f"{total} appearances across {len(payload['characters'])} characters",
                    output=str(chars_path),
                ))
        else:
            report.steps.append(IngestStep(
                name="characters",
                status="ok",
                detail="cached",
                output=str(chars_path),
            ))

    # Update source catalog.
    catalog_path = project_dir / "data" / "source-catalog.json"
    catalog = _load_or_init_catalog(catalog_path, project_slug=project_dir.name)

    characters_present: list[dict[str, Any]] = []
    if chars_path.exists():
        chars_data = json.loads(chars_path.read_text(encoding="utf-8"))
        for c in chars_data.get("characters", []):
            if c.get("total_appearances", 0) > 0:
                characters_present.append({
                    "character": c["name"],
                    "confidence": c["confidence"],
                    "appearances": c["total_appearances"],
                })

    entry: dict[str, Any] = {
        "id": source_id,
        "path": str(video_path),
        "fandom": fandom,
        "source_type": source_type,
        "media": media,
        "derived": {},
        "flags": flags,
        "added_at": _now_iso(),
    }
    if title:
        entry["title"] = title
    if year:
        entry["year"] = year
    if transcript_path.exists():
        entry["derived"]["transcript"] = str(transcript_path)
    if scenes_path.exists():
        entry["derived"]["scenes"] = str(scenes_path)
    if clip_manifest_path.exists():
        entry["derived"]["clip_embeddings"] = str(clip_npz_path)
    if chars_path.exists():
        entry["derived"]["character_tags"] = str(chars_path)
    if characters_present:
        entry["characters_present"] = characters_present

    _upsert_source(catalog, entry)
    catalog["generated_at"] = _now_iso()
    validate_and_write(catalog, "source-catalog", catalog_path)
    report.steps.append(IngestStep(
        name="catalog",
        status="ok",
        detail=f"registered {source_id} in source-catalog.json",
        output=str(catalog_path),
    ))

    return report
