"""Reference-edit library ingestion + pattern learning.

Downloads a playlist of fandom-edit videos, analyzes each for cut rhythm,
shot duration distribution, and pacing curve, and aggregates the statistics
into `reference-priors.json`. The sync planner reads these priors when
available and biases its choices toward the patterns found in videos the
user already likes.

Download path uses yt-dlp (shelled out — that's the industry standard and
we keep the dep surface minimal). Analysis path uses PySceneDetect.

Storage: references live outside any one project at
~/.fandomforge/references/<corpus_tag>/ — one ingest, many projects benefit.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import statistics
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fandomforge.validation import validate

logger = logging.getLogger(__name__)


def references_root() -> Path:
    env = os.environ.get("FF_REFERENCES_DIR")
    if env:
        return Path(env)
    return Path.home() / ".fandomforge" / "references"


@dataclass
class RefVideo:
    id: str
    title: str
    url: str
    path: Path
    duration_sec: float


# ---------- Download ----------


def _yt_dlp_available() -> bool:
    return shutil.which("yt-dlp") is not None


def list_playlist_entries(playlist_url: str) -> list[dict[str, Any]]:
    """Enumerate a YouTube playlist via yt-dlp --flat-playlist.

    Returns each entry's id, title, url, duration. Does NOT download.
    """
    if not _yt_dlp_available():
        raise RuntimeError("yt-dlp not installed; cannot enumerate playlist")
    try:
        proc = subprocess.run(
            ["yt-dlp", "--flat-playlist", "-J", playlist_url],
            capture_output=True, text=True, check=True, timeout=60,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"yt-dlp failed: {exc}") from exc
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"yt-dlp returned invalid JSON: {exc}") from exc

    entries = payload.get("entries") or [payload]
    out: list[dict[str, Any]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        vid = e.get("id")
        if not vid:
            continue
        out.append({
            "id": str(vid),
            "title": str(e.get("title") or vid),
            "url": str(e.get("url") or e.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}"),
            "duration_sec": float(e.get("duration") or 0),
        })
    return out


def download_reference_video(
    url: str,
    target_dir: Path,
    *,
    max_height: int = 480,
    video_id: str | None = None,
) -> Path | None:
    """Download a single reference video at low resolution — we only need it
    for scene analysis, not playback.

    Returns the path to THIS video's file (matched by id prefix) rather than
    whatever happens to be first alphabetically in target_dir — otherwise
    every subsequent video in a playlist ingestion re-analyzes the first one.
    """
    if not _yt_dlp_available():
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(target_dir / "%(id)s.%(ext)s")
    args = [
        "yt-dlp",
        "-f", f"bv*[height<={max_height}]+ba/best[height<={max_height}]/best",
        "--merge-output-format", "mp4",
        "-o", outtmpl,
        "--no-playlist",
        url,
    ]
    # When we know the video id (the playlist enumerator knows it), derive it
    # from the URL as a fallback. Pattern: ?v=<id> or /watch?v=<id>
    if video_id is None:
        import re as _re
        m = _re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", url)
        if m:
            video_id = m.group(1)
    try:
        subprocess.run(args, check=True, capture_output=True, text=True, timeout=600)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.warning("yt-dlp download failed for %s: %s", url, exc)
        return None
    if video_id:
        for suffix in (".mp4", ".mkv", ".webm"):
            candidate = target_dir / f"{video_id}{suffix}"
            if candidate.exists():
                return candidate
    return None


# ---------- Analysis ----------


def analyze_reference_video(
    video_path: Path,
    *,
    scene_threshold: float = 3.0,
    min_scene_sec: float = 0.25,
    deep: bool = True,
) -> dict[str, Any] | None:
    """Run the deep analyzer over a reference video.

    When `deep=True` (default), captures pacing curve, visual stats (luma /
    hue / saturation), beat-sync rate, and motion signal in addition to the
    baseline shot-duration distribution.

    `deep=False` keeps the older shot-only signature for tests or fast passes.
    """
    if deep:
        try:
            from fandomforge.intelligence.reference_analyzer_deep import analyze_deep
            result = analyze_deep(video_path)
            return result if result.get("shot_count") else {"shot_count": 0}
        except Exception as exc:  # noqa: BLE001
            logger.warning("deep analyze failed on %s: %s", video_path, exc)
            # fall through to baseline

    try:
        from scenedetect import AdaptiveDetector, detect  # type: ignore
    except ImportError:
        return None

    try:
        scene_list = detect(
            str(video_path),
            AdaptiveDetector(
                adaptive_threshold=scene_threshold,
                min_scene_len=max(1, int(min_scene_sec * 24)),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("scenedetect failed on %s: %s", video_path, exc)
        return None

    durations = [
        end.get_seconds() - start.get_seconds() for start, end in scene_list
    ]
    durations = [d for d in durations if d >= min_scene_sec]
    if not durations:
        return {"shot_count": 0}

    total_duration = sum(durations)
    cuts_per_minute = (len(durations) / total_duration) * 60.0 if total_duration > 0 else 0.0

    return {
        "shot_count": len(durations),
        "avg_shot_duration_sec": round(statistics.mean(durations), 3),
        "median_shot_duration_sec": round(statistics.median(durations), 3),
        "cuts_per_minute": round(cuts_per_minute, 2),
        "min_shot_duration_sec": round(min(durations), 3),
        "max_shot_duration_sec": round(max(durations), 3),
        "shot_duration_stddev_sec": round(
            statistics.pstdev(durations) if len(durations) > 1 else 0.0, 3
        ),
    }


def aggregate_priors(videos: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up per-video metrics into corpus-wide editing priors.

    Pulls rich signals from the deep analyzer when present — beat-sync %,
    luma/saturation, act pacing from real measurements, pacing profile. Each
    signal is optional and only aggregated when the corpus has enough data
    points to be meaningful.
    """
    def _metric(v, key, default=None):
        m = v.get("metrics") or {}
        val = m.get(key, default)
        return val if isinstance(val, (int, float)) else default

    def _bool_metric(v, key):
        m = v.get("metrics") or {}
        return bool(m.get(key))

    valid = [
        v for v in videos
        if (v.get("metrics") or {}).get("shot_count", 0) > 0
    ]

    medians = [_metric(v, "median_shot_duration_sec", 0) for v in valid]
    cpm = [_metric(v, "cuts_per_minute", 0) for v in valid]

    all_durations: list[float] = []
    for v in valid:
        m = v.get("metrics") or {}
        avg = m.get("avg_shot_duration_sec")
        count = int(m.get("shot_count", 0))
        if avg and count:
            all_durations.extend([float(avg)] * count)

    priors: dict[str, Any] = {
        "median_shot_duration_sec": round(statistics.median(medians), 3) if medians else 2.0,
        "cuts_per_minute": round(statistics.mean(cpm), 2) if cpm else 20.0,
    }

    if all_durations:
        all_durations.sort()
        p10_idx = int(len(all_durations) * 0.1)
        p90_idx = max(0, int(len(all_durations) * 0.9) - 1)
        priors["shot_duration_range_sec"] = [
            round(all_durations[p10_idx], 3),
            round(all_durations[p90_idx], 3),
        ]

    # Real act pacing rollup when deep analyzer captured it.
    act_arrays = [_metric_list(v, "act_pacing_pct") for v in valid]
    act_arrays = [a for a in act_arrays if isinstance(a, list) and len(a) == 3]
    if act_arrays:
        priors["typical_act_pacing_pct"] = [
            round(statistics.mean(a[i] for a in act_arrays), 1)
            for i in range(3)
        ]
    else:
        priors["typical_act_pacing_pct"] = [25.0, 45.0, 30.0]

    # Beat-sync rate — the key fandom-edit signal.
    on_beat = [
        _metric(v, "cuts_on_beat_pct")
        for v in valid
        if _bool_metric(v, "beat_sync_available")
        and _metric(v, "cuts_on_beat_pct") is not None
    ]
    if on_beat:
        priors["cuts_on_beat_pct_mean"] = round(statistics.mean(on_beat), 2)

    tempos = [
        _metric(v, "tempo_bpm")
        for v in valid
        if _bool_metric(v, "beat_sync_available")
        and _metric(v, "tempo_bpm")
    ]
    if tempos:
        priors["tempo_bpm_median"] = round(statistics.median(tempos), 2)

    # Visual palette.
    lumas = [_metric(v, "avg_luma") for v in valid]
    lumas = [x for x in lumas if x is not None]
    if lumas:
        priors["avg_luma_mean"] = round(statistics.mean(lumas), 4)

    darks = [_metric(v, "dark_shot_pct") for v in valid]
    darks = [x for x in darks if x is not None]
    if darks:
        priors["dark_shot_pct_mean"] = round(statistics.mean(darks), 2)

    brights = [_metric(v, "bright_shot_pct") for v in valid]
    brights = [x for x in brights if x is not None]
    if brights:
        priors["bright_shot_pct_mean"] = round(statistics.mean(brights), 2)

    sats = [_metric(v, "saturation_mean") for v in valid]
    sats = [x for x in sats if x is not None]
    if sats:
        priors["saturation_mean_mean"] = round(statistics.mean(sats), 4)

    intros = [_metric(v, "intro_to_first_cut_sec") for v in valid]
    intros = [x for x in intros if x is not None]
    if intros:
        priors["intro_to_first_cut_sec_median"] = round(statistics.median(intros), 3)

    # Pacing profile — classify the corpus's pacing shape.
    priors["pacing_profile"] = _classify_pacing(priors.get("cuts_per_minute", 0))

    return priors


