"""Lyric-alignment scorer for reference videos.

Runs whisper on the reference video's audio track (extracted to a temp WAV),
then measures how tightly the cuts are aligned to word-starts and
phrase-boundaries. Distinct from beat-sync (rhythm) — this is meaning-sync,
the signal that the editor is actually listening to the lyrics.

Whisper runs are cached per video (keyed by content hash) under
<references_root>/<tag>/.transcripts/<video-id>.json so re-analysis is free
after the first pass.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _content_hash(path: Path) -> str:
    """Hash first 64KB + size as a cheap content fingerprint."""
    try:
        stat = path.stat()
        with open(path, "rb") as f:
            head = f.read(64 * 1024)
    except OSError:
        return ""
    h = hashlib.sha256()
    h.update(str(stat.st_size).encode())
    h.update(head)
    return h.hexdigest()[:24]


def _cache_path(video: Path) -> Path:
    """Transcripts cached next to the video under .transcripts/."""
    key = _content_hash(video)
    return video.parent / ".transcripts" / f"{video.stem}.{key}.json"


def _extract_audio(video: Path, out_wav: Path) -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-i", str(video),
             "-vn", "-ac", "1", "-ar", "16000",
             "-acodec", "pcm_s16le",
             str(out_wav)],
            check=True, capture_output=True, timeout=180,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return out_wav.exists() and out_wav.stat().st_size > 1000


def _transcribe(audio: Path, model_size: str = "base") -> dict[str, Any] | None:
    """Run whisper with word timestamps. Returns the segment list or None."""
    try:
        import whisper  # type: ignore
    except ImportError:
        return None

    from fandomforge.ingest import WHISPER_CACHE

    try:
        model = whisper.load_model(model_size, download_root=str(WHISPER_CACHE))
        result = model.transcribe(
            str(audio),
            language="en",
            verbose=False,
            word_timestamps=True,
            condition_on_previous_text=False,  # speedup for music
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("whisper failed on %s: %s", audio, exc)
        return None

    segments: list[dict[str, Any]] = []
    for seg in result.get("segments") or []:
        words: list[dict[str, Any]] = []
        for w in seg.get("words") or []:
            words.append({
                "word": str(w.get("word", "")).strip(),
                "start": float(w.get("start", 0.0)),
                "end": float(w.get("end", 0.0)),
            })
        segments.append({
            "start": float(seg.get("start", 0.0)),
            "end": float(seg.get("end", 0.0)),
            "text": str(seg.get("text", "")).strip(),
            "words": words,
        })
    return {"segments": segments}


def _load_or_transcribe(video: Path, model_size: str) -> dict[str, Any] | None:
    cache = _cache_path(video)
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        if not _extract_audio(video, wav):
            return None
        transcript = _transcribe(wav, model_size=model_size)
        if transcript is None:
            return None

    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(transcript))
    except OSError:
        pass
    return transcript


def score_lyric_alignment(
    video: Path,
    cut_times: list[float],
    *,
    model_size: str = "base",
    word_tol_sec: float = 0.10,
    phrase_tol_sec: float = 0.25,
    phrase_gap_sec: float = 0.6,
) -> dict[str, Any]:
    """Compute alignment of cut_times against the reference's transcribed words.

    - cuts_on_word_boundary_pct: cuts within `word_tol_sec` of a word start
    - cuts_on_phrase_boundary_pct: cuts within `phrase_tol_sec` of a phrase
      break (silence >= phrase_gap_sec between words)

    Returns `{"available": False}` when whisper / audio extraction isn't
    available or the video has no speech.
    """
    if not cut_times:
        return {"available": False}

    transcript = _load_or_transcribe(video, model_size)
    if not transcript:
        return {"available": False}

    segments = transcript.get("segments") or []
    word_starts: list[float] = []
    for seg in segments:
        for w in seg.get("words") or []:
            ws = float(w.get("start", 0.0))
            if ws > 0:
                word_starts.append(ws)
    word_starts.sort()

    if not word_starts:
        return {"available": False, "reason": "no words detected"}

    # Phrase boundaries: a phrase ends where a word-end is followed by a
    # silence of at least `phrase_gap_sec` before the next word-start.
    word_pairs: list[tuple[float, float]] = []
    for seg in segments:
        for w in seg.get("words") or []:
            word_pairs.append(
                (float(w.get("start", 0.0)), float(w.get("end", 0.0)))
            )
    word_pairs.sort()
    phrase_starts: list[float] = []
    for i, (_start, end) in enumerate(word_pairs):
        nxt = word_pairs[i + 1][0] if i + 1 < len(word_pairs) else None
        if nxt is None or nxt - end >= phrase_gap_sec:
            if nxt is not None:
                phrase_starts.append(nxt)

    def _within(target: float, sorted_list: list[float], tol: float) -> bool:
        if not sorted_list:
            return False
        i = bisect.bisect_left(sorted_list, target)
        for j in (i - 1, i):
            if 0 <= j < len(sorted_list):
                if abs(sorted_list[j] - target) <= tol:
                    return True
        return False

    on_word = sum(1 for c in cut_times if _within(c, word_starts, word_tol_sec))
    on_phrase = sum(1 for c in cut_times if _within(c, phrase_starts, phrase_tol_sec))

    return {
        "available": True,
        "cuts_checked": len(cut_times),
        "cuts_on_word_boundary_pct": round(on_word / len(cut_times) * 100.0, 2),
        "cuts_on_phrase_boundary_pct": round(on_phrase / len(cut_times) * 100.0, 2),
        "word_count": len(word_starts),
        "phrase_count": len(phrase_starts),
    }


__all__ = ["score_lyric_alignment"]
