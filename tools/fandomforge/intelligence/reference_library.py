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


def _yt_dlp_auth_args() -> list[str]:
    """Optional cookie/auth args pulled from env. Used to unblock YouTube
    bot-checks without requiring a code change.

    FF_YT_DLP_COOKIES_BROWSER: browser name (e.g. "chrome", "firefox", "brave")
    FF_YT_DLP_COOKIES_FILE: path to cookies.txt (takes precedence)
    FF_YT_DLP_EXTRA_ARGS: freeform extra args, space-separated
    """
    args: list[str] = []
    cookies_file = os.environ.get("FF_YT_DLP_COOKIES_FILE")
    if cookies_file:
        args.extend(["--cookies", cookies_file])
    else:
        cookies_browser = os.environ.get("FF_YT_DLP_COOKIES_BROWSER")
        if cookies_browser:
            args.extend(["--cookies-from-browser", cookies_browser])
    extra = os.environ.get("FF_YT_DLP_EXTRA_ARGS")
    if extra:
        args.extend(extra.split())
    return args


_SINGLE_VIDEO_RE = None  # lazy-compiled in _is_single_video_url


def _is_single_video_url(url: str) -> bool:
    """True for YouTube single-video URLs (watch?v=... or youtu.be/...).

    yt-dlp's `--flat-playlist -J` triggers the full video extractor (and the
    anti-bot signature challenge) when fed a single-video URL. Playlist URLs
    stay at the cheap metadata layer. We route singles through the gentler
    `--dump-json --skip-download --no-playlist` path instead.
    """
    import re as _re
    global _SINGLE_VIDEO_RE
    if _SINGLE_VIDEO_RE is None:
        _SINGLE_VIDEO_RE = _re.compile(
            r"(?:youtube\.com/watch\?v=|youtu\.be/)[A-Za-z0-9_-]{6,}",
            _re.IGNORECASE,
        )
    lower = url.lower()
    # Any `list=` param (either playlist?list=... or watch?v=X&list=Y) means
    # yt-dlp will treat the URL as a playlist — don't route to single path.
    if "list=" in lower:
        return False
    return bool(_SINGLE_VIDEO_RE.search(url))


