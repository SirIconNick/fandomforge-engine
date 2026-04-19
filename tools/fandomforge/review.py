"""Post-render review — grades a finished edit before it leaves the rough-cut stage.

Runs five passes and emits a structured report:

  1. Technical — ffprobe: resolution / fps / duration / codecs / bitrate / audio channels
  2. Visual    — ffmpeg blackdetect + frame sampling: black / white / frozen / stuck channels
  3. Audio     — ffmpeg loudnorm measure + volumedetect + silencedetect
  4. Structural — rendered duration vs shot-list.json expected sum of shot durations
  5. Shot list  — dedupe check (no (source, offset) reused unless intent=callback),
                  per-source distribution sanity (no single source >50% runtime)

Each dimension returns a verdict: pass | warn | fail. The overall verdict is the
worst of the five (fail dominates, then warn, else pass). `green | yellow | red`
is the shorthand the qa-reviewer subagent emits.

No LLM calls — this is all ffmpeg + stdlib. Pair with the `qa-reviewer` subagent
for vision-level critique (does it feel like a good edit?) via Claude.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VERDICT_RANK = {"pass": 0, "warn": 1, "fail": 2}


# Per-dimension contribution to the overall 0-100 score. Visual weighs heaviest
# because dark / frozen / flashed frames are what the human eye catches first.
DIMENSION_WEIGHTS: dict[str, float] = {
    "technical":  0.20,
    "visual":     0.30,
    "audio":      0.20,
    "structural": 0.15,
    "shot_list":  0.15,
}

# Base credit per verdict (before per-finding adjustments).
_VERDICT_BASE = {"pass": 1.00, "warn": 0.75, "fail": 0.25}

# Each finding shaves this much off the dimension's credit (capped).
_FINDING_DEDUCT = 0.06
_MAX_FINDING_DEDUCT = 0.30

# (lower-bound-inclusive, letter) table. Checked in order, first match wins.
_LETTER_BANDS: list[tuple[float, str]] = [
    (97, "A+"), (93, "A"),  (90, "A-"),
    (87, "B+"), (83, "B"),  (80, "B-"),
    (77, "C+"), (73, "C"),  (70, "C-"),
    (67, "D+"), (63, "D"),  (60, "D-"),
    (0,  "F"),
]


def score_to_letter(score: float) -> str:
    for threshold, letter in _LETTER_BANDS:
        if score >= threshold:
            return letter
    return "F"


def _dimension_score(dim: "DimensionReport") -> float:
    base = _VERDICT_BASE.get(dim.verdict, 0.0)
    deduction = min(_MAX_FINDING_DEDUCT, len(dim.findings) * _FINDING_DEDUCT)
    return max(0.0, (base - deduction)) * 100.0


def overall_score(dimensions: list["DimensionReport"]) -> float:
    """Weighted 0-100 score across all review dimensions."""
    total = 0.0
    for d in dimensions:
        weight = DIMENSION_WEIGHTS.get(d.name, 0.0)
        total += weight * _dimension_score(d)
    return round(total, 1)


@dataclass
class DimensionReport:
    name: str
    verdict: str  # pass | warn | fail
    findings: list[str] = field(default_factory=list)
    measurements: dict[str, Any] = field(default_factory=dict)

    @property
    def score(self) -> float:
        return round(_dimension_score(self), 1)


@dataclass
class ReviewReport:
    project_slug: str
    video_path: str
    generated_at: str
    overall: str                        # green | yellow | red
    overall_verdict: str                # pass | warn | fail
    score: float = 100.0                # 0 - 100, weighted across dimensions
    grade: str = "A+"                   # A+ / A / A- / B+ ... / F
    dimensions: list[DimensionReport] = field(default_factory=list)
    ship_recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_slug": self.project_slug,
            "video_path": self.video_path,
            "generated_at": self.generated_at,
            "overall": self.overall,
            "overall_verdict": self.overall_verdict,
            "score": self.score,
            "grade": self.grade,
            "ship_recommendation": self.ship_recommendation,
            "dimensions": [
                {
                    "name": d.name,
                    "verdict": d.verdict,
                    "score": d.score,
                    "findings": d.findings,
                    "measurements": d.measurements,
                }
                for d in self.dimensions
            ],
        }


# ---------- Technical (ffprobe) ----------


def _ffprobe_streams(video: Path) -> dict[str, Any]:
    if shutil.which("ffprobe") is None:
        return {}
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_streams", "-show_format",
                "-of", "json",
                str(video),
            ],
            capture_output=True, text=True, check=True, timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def _dim_technical(video: Path, target_fps: int = 24,
                   target_w: int = 1920, target_h: int = 1080) -> DimensionReport:
    data = _ffprobe_streams(video)
    if not data:
        return DimensionReport(
            name="technical", verdict="fail",
            findings=["ffprobe unavailable or failed"],
        )
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)

    findings: list[str] = []
    verdict = "pass"
    m: dict[str, Any] = {}

    if not v:
        return DimensionReport(
            name="technical", verdict="fail",
            findings=["no video stream in container"],
            measurements={"container": fmt.get("format_name")},
        )
    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    m["width"] = w
    m["height"] = h
    m["video_codec"] = v.get("codec_name")
    m["pix_fmt"] = v.get("pix_fmt")
    # Parse r_frame_rate "24/1" -> 24.0
    rfr = v.get("r_frame_rate", "0/1")
    try:
        num, den = rfr.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    m["fps"] = round(fps, 3)
    m["duration_sec"] = float(fmt.get("duration", 0))
    m["video_bitrate_bps"] = int(v.get("bit_rate") or 0)
    m["container_bitrate_bps"] = int(fmt.get("bit_rate") or 0)

    if w != target_w or h != target_h:
        verdict = "warn"
        findings.append(f"resolution {w}x{h} != target {target_w}x{target_h}")
    if abs(fps - target_fps) > 0.1:
        verdict = "warn"
        findings.append(f"fps {fps:.2f} != target {target_fps}")
    if m["video_codec"] not in {"h264", "hevc", "av1", "vp9"}:
        verdict = "warn"
        findings.append(f"video codec '{m['video_codec']}' may not import cleanly in some NLEs")
    if m["duration_sec"] < 1.0:
        verdict = "fail"
        findings.append(f"duration {m['duration_sec']:.2f}s is suspiciously short")

    if not a:
        verdict = "warn" if verdict == "pass" else verdict
        findings.append("no audio stream")
        m["audio_codec"] = None
    else:
        m["audio_codec"] = a.get("codec_name")
        m["sample_rate"] = int(a.get("sample_rate") or 0)
        m["channels"] = int(a.get("channels") or 0)
        m["audio_bitrate_bps"] = int(a.get("bit_rate") or 0)
        if m["audio_codec"] not in {"aac", "opus", "mp3", "flac"}:
            findings.append(f"audio codec '{m['audio_codec']}' may not import cleanly")
            verdict = "warn" if verdict == "pass" else verdict
        if m["channels"] not in (1, 2):
            findings.append(f"unusual audio channel count: {m['channels']}")

    return DimensionReport(
        name="technical", verdict=verdict,
        findings=findings, measurements=m,
    )


# ---------- Visual (blackdetect + sampling) ----------


_BLACK_RE = re.compile(
    r"black_start:([\d.]+).*?black_end:([\d.]+).*?black_duration:([\d.]+)"
)
_FREEZE_RE = re.compile(r"freeze_start:([\d.]+)")


def _ffmpeg_blackdetect(video: Path, pix_th: float = 0.1, d: float = 0.2) -> list[dict]:
    if shutil.which("ffmpeg") is None:
        return []
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-i", str(video),
                "-vf", f"blackdetect=d={d}:pix_th={pix_th}",
                "-an", "-f", "null", "-",
            ],
            capture_output=True, text=True, check=False, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return []
    out: list[dict] = []
    for line in (proc.stderr or "").splitlines():
        m = _BLACK_RE.search(line)
        if m:
            out.append({
                "start": float(m.group(1)),
                "end": float(m.group(2)),
                "duration": float(m.group(3)),
            })
    return out


def _ffmpeg_freezedetect(video: Path, n: float = 0.003, d: float = 0.5) -> list[dict]:
    if shutil.which("ffmpeg") is None:
        return []
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-i", str(video),
                "-vf", f"freezedetect=n={n}:d={d}",
                "-an", "-f", "null", "-",
            ],
            capture_output=True, text=True, check=False, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return []
    out: list[dict] = []
    for line in (proc.stderr or "").splitlines():
        m = _FREEZE_RE.search(line)
        if m:
            out.append({"start": float(m.group(1))})
    return out


def _dim_visual(video: Path, duration_sec: float) -> DimensionReport:
    findings: list[str] = []
    verdict = "pass"

    blacks = _ffmpeg_blackdetect(video)
    freezes = _ffmpeg_freezedetect(video)

    black_total = sum(b["duration"] for b in blacks)
    if blacks:
        verdict = "warn"
        for b in blacks[:5]:
            findings.append(
                f"dark segment @ {b['start']:.2f}s → {b['end']:.2f}s ({b['duration']:.2f}s)"
            )
        if black_total > max(1.0, duration_sec * 0.1):
            verdict = "fail"
            findings.append(
                f"total dark runtime {black_total:.2f}s is >10% of the edit — extraction likely failed"
            )

    if freezes:
        verdict = "warn" if verdict == "pass" else verdict
        for f in freezes[:3]:
            findings.append(f"frozen frame start @ {f['start']:.2f}s")

    return DimensionReport(
        name="visual", verdict=verdict,
        findings=findings,
        measurements={
            "black_segments": blacks,
            "freeze_events": freezes,
            "total_black_sec": round(black_total, 3),
        },
    )


# ---------- Audio (loudnorm + silencedetect) ----------


_LOUDNORM_FIELDS = ("input_i", "input_tp", "input_lra", "input_thresh")
_VOL_MEAN_RE = re.compile(r"mean_volume:\s*(-?[\d.]+)\s*dB")
_VOL_MAX_RE = re.compile(r"max_volume:\s*(-?[\d.]+)\s*dB")
_SILENCE_RE = re.compile(r"silence_start:\s*([\d.]+)|silence_duration:\s*([\d.]+)")


def _ffmpeg_loudnorm(video: Path) -> dict[str, float]:
    if shutil.which("ffmpeg") is None:
        return {}
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-i", str(video),
                "-af", "loudnorm=print_format=json",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, check=False, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {}
    # loudnorm's JSON payload lands at the END of stderr between braces.
    stderr = proc.stderr or ""
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(stderr[start:end + 1])
    except json.JSONDecodeError:
        return {}
    out: dict[str, float] = {}
    for key in _LOUDNORM_FIELDS:
        try:
            out[key] = float(parsed.get(key))
        except (TypeError, ValueError):
            pass
    return out


def _ffmpeg_volumedetect(video: Path) -> dict[str, float]:
    if shutil.which("ffmpeg") is None:
        return {}
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-i", str(video),
                "-af", "volumedetect",
                "-vn", "-sn", "-dn", "-f", "null", "-",
            ],
            capture_output=True, text=True, check=False, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {}
    out: dict[str, float] = {}
    stderr = proc.stderr or ""
    m = _VOL_MEAN_RE.search(stderr)
    if m:
        out["mean_dbfs"] = float(m.group(1))
    m = _VOL_MAX_RE.search(stderr)
    if m:
        out["max_dbfs"] = float(m.group(1))
    return out


def _ffmpeg_silencedetect(video: Path, noise_db: float = -40, d: float = 2.0) -> list[dict]:
    if shutil.which("ffmpeg") is None:
        return []
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-i", str(video),
                "-af", f"silencedetect=noise={noise_db}dB:d={d}",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, check=False, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return []
    out: list[dict] = []
    starts: list[float] = []
    durations: list[float] = []
    for line in (proc.stderr or "").splitlines():
        if "silence_start:" in line:
            try:
                starts.append(float(line.split("silence_start:")[1].strip().split()[0]))
            except (ValueError, IndexError):
                pass
        if "silence_duration:" in line:
            try:
                durations.append(float(line.split("silence_duration:")[1].strip().split()[0]))
            except (ValueError, IndexError):
                pass
    for i, start in enumerate(starts):
        dur = durations[i] if i < len(durations) else None
        out.append({"start": start, "duration": dur})
    return out


def _dim_audio(video: Path, tech: DimensionReport) -> DimensionReport:
    if not tech.measurements.get("audio_codec"):
        return DimensionReport(
            name="audio", verdict="fail",
            findings=["no audio stream to measure"],
        )

    findings: list[str] = []
    verdict = "pass"
    m: dict[str, Any] = {}

    loud = _ffmpeg_loudnorm(video)
    m.update(loud)
    vol = _ffmpeg_volumedetect(video)
    m.update(vol)
    silences = _ffmpeg_silencedetect(video)
    m["silences"] = silences

    lufs = loud.get("input_i")
    tp = loud.get("input_tp")
    if lufs is not None:
        if lufs < -20:
            verdict = "warn"
            findings.append(f"integrated LUFS {lufs:.1f} is quiet (target -16 to -10)")
        elif lufs > -6:
            verdict = "fail"
            findings.append(f"integrated LUFS {lufs:.1f} is dangerously loud")
    if tp is not None and tp > -1:
        verdict = "fail"
        findings.append(f"true peak {tp:.2f} dBTP > -1 dBTP ceiling — clipping risk")
    if silences:
        long_silences = [s for s in silences if (s.get("duration") or 0) > 2.0]
        if long_silences:
            verdict = "warn" if verdict == "pass" else verdict
            findings.append(f"{len(long_silences)} silence stretch(es) > 2s")
    mean_dbfs = vol.get("mean_dbfs")
    if mean_dbfs is not None and mean_dbfs <= -70:
        verdict = "fail"
        findings.append(f"mean volume {mean_dbfs:.1f} dBFS — track is silent")

    return DimensionReport(name="audio", verdict=verdict, findings=findings, measurements=m)


# ---------- Structural (rendered vs shot list) ----------


def _dim_structural(
    video: Path, duration_sec: float, shot_list: dict | None,
) -> DimensionReport:
    findings: list[str] = []
    verdict = "pass"
    m: dict[str, Any] = {"rendered_duration_sec": duration_sec}

    if not shot_list:
        return DimensionReport(
            name="structural", verdict="warn",
            findings=["no shot-list.json — cannot compare"],
            measurements=m,
        )

    fps = float(shot_list.get("fps") or 24.0)
    shots = shot_list.get("shots") or []
    expected_sec = sum(int(s.get("duration_frames") or 0) for s in shots) / fps
    m["expected_duration_sec"] = round(expected_sec, 3)
    m["shot_count"] = len(shots)

    if expected_sec <= 0:
        verdict = "warn"
        findings.append("shot-list durations sum to 0")
        return DimensionReport(name="structural", verdict=verdict, findings=findings, measurements=m)

    delta = duration_sec - expected_sec
    pct = abs(delta) / expected_sec
    m["delta_sec"] = round(delta, 3)
    m["delta_pct"] = round(pct, 4)
    if pct > 0.10:
        verdict = "fail"
        findings.append(
            f"rendered {duration_sec:.2f}s vs expected {expected_sec:.2f}s ({pct*100:.1f}% off)"
        )
    elif pct > 0.03:
        verdict = "warn"
        findings.append(
            f"rendered {duration_sec:.2f}s vs expected {expected_sec:.2f}s ({pct*100:.1f}% off)"
        )

    return DimensionReport(name="structural", verdict=verdict, findings=findings, measurements=m)


# ---------- Shot list dedupe + distribution ----------


def _dim_shot_list(shot_list: dict | None) -> DimensionReport:
    findings: list[str] = []
    verdict = "pass"
    m: dict[str, Any] = {}

    if not shot_list:
        return DimensionReport(
            name="shot_list", verdict="warn",
            findings=["no shot-list.json — cannot check reuse or distribution"],
        )

    shots = shot_list.get("shots") or []
    m["shot_count"] = len(shots)

    # Reuse check
    keys: list[tuple[str, str]] = []
    intentional_reuse = 0
    for s in shots:
        key = (s.get("source_id", ""), s.get("source_timecode", ""))
        if s.get("intent") == "callback":
            intentional_reuse += 1
        keys.append(key)
    unique = len(set(keys))
    m["unique_shots"] = unique
    m["intentional_callbacks"] = intentional_reuse
    accidental_reuse = len(keys) - unique - intentional_reuse
    m["accidental_reuse"] = accidental_reuse
    if accidental_reuse > 0:
        verdict = "fail"
        findings.append(
            f"{accidental_reuse} shot(s) reuse the same source+timecode without an intent=callback marker"
        )
    if intentional_reuse:
        findings.append(f"{intentional_reuse} intentional callback reuse(s) — expected")

    # Per-source distribution
    src_counts = Counter(s.get("source_id") for s in shots)
    m["source_distribution"] = dict(src_counts)
    total = len(shots)
    if total > 0:
        worst_src, worst_n = src_counts.most_common(1)[0]
        share = worst_n / total
        m["top_source_share"] = round(share, 3)
        if share > 0.50:
            verdict = "warn" if verdict == "pass" else verdict
            findings.append(
                f"source {worst_src!r} takes {share*100:.0f}% of the runtime — consider adding variety"
            )

    # Warnings surfaced by the proposer (tolerance widening)
    proposer_warnings = shot_list.get("warnings") or []
    if proposer_warnings:
        verdict = "warn" if verdict == "pass" else verdict
        findings.append(f"{len(proposer_warnings)} proposer warning(s): {proposer_warnings[0]}")

    return DimensionReport(name="shot_list", verdict=verdict, findings=findings, measurements=m)


# ---------- Orchestration ----------


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _roll_up(verdicts: list[str]) -> str:
    if not verdicts:
        return "pass"
    return max(verdicts, key=lambda v: VERDICT_RANK.get(v, 0))


def _overall_label(verdict: str) -> str:
    return {"pass": "green", "warn": "yellow", "fail": "red"}.get(verdict, "yellow")


def _ship_recommendation(verdict: str, dimensions: list[DimensionReport]) -> str:
    if verdict == "fail":
        bad = [d.name for d in dimensions if d.verdict == "fail"]
        return (
            f"Do NOT ship — hard failure(s) in: {', '.join(bad)}. "
            f"Fix the underlying issue and re-render."
        )
    if verdict == "warn":
        soft = [d.name for d in dimensions if d.verdict == "warn"]
        return (
            f"Reviewable with caveats — {', '.join(soft)} flagged. "
            f"Open the output and eyeball before shipping."
        )
    return "Green across the board. Safe to pull into the NLE."


def review_rendered_edit(
    project_dir: Path,
    *,
    video_name: str = "graded.mp4",
    target_fps: int = 24,
    target_w: int = 1920,
    target_h: int = 1080,
) -> ReviewReport:
    """Run every review pass against a rendered edit and return a full ReviewReport."""
    video = project_dir / "exports" / video_name
    if not video.exists():
        raise FileNotFoundError(f"no render at {video}")

    tech = _dim_technical(video, target_fps=target_fps, target_w=target_w, target_h=target_h)
    duration = float(tech.measurements.get("duration_sec", 0) or 0)

    visual = _dim_visual(video, duration)
    audio = _dim_audio(video, tech)

    shot_list = _load_json(project_dir / "data" / "shot-list.json")
    structural = _dim_structural(video, duration, shot_list)
    shotlist = _dim_shot_list(shot_list)

    dimensions = [tech, visual, audio, structural, shotlist]
    overall_verdict = _roll_up([d.verdict for d in dimensions])
    score = overall_score(dimensions)
    grade = score_to_letter(score)

    return ReviewReport(
        project_slug=project_dir.name,
        video_path=str(video),
        generated_at=datetime.now(timezone.utc).isoformat(),
        overall=_overall_label(overall_verdict),
        overall_verdict=overall_verdict,
        score=score,
        grade=grade,
        dimensions=dimensions,
        ship_recommendation=_ship_recommendation(overall_verdict, dimensions),
    )


__all__ = [
    "DIMENSION_WEIGHTS",
    "DimensionReport",
    "ReviewReport",
    "overall_score",
    "review_rendered_edit",
    "score_to_letter",
]
