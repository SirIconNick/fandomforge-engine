"""OpenAI API integration — transcription, shot description embeddings, GPT assists.

Uses the key in .env (OPENAI_API_KEY). Falls back to local models if the key
isn't set or the API call fails.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env(project_root: Path | str = ".") -> None:
    """Load OPENAI_API_KEY from .env if present. Safe to call repeatedly."""
    if os.environ.get("OPENAI_API_KEY"):
        return

    for env_name in [".env", ".env.local"]:
        env_file = Path(project_root) / env_name
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k == "OPENAI_API_KEY" and v:
                        os.environ["OPENAI_API_KEY"] = v
                        return


def openai_available() -> bool:
    """Check if OpenAI SDK + API key are ready."""
    _load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        return False
    try:
        import openai  # noqa: F401
        return True
    except ImportError:
        return False


@dataclass
class TranscriptionResult:
    success: bool
    text: str = ""
    srt_path: Path | None = None
    error: str = ""


def transcribe_via_openai(
    audio_or_video_path: str | Path,
    output_srt: str | Path,
    *,
    language: str = "en",
    model: str = "whisper-1",
    project_root: Path | str = ".",
) -> TranscriptionResult:
    """Transcribe a file via the OpenAI Whisper API (cloud).

    Faster than running Whisper locally, higher quality than whisper-tiny/base,
    and handles file sizes up to 25 MB by default (ffmpeg-segment if larger).
    """
    _load_env(project_root)
    if not os.environ.get("OPENAI_API_KEY"):
        return TranscriptionResult(
            success=False, error="OPENAI_API_KEY not set"
        )

    try:
        from openai import OpenAI
    except ImportError:
        return TranscriptionResult(
            success=False, error="OpenAI SDK not installed. pip install openai"
        )

    src = Path(audio_or_video_path)
    out = Path(output_srt)
    if not src.exists():
        return TranscriptionResult(
            success=False, error=f"Source not found: {src}"
        )
    out.parent.mkdir(parents=True, exist_ok=True)

    # API has a 25 MB file-size limit. For bigger files, extract audio + compress.
    size_mb = src.stat().st_size / (1024 * 1024)
    if size_mb > 24:
        import shutil
        import subprocess
        import tempfile

        if shutil.which("ffmpeg") is None:
            return TranscriptionResult(success=False, error="ffmpeg required for large files")

        tmp = Path(tempfile.mktemp(suffix=".mp3"))
        # Extract audio at low bitrate — speech only needs ~64kbps
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-nostats",
                "-i", str(src),
                "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "libmp3lame", "-b:a", "48k",
                str(tmp),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if not tmp.exists():
            return TranscriptionResult(success=False, error="Audio extraction failed")
        upload_path = tmp
    else:
        upload_path = src

    try:
        client = OpenAI()
        with upload_path.open("rb") as f:
            response = client.audio.transcriptions.create(
                model=model,
                file=f,
                language=language,
                response_format="srt",
            )
        # response is a string when response_format is "srt"
        srt_text = response if isinstance(response, str) else str(response)
        out.write_text(srt_text, encoding="utf-8")
        return TranscriptionResult(
            success=True,
            text=srt_text,
            srt_path=out,
        )
    except Exception as exc:
        return TranscriptionResult(success=False, error=str(exc))


def rank_shot_descriptions(
    query: str,
    descriptions: list[str],
    top_k: int = 10,
    project_root: Path | str = ".",
) -> list[tuple[int, float, str]]:
    """Rank a list of shot descriptions by how well they match the query.

    Uses OpenAI embeddings (text-embedding-3-small — cheap, fast, 1536 dim).
    Returns list of (index, score, description) sorted by score desc.
    """
    _load_env(project_root)
    if not os.environ.get("OPENAI_API_KEY"):
        return []

    try:
        from openai import OpenAI
    except ImportError:
        return []

    client = OpenAI()

    try:
        # Embed query
        q_resp = client.embeddings.create(
            model="text-embedding-3-small",
            input=query,
        )
        q_emb = q_resp.data[0].embedding

        # Embed all descriptions in one call (supports up to ~8k tokens)
        d_resp = client.embeddings.create(
            model="text-embedding-3-small",
            input=descriptions,
        )
        d_embs = [d.embedding for d in d_resp.data]
    except Exception:
        return []

    # Cosine similarity
    def cos(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    scored = [(i, cos(q_emb, e), descriptions[i]) for i, e in enumerate(d_embs)]
    scored.sort(key=lambda x: -x[1])
    return scored[:top_k]