def _single_video_entry(url: str) -> dict[str, Any] | None:
    """Resolve a single video URL to the same dict shape `list_playlist_entries`
    returns — via the metadata-only yt-dlp call path."""
    try:
        proc = subprocess.run(
            ["yt-dlp", *_yt_dlp_auth_args(),
             "--dump-json", "--skip-download", "--no-playlist", url],
            capture_output=True, text=True, check=True, timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    vid = payload.get("id")
    if not vid:
        return None
    return {
        "id": str(vid),
        "title": str(payload.get("title") or vid),
        "url": str(payload.get("webpage_url") or url),
        "duration_sec": float(payload.get("duration") or 0),
    }


def list_playlist_entries(playlist_url: str) -> list[dict[str, Any]]:
    """Enumerate a YouTube playlist via yt-dlp --flat-playlist.

    For single-video URLs (watch?v=... / youtu.be/...), routes through the
    metadata-only path instead — `--flat-playlist` triggers the video-player
    bot-check on those now.

    Returns each entry's id, title, url, duration. Does NOT download.
    """
    if not _yt_dlp_available():
        raise RuntimeError("yt-dlp not installed; cannot enumerate playlist")

    if _is_single_video_url(playlist_url):
        entry = _single_video_entry(playlist_url)
        if entry is None:
            raise RuntimeError(f"yt-dlp failed: could not resolve single video {playlist_url}")
        return [entry]

    try:
        proc = subprocess.run(
            ["yt-dlp", *_yt_dlp_auth_args(),
             "--flat-playlist", "-J", playlist_url],
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
        *_yt_dlp_auth_args(),
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


def _weighted_mean(values_and_weights: list[tuple[float, float]]) -> float | None:
    if not values_and_weights:
        return None
    total_w = sum(w for _v, w in values_and_weights)
    if total_w <= 0:
        return None
    return sum(v * w for v, w in values_and_weights) / total_w


def _weighted_median(values_and_weights: list[tuple[float, float]]) -> float | None:
    if not values_and_weights:
        return None
    pairs = sorted(values_and_weights, key=lambda p: p[0])
    total_w = sum(w for _v, w in pairs)
    if total_w <= 0:
        return None
    cum = 0.0
    half = total_w / 2.0
    for v, w in pairs:
        cum += w
        if cum >= half:
            return v
    return pairs[-1][0]


def _tier_only_priors(videos: list[dict[str, Any]], tier: str) -> dict[str, Any] | None:
    """Run aggregate_priors on just the subset of videos at a given tier.

    Returns None when the subset is too small to be meaningful (< 5 videos).
    """
    tier_videos = [
        v for v in videos if (v.get("quality_tier") or "") == tier
    ]
    if len(tier_videos) < 5:
        return None
    subset = aggregate_priors(tier_videos, _skip_tiered=True)
    subset["video_count"] = len(tier_videos)
    return subset


def aggregate_priors(
    videos: list[dict[str, Any]],
    *,
    _skip_tiered: bool = False,
) -> dict[str, Any]:
    """Roll up per-video metrics into corpus-wide editing priors.

    When videos carry `quality_score`, each video's contribution to the
    median / mean is weighted by `quality_score / 100` — so S-tier edits
    (95+) drive the priors ~1.5x harder than C-tier (65) videos. This is
    how the planner learns from the best edits, not the average.

    The caller gets `s_tier_only` and `a_tier_only` sub-priors when the
    corpus has >=5 videos in those tiers — lets the sync planner
    deliberately target the "excellent" signature when enough data exists.
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

    # Per-video weight. Videos without a quality_score get a neutral 1.0 so
    # pre-quality data still aggregates sanely.
    def _weight(v: dict[str, Any]) -> float:
        q = v.get("quality_score")
        if isinstance(q, (int, float)) and q > 0:
            # 50 → 1.0, 100 → 2.0, 0 → 0.0. Linear scaling bounded at 0.2.
            return max(0.2, float(q) / 50.0)
        return 1.0

    medians_w = [
        (_metric(v, "median_shot_duration_sec", 0), _weight(v))
        for v in valid
        if _metric(v, "median_shot_duration_sec", 0)
    ]
    cpm_w = [
        (_metric(v, "cuts_per_minute", 0), _weight(v))
        for v in valid
        if _metric(v, "cuts_per_minute", 0)
    ]

    all_durations: list[float] = []
    for v in valid:
        m = v.get("metrics") or {}
        avg = m.get("avg_shot_duration_sec")
        count = int(m.get("shot_count", 0))
        if avg and count:
            # Weight percentile contributions by quality too.
            w = _weight(v)
            all_durations.extend([float(avg)] * max(1, int(count * w)))

    median_shot = _weighted_median(medians_w)
    mean_cpm = _weighted_mean(cpm_w)

    priors: dict[str, Any] = {
        "median_shot_duration_sec": round(median_shot, 3) if median_shot else 2.0,
        "cuts_per_minute": round(mean_cpm, 2) if mean_cpm else 20.0,
        "quality_weighted": any("quality_score" in v for v in valid),
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

    # Tier breakdown and per-tier sub-priors.
    if not _skip_tiered:
        tier_counts: dict[str, int] = {}
        for v in valid:
            t = v.get("quality_tier")
            if t:
                tier_counts[t] = tier_counts.get(t, 0) + 1
        if tier_counts:
            priors["tier_samples"] = tier_counts
        s_only = _tier_only_priors(valid, "S")
        if s_only:
            priors["s_tier_only"] = s_only
        a_only = _tier_only_priors(valid, "A")
        if a_only:
            priors["a_tier_only"] = a_only

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
    lyric_sample_n: int = 0,
) -> dict[str, Any]:
    """Enumerate a playlist, download each video, analyze, and emit priors.

    Set `download=False` to skip the download step — useful when videos are
    already present under <references_root>/<tag>/.

    `lyric_sample_n` runs whisper-based lyric alignment on the first N
    videos (0 = skip). Whisper is ~2-5 min per video so small samples are
    the norm. The signal generalizes well — 5 videos per corpus is enough
    to see whether editors in that corpus sync to lyrics or only to beats.
    """
    target_dir = references_root() / tag
    target_dir.mkdir(parents=True, exist_ok=True)

    # no-download re-analysis: enumerate existing video files in the tag dir
    # instead of calling yt-dlp. file:// URLs are the sentinel the CLI uses
    # when --no-download + --playlist omitted.
    if not download and playlist_url.startswith("file://"):
        entries = []
        for child in sorted(target_dir.iterdir()):
            if child.suffix.lower() not in (".mp4", ".mkv", ".webm"):
                continue
            entries.append({
                "id": child.stem,
                "title": child.stem,
                "url": str(child),
                "duration_sec": 0.0,
            })
    else:
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
            # Enrich with transition + motion signals whenever we have shot
            # boundaries to classify.
            try:
                from fandomforge.intelligence.reference_analyzer_deep import (
                    _scene_boundaries,
                )
                from fandomforge.intelligence.reference_transitions import (
                    classify_transitions,
                )
                from fandomforge.intelligence.reference_motion import (
                    classify_motion_cuts,
                )
                boundaries = _scene_boundaries(target_file)
                if boundaries:
                    tr = classify_transitions(target_file, boundaries)
                    if tr.get("sample_count"):
                        metrics["transitions"] = tr
                    mo = classify_motion_cuts(target_file, boundaries)
                    if mo.get("sample_count"):
                        metrics["motion_cuts"] = mo
                    # Lyric alignment on a sample of the corpus — whisper is
                    # expensive so we cap it. len(analyzed) is how many we've
                    # already processed in this ingest pass.
                    if lyric_sample_n > 0 and len(analyzed) < lyric_sample_n:
                        from fandomforge.intelligence.reference_lyrics import (
                            score_lyric_alignment,
                        )
                        cut_times = [s for s, _e in boundaries[1:]]
                        la = score_lyric_alignment(target_file, cut_times)
                        if la.get("available"):
                            metrics["lyric_alignment"] = la
            except Exception as exc:  # noqa: BLE001
                logger.warning("transition/motion/lyric enrich failed: %s", exc)

        youtube_metadata: dict[str, Any] = {}
        try:
            yt = fetch_youtube_metadata(e["url"])
            if yt is not None:
                youtube_metadata = yt
        except Exception as exc:  # noqa: BLE001
            logger.warning("youtube metadata fetch failed for %s: %s", e["id"], exc)

        entry: dict[str, Any] = {
            "id": e["id"],
            "title": e["title"],
            "url": e["url"],
            "duration_sec": float(e.get("duration_sec") or 0),
            "metrics": metrics,
        }
        if youtube_metadata:
            entry["youtube_metadata"] = youtube_metadata
        analyzed.append(entry)

    # Audience reference = 90th percentile view count, robust against a
    # single mega-viral outlier skewing every other video into single digits.
    view_counts = sorted([
        int((v.get("youtube_metadata") or {}).get("view_count") or 0)
        for v in analyzed
        if isinstance((v.get("youtube_metadata") or {}).get("view_count"), (int, float))
    ])
    if view_counts:
        p90_idx = max(0, int(len(view_counts) * 0.9) - 1)
        audience_ref = view_counts[p90_idx] or max(view_counts)
    else:
        audience_ref = None
    for v in analyzed:
        q = score_quality(v, corpus_audience_reference=audience_ref)
        v["quality_score"] = q["quality_score"]
        v["quality_tier"] = q["quality_tier"]
        v["quality_components"] = q["components"]

    out = target_dir / "reference-priors.json"

    # Merge with any existing priors file at this tag. Singles + repeat-ingests
    # used to overwrite the file, losing every video except the last one — so
    # a 5-single batch ended up with video_count=1. Now we accumulate by id
    # (new analysis replaces the old for the same video) and dedupe source
    # playlist URLs.
    merged_videos: dict[str, dict[str, Any]] = {}
    merged_sources: list[str] = []
    if out.exists():
        try:
            prior = json.loads(out.read_text(encoding="utf-8"))
            for v in prior.get("videos") or []:
                vid = v.get("id")
                if vid:
                    merged_videos[vid] = v
            for src in prior.get("source_playlists") or []:
                if src not in merged_sources:
                    merged_sources.append(src)
        except (json.JSONDecodeError, OSError):
            pass
    for v in analyzed:
        vid = v.get("id")
        if vid:
            merged_videos[vid] = v
    if playlist_url not in merged_sources:
        merged_sources.append(playlist_url)

    all_videos = list(merged_videos.values())

    priors_payload: dict[str, Any] = {
        "schema_version": 1,
        "tag": tag,
        "source_playlists": merged_sources,
        "video_count": len(all_videos),
        "videos": all_videos,
        "priors": aggregate_priors(all_videos),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "ff reference ingest",
    }
    validate(priors_payload, "reference-priors")

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


# Thresholds calibrated against the empirical distribution of the 148-video
# corpus — mid-60s is genuinely great work once all signals are computed; 85+
# is the rare edit that has audience, craft, and discipline all at once.
QUALITY_TIER_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (82.0, "S"),
    (73.0, "A"),
    (65.0, "B"),
    (55.0, "C"),
    (0.0, "D"),
)


def score_quality(
    video_entry: dict[str, Any],
    *,
    corpus_audience_reference: int | None = None,
) -> dict[str, Any]:
    """Compute a 0-100 quality score for a single video entry.

    Components (all 0-100):

    - 25% audience: view_count normalized against corpus_audience_reference
      (typically the 90th-percentile view count — robust against outliers
      like a non-fandom video with 60M+ views)
    - 15% transition variety: entropy of transition distribution
    - 20% beat-sync %: rhythm discipline from deep analyzer
    - 15% lyric-boundary sync %: meaning discipline from whisper alignment
    - 15% motion continuity: match_cut + impact_cut rate
    - 10% like ratio: likes / views from yt-dlp metadata

    When a signal is completely missing (e.g. whisper never ran on this
    video) the component falls back to the corpus-neutral 50 so we don't
    punish videos for signals we didn't compute. When a signal IS computed
    but low, the actual value stands.
    """
    metrics = video_entry.get("metrics") or {}
    yt = video_entry.get("youtube_metadata") or {}
    transitions = metrics.get("transitions") or {}
    lyric = metrics.get("lyric_alignment") or {}
    motion = metrics.get("motion_cuts") or {}

    comp: dict[str, float] = {}
    MISSING_NEUTRAL = 50.0

    # Audience reception — cap at 100 when the video meets or exceeds the
    # corpus reference (90th percentile). Linear below that.
    vc = yt.get("view_count")
    if isinstance(vc, (int, float)) and vc > 0 and corpus_audience_reference:
        comp["audience"] = min(100.0, (vc / corpus_audience_reference) * 100.0)
    else:
        comp["audience"] = MISSING_NEUTRAL

    # Craft: transition variety entropy (normalized 0..1 → 0..100)
    ent = transitions.get("variety_entropy_normalized")
    if isinstance(ent, (int, float)):
        comp["variety"] = float(ent) * 100.0
    else:
        comp["variety"] = MISSING_NEUTRAL

    # Rhythm discipline
    bs = metrics.get("cuts_on_beat_pct")
    comp["beat_sync"] = float(bs) if isinstance(bs, (int, float)) else MISSING_NEUTRAL

    # Meaning discipline — only substitute the neutral when whisper didn't
    # run at all (lyric.available is explicitly missing or False).
    if lyric.get("available"):
        lb = lyric.get("cuts_on_phrase_boundary_pct") or 0.0
        wb = lyric.get("cuts_on_word_boundary_pct") or 0.0
        comp["lyric_sync"] = float(lb) * 0.6 + float(wb) * 0.4
    else:
        comp["lyric_sync"] = MISSING_NEUTRAL

    # Motion continuity
    mc = motion.get("continuity_score")
    comp["motion"] = float(mc) if isinstance(mc, (int, float)) else MISSING_NEUTRAL

    # Audience approval — like ratio at ~3% is typical for viral fandom
    # edits; normalize so 3% → 100, cap at 100. Missing = neutral.
    lr = yt.get("like_ratio")
    if isinstance(lr, (int, float)):
        comp["approval"] = min(100.0, float(lr) / 0.03 * 100.0)
    else:
        comp["approval"] = MISSING_NEUTRAL

    weights = {
        "audience": 0.25,
        "variety": 0.15,
        "beat_sync": 0.20,
        "lyric_sync": 0.15,
        "motion": 0.15,
        "approval": 0.10,
    }
    score = sum(comp[k] * weights[k] for k in weights)
    score = round(max(0.0, min(100.0, score)), 2)

    tier = "D"
    for threshold, name in QUALITY_TIER_THRESHOLDS:
        if score >= threshold:
            tier = name
            break

    return {
        "quality_score": score,
        "quality_tier": tier,
        "components": {k: round(v, 2) for k, v in comp.items()},
    }


def fetch_youtube_metadata(url: str) -> dict[str, Any] | None:
    """Pull view_count / like_count / upload_date / channel / duration without
    downloading the video. Cheap enough to call for every video in a corpus
    (yt-dlp caches internally and YouTube tolerates metadata rates).

    Returns None when yt-dlp is unavailable or the fetch fails — callers
    should treat metadata as optional.
    """
    if not _yt_dlp_available():
        return None
    try:
        proc = subprocess.run(
            ["yt-dlp", *_yt_dlp_auth_args(),
             "--dump-json", "--skip-download", "--no-playlist", url],
            capture_output=True, text=True, check=True, timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None

    view_count = payload.get("view_count")
    like_count = payload.get("like_count")
    duration = payload.get("duration")

    # like_ratio = likes / views. Fall back to None when either missing.
    like_ratio = None
    if isinstance(view_count, (int, float)) and view_count > 0 \
       and isinstance(like_count, (int, float)):
        like_ratio = round(like_count / view_count, 6)

    return {
        "view_count": int(view_count) if isinstance(view_count, (int, float)) else None,
        "like_count": int(like_count) if isinstance(like_count, (int, float)) else None,
        "like_ratio": like_ratio,
        "duration_sec": float(duration) if isinstance(duration, (int, float)) else None,
        "channel": str(payload.get("channel") or payload.get("uploader") or ""),
        "upload_date": str(payload.get("upload_date") or ""),  # YYYYMMDD
        "title": str(payload.get("title") or ""),
    }


# ---------------------------------------------------------------------------
# Phase 0.5.2 / 0.5.3 helpers — corpus expansion + per-bucket priors
# ---------------------------------------------------------------------------


# Tag prefix → edit_type. Tag convention: "<prefix>-pl<N>" or
# "<prefix>-singles". `tag.split("-")[0]` extracts the prefix.
TAG_PREFIX_TO_EDIT_TYPE: dict[str, str | None] = {
    "action":    "action",
    "dance":     "dance_movement",
    "sad":       "sad_emotional",
    "emotional": "emotional",
    "tribute":   "tribute",
    "dialogue":  "dialogue_narrative",
    "shipping":  "shipping",
    "comedy":    "comedy",
    "speed":     "speed_amv",
    "cinematic": "cinematic",
    "hype":      "hype_trailer",
    "mixed":     None,  # explicitly held back from per-edit-type buckets
}


def edit_type_for_tag(tag: str) -> str | None:
    """Map a corpus tag (e.g. 'action-pl1', 'dance-singles', 'sad-pl3') to
    the edit_type it carries. Returns None for 'mixed-*' tags or unknown
    prefixes — callers should fall back to the global priors then.

    Phase 0.5.3 prerequisite.
    """
    if not tag:
        return None
    base = tag.split("-")[0].lower()
    return TAG_PREFIX_TO_EDIT_TYPE.get(base)


def list_playlist_metadata_only(
    playlist_url: str,
    *,
    top_n: int = 20,
    max_workers: int = 8,
    enumerate_cap: int | None = None,
) -> list[dict[str, Any]]:
    """Enumerate a playlist's videos AND fetch each video's metadata
    (view_count, like_count, channel, upload_date, title) without
    downloading any video bytes. Sorted by view_count descending.

    Parallelized via ThreadPoolExecutor — yt-dlp metadata fetches are
    IO-bound (network roundtrip ~5s each), so 8 workers cuts total time
    by ~7-8x.

    Phase 0.5.2 validation pass — used by `ff reference validate` to surface
    top-rated content per playlist before committing GB of disk to download.

    Args:
        playlist_url: full YouTube playlist URL (or single-video URL).
        top_n: cap on returned records (sorted by view_count desc).
        max_workers: thread pool size for parallel metadata fetches. Higher
            than ~12 risks YouTube rate-limiting.
        enumerate_cap: if set, only fetch metadata for the first N entries
            from the playlist (still in playlist order). Useful when a
            playlist has hundreds of videos and you only need the top
            handful. None = fetch metadata for every entry, then sort.

    Returns up to `top_n` records. Records that yt-dlp fails to enumerate
    are silently dropped; partial results are returned rather than failing
    the whole batch.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    try:
        entries = list_playlist_entries(playlist_url)
    except RuntimeError:
        return []
    if not entries:
        return []

    if enumerate_cap is not None and enumerate_cap > 0:
        entries = entries[:enumerate_cap]

    def _fetch_one(entry: dict[str, Any]) -> dict[str, Any]:
        meta = fetch_youtube_metadata(entry["url"])
        if meta is None:
            return {
                **entry,
                "view_count": None,
                "like_count": None,
                "channel": "",
                "upload_date": "",
                "metadata_available": False,
            }
        return {**entry, **meta, "metadata_available": True}

    enriched: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = [pool.submit(_fetch_one, e) for e in entries]
        for f in as_completed(futures):
            try:
                enriched.append(f.result())
            except Exception:  # noqa: BLE001 — keep partial results on per-task failure
                continue

    enriched.sort(key=lambda r: -(r.get("view_count") or 0))
    return enriched[:top_n]


def aggregate_priors_per_bucket(
    *,
    refs_root: Path | None = None,
) -> dict[str, Any]:
    """Walk every tag dir under refs_root, group by edit_type_for_tag(tag),
    aggregate priors per (edit_type, fandom_family) bucket, write
    references/priors/<edit_type>/<fandom_family>.json.

    Phase 0.5.3 implementation.

    Returns a summary report:
      {
        "buckets_written": [{path, edit_type, fandom_family, video_count}, ...],
        "buckets_skipped": [{tag, reason}, ...],
        "total_tags_scanned": int,
      }
    """
    root = refs_root or references_root()
    summary: dict[str, Any] = {
        "buckets_written": [],
        "buckets_skipped": [],
        "total_tags_scanned": 0,
    }
    if not root.exists():
        return summary

    # Gather per-tag video metric records
    per_edit_type_videos: dict[str, list[dict[str, Any]]] = {}
    for tag_dir in sorted(root.iterdir()):
        if not tag_dir.is_dir():
            continue
        tag = tag_dir.name
        summary["total_tags_scanned"] += 1
        et = edit_type_for_tag(tag)
        if et is None:
            summary["buckets_skipped"].append({"tag": tag, "reason": "tag prefix not mapped to edit_type"})
            continue
        priors_path = tag_dir / "reference-priors.json"
        if not priors_path.exists():
            summary["buckets_skipped"].append({"tag": tag, "reason": "no reference-priors.json"})
            continue
        try:
            tag_priors = json.loads(priors_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            summary["buckets_skipped"].append({"tag": tag, "reason": "could not read reference-priors.json"})
            continue
        videos = tag_priors.get("videos") or []
        per_edit_type_videos.setdefault(et, []).extend(videos)

    # Write per-bucket priors. For now we ship the simpler layout: one
    # `all.json` per edit_type pooling all fandom_families. Per-fandom
    # split (anime/live-action/kpop/western) is a follow-up once
    # fandom_family classification per video lands.
    priors_dir = root / "priors"
    for edit_type, videos in per_edit_type_videos.items():
        if len(videos) < 5:
            summary["buckets_skipped"].append({
                "edit_type": edit_type,
                "reason": f"only {len(videos)} videos; need ≥5 for stable aggregate",
            })
            continue
        out_dir = priors_dir / edit_type
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "all.json"
        agg = aggregate_priors(videos)
        payload = {
            "schema_version": 1,
            "edit_type": edit_type,
            "fandom_family": "all",
            "video_count": len(videos),
            "priors": agg,
            "generated_at": _now_iso(),
            "generator": "ff reference rebuild-priors",
        }
        out_path.write_text(json.dumps(payload, indent=2))
        summary["buckets_written"].append({
            "path": str(out_path),
            "edit_type": edit_type,
            "fandom_family": "all",
            "video_count": len(videos),
        })

    return summary


def load_per_bucket_priors(
    edit_type: str,
    *,
    fandom_family: str = "all",
    refs_root: Path | None = None,
) -> dict[str, Any] | None:
    """Try to load `references/priors/<edit_type>/<fandom_family>.json`.
    Returns None when the bucket file doesn't exist — caller should fall
    back to the global reference-priors.json.

    Used by sync_planner to pick the right priors per render."""
    root = refs_root or references_root()
    p = root / "priors" / edit_type / f"{fandom_family}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("priors") or data
    except (json.JSONDecodeError, OSError):
        return None


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "QUALITY_TIER_THRESHOLDS",
    "RefVideo",
    "TAG_PREFIX_TO_EDIT_TYPE",
    "aggregate_priors",
    "aggregate_priors_per_bucket",
    "analyze_reference_video",
    "download_reference_video",
    "edit_type_for_tag",
    "fetch_youtube_metadata",
    "ingest_playlist",
    "list_playlist_entries",
    "list_playlist_metadata_only",
    "load_per_bucket_priors",
    "load_priors",
    "references_root",
    "score_quality",
]