def _metric_list(v: dict[str, Any], key: str) -> list[float] | None:
    m = v.get("metrics") or {}
    val = m.get(key)
    return val if isinstance(val, list) else None


def _classify_pacing(cpm: float) -> str:
    """Bucket a corpus's cuts-per-minute into a human-readable pacing profile."""
    if cpm < 15:
        return "slow-burn"
    if cpm < 25:
        return "steady"
    if cpm < 35:
        return "escalator"
    if cpm < 50:
        return "peaks-and-valleys"
    return "machine-gun"


def ingest_playlist(
    playlist_url: str,
    *,
    tag: str,
    max_videos: int | None = None,
    max_height: int = 480,
    download: bool = True,
) -> dict[str, Any]:
    """Enumerate a playlist, download each video, analyze, and emit priors.

    Set `download=False` to skip the download step — useful when videos are
    already present under <references_root>/<tag>/.
    """
    target_dir = references_root() / tag
    target_dir.mkdir(parents=True, exist_ok=True)

    entries = list_playlist_entries(playlist_url)
    if max_videos is not None:
        entries = entries[:max_videos]

    analyzed: list[dict[str, Any]] = []
    for e in entries:
        target_file = target_dir / f"{e['id']}.mp4"
        if download and not target_file.exists():
            dl = download_reference_video(
                e["url"], target_dir, max_height=max_height, video_id=e["id"],
            )
            if dl is not None:
                target_file = dl

        # When no-download mode is requested, also check for existing files
        # in other extensions (users may have manually dropped them in).
        if not target_file.exists():
            for suffix in (".mkv", ".webm"):
                alt = target_dir / f"{e['id']}{suffix}"
                if alt.exists():
                    target_file = alt
                    break

        metrics: dict[str, Any] = {}
        if target_file.exists():
            m = analyze_reference_video(target_file)
            if m is not None:
                metrics = m

        analyzed.append({
            "id": e["id"],
            "title": e["title"],
            "url": e["url"],
            "duration_sec": float(e.get("duration_sec") or 0),
            "metrics": metrics,
        })

    priors_payload: dict[str, Any] = {
        "schema_version": 1,
        "tag": tag,
        "source_playlists": [playlist_url],
        "video_count": len(analyzed),
        "videos": analyzed,
        "priors": aggregate_priors(analyzed),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "ff reference ingest",
    }
    validate(priors_payload, "reference-priors")

    out = target_dir / "reference-priors.json"
    out.write_text(json.dumps(priors_payload, indent=2) + "\n", encoding="utf-8")
    return priors_payload


def load_priors(tag: str | None = None) -> dict[str, Any] | None:
    """Load reference-priors from the references root.

    If `tag` is None, returns the most-recently-generated priors across all tags.
    """
    root = references_root()
    if not root.exists():
        return None
    candidates: list[Path] = []
    if tag:
        p = root / tag / "reference-priors.json"
        if p.exists():
            candidates.append(p)
    else:
        for sub in root.iterdir():
            p = sub / "reference-priors.json"
            if p.exists():
                candidates.append(p)
    if not candidates:
        return None
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    try:
        return json.loads(newest.read_text())
    except (json.JSONDecodeError, OSError):
        return None


__all__ = [
    "RefVideo",
    "aggregate_priors",
    "analyze_reference_video",
    "download_reference_video",
    "ingest_playlist",
    "list_playlist_entries",
    "load_priors",
    "references_root",
]
