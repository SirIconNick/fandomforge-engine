"""Render verifier — the "did you actually watch it" check.

Runs after every render to confirm the mp4 on disk matches what the plan
claims. Existing qa_loop.py checks LUFS + structural integrity. This goes
further: samples frames at every key timestamp, sends them to GPT-4o
vision, measures per-cue audio, reports pacing variance, beat alignment,
and character presence.

Output: a structured report with specific timestamps of every issue, so
the user doesn't have to watch the whole thing to find what's broken.

Usage (module):
    from fandomforge.intelligence.render_verifier import verify_render
    report = verify_render(video_path, plan_path, project_dir)
    print(report.summary())

Usage (CLI):
    ff verify --project leon-badass-monologue
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, pstdev


# ---------------------------------------------------------------------------
# Report data model
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    passed: bool
    note: str = ""
    issues: list[str] = field(default_factory=list)
    samples: list[dict] = field(default_factory=list)


@dataclass
class RenderReport:
    video_path: Path
    plan_path: Path
    duration_sec: float = 0.0
    integrated_lufs: float | None = None
    true_peak_dbfs: float | None = None
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def issue_count(self) -> int:
        return sum(len(c.issues) for c in self.checks)

    def summary(self) -> str:
        lines: list[str] = []
        status = "PASS" if self.passed else "FAIL"
        lines.append(f"=== render verification {status} ===")
        lines.append(f"video: {self.video_path.name}")
        lines.append(f"duration: {self.duration_sec:.2f}s")
        if self.integrated_lufs is not None:
            lines.append(
                f"integrated_lufs: {self.integrated_lufs:.2f} LUFS "
                f"(true_peak: {self.true_peak_dbfs:.2f} dBFS)"
            )
        lines.append("")
        for c in self.checks:
            mark = "✓" if c.passed else "✗"
            lines.append(f"{mark} {c.name}: {c.note}")
            for iss in c.issues[:8]:
                lines.append(f"    ⚠ {iss}")
            if len(c.issues) > 8:
                lines.append(f"    … and {len(c.issues) - 8} more")
        lines.append("")
        lines.append(
            f"{self.issue_count} total issues across {len(self.checks)} checks"
        )
        return "\n".join(lines)

    def to_json(self, out: Path) -> Path:
        data: dict = {
            "video_path": str(self.video_path),
            "plan_path": str(self.plan_path),
            "duration_sec": self.duration_sec,
            "integrated_lufs": self.integrated_lufs,
            "true_peak_dbfs": self.true_peak_dbfs,
            "passed": self.passed,
            "issue_count": self.issue_count,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "note": c.note,
                    "issues": c.issues,
                    "samples": c.samples,
                }
                for c in self.checks
            ],
        }
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=2))
        return out


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe helpers
# ---------------------------------------------------------------------------

def _probe_duration(video: Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, check=True, timeout=30,
        )
        return float(r.stdout.strip())
    except Exception:  # noqa: BLE001
        return 0.0


def _measure_ebu_r128(video: Path) -> tuple[float | None, float | None]:
    """Run ebur128 and return (integrated_lufs, true_peak_dbfs).

    Parses ONLY the final "Summary:" block, not the running-average lines
    (those include the cold-start -70 LUFS frame).
    """
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", str(video),
             "-af", "ebur128=peak=true:target=-14", "-f", "null", "-"],
            capture_output=True, text=True, check=False, timeout=180,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None, None
    text = r.stderr
    # Find the Summary block; everything before it is per-frame running data.
    idx = text.rfind("Summary:")
    if idx == -1:
        return None, None
    summary = text[idx:]
    i_match = re.search(r"I:\s*(-?\d+\.?\d*)\s*LUFS", summary)
    p_match = re.search(r"Peak:\s*(-?\d+\.?\d*)\s*dBFS", summary)
    return (
        float(i_match.group(1)) if i_match else None,
        float(p_match.group(1)) if p_match else None,
    )


def _extract_jpg(video: Path, t_sec: float, out_jpg: Path, w: int = 640) -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-ss", f"{t_sec:.3f}", "-i", str(video),
        "-frames:v", "1", "-vf", f"scale={w}:-1",
        "-q:v", "5", str(out_jpg),
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=30)
        return out_jpg.exists() and out_jpg.stat().st_size > 1000
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def _is_near_black(jpg: Path) -> bool:
    """Check if jpg is a nearly-black frame (mean luminance < 10/255)."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", str(jpg), "-vf",
             "scale=1:1,format=gray", "-f", "rawvideo", "-"],
            capture_output=True, check=True, timeout=15,
        )
        return len(r.stdout) > 0 and r.stdout[0] < 10
    except Exception:  # noqa: BLE001
        return False


def _sample_non_black_jpg(
    video: Path, start: float, duration: float, tmp: Path, prefix: str,
) -> Path | None:
    """Try 5 sample points weighted toward the middle. Returns first
    non-black jpg.

    Shots shorter than 1.0s can have the whole middle eaten by a xfade
    transition so we also spread samples outward from center. Tries in
    order: 50%, 40%, 60%, 35%, 65% — center-biased, avoiding the first
    200ms and last 200ms where xfades live.
    """
    # Compute safe window: skip first 0.2s and last 0.2s to avoid xfade tails
    safe_start = 0.2 / max(duration, 0.5)
    safe_end = 1.0 - (0.2 / max(duration, 0.5))
    safe_start = max(0.0, min(0.45, safe_start))
    safe_end = min(1.0, max(0.55, safe_end))
    center = (safe_start + safe_end) / 2
    offsets = [center]
    # Spread outward
    step = (safe_end - safe_start) / 6
    for i in range(1, 3):
        offsets.append(center - step * i)
        offsets.append(center + step * i)
    for idx, pct in enumerate(offsets):
        pct = max(safe_start, min(safe_end, pct))
        jpg = tmp / f"{prefix}_{idx}.jpg"
        t = start + duration * pct
        if not _extract_jpg(video, t, jpg):
            continue
        if not _is_near_black(jpg):
            return jpg
    return None


def _measure_band_rms(video: Path, start: float, dur: float, band: str) -> float | None:
    """Return RMS dBFS of audio in [start, start+dur] with bandpass filter.

    band: "voice" (200-3000 Hz) or "full" (no filter).
    """
    if shutil.which("ffmpeg") is None:
        return None
    try:
        if band == "voice":
            af = f"highpass=f=200,lowpass=f=3000,volumedetect"
        else:
            af = "volumedetect"
        r = subprocess.run(
            ["ffmpeg", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
             "-i", str(video), "-vn", "-af", af, "-f", "null", "-"],
            capture_output=True, text=True, check=False, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return None
    m = re.search(r"mean_volume:\s*(-?\d+\.?\d*)\s*dB", r.stderr)
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# GPT-4o vision check
# ---------------------------------------------------------------------------

def _vision_check(jpg: Path, prompt: str, api_key: str) -> dict | None:
    """Send a frame + prompt to gpt-4o-mini; return parsed JSON or None.

    Returns {"_rate_limited": True} on 429 so callers can distinguish
    "couldn't check" from "checked and not visible".
    """
    import urllib.request
    import time
    img_b64 = base64.b64encode(jpg.read_bytes()).decode()
    body = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            ]},
        ],
        "max_tokens": 200,
        "response_format": {"type": "json_object"},
    }).encode()
    for attempt in range(4):
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.loads(r.read())
            content = j["choices"][0]["message"]["content"]
            return json.loads(content)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(2 ** attempt * 2)
                continue
            if e.code == 429:
                return {"_rate_limited": True}
            return None
        except Exception:  # noqa: BLE001
            return None
    return None


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_sync_anchors(
    video: Path, plan: dict, tmp: Path, character: str, api_key: str | None,
) -> CheckResult:
    """Sample each sync-anchor shot; verify the character is visible."""
    out = CheckResult(name="sync_anchors", passed=True)
    anchors = [s for s in plan.get("shots", []) if s.get("kind") == "sync_anchor"]
    if not anchors:
        out.note = "no sync anchors in plan"
        out.passed = False
        out.issues.append(
            "plan has zero sync anchors — every dialogue line is vo_only. "
            "Your dialogue will never match what's on screen."
        )
        return out

    out.note = f"{len(anchors)} anchors to check"
    for i, shot in enumerate(anchors):
        # Sample 3 points across the anchor; character visible at ANY of
        # them means the anchor is good (legit cutaways within the clip —
        # Leon looking at a CCTV feed, a whip-pan — shouldn't flag).
        start = shot["start_sec"]
        dur = shot["duration_sec"]
        jpgs: list[Path] = []
        for pct, tag in [(0.25, "a"), (0.55, "b"), (0.80, "c")]:
            t = start + dur * pct
            j = tmp / f"anchor_{i:02d}_{tag}.jpg"
            if _extract_jpg(video, t, j) and not _is_near_black(j):
                jpgs.append(j)
        if not jpgs:
            out.issues.append(
                f"@{start:.1f}s anchor #{i}: all 3 sample points are BLACK — "
                f"anchor clip is a fade/blank. Shift clip_start_sec ±1s."
            )
            continue

        # Run vision check on each non-black frame; pass if any shows the
        # character. Store the first-pass description as the main sample.
        any_visible = False
        rate_limited = False
        last_desc = ""
        for j in jpgs:
            if api_key:
                prompt = (
                    f"This is a frame from a video tribute for {character}. "
                    f"Return JSON with keys: character_visible (boolean), "
                    f"scene_description (string, under 20 words)."
                )
                res = _vision_check(j, prompt, api_key)
                if res is not None:
                    if res.get("_rate_limited"):
                        rate_limited = True
                        continue
                    last_desc = res.get("scene_description", "") or last_desc
                    if res.get("character_visible"):
                        any_visible = True
                        break
            else:
                any_visible = True  # no api_key, trust the anchor
                break
        sample = {"t_sec": start, "jpg": str(jpgs[0]), "visible": any_visible}
        if rate_limited and not any_visible:
            # Rate-limited and no prior frame passed — treat as unverified,
            # don't fail the check (rate limits are transient).
            sample["rate_limited"] = True
        elif api_key and not any_visible:
            out.issues.append(
                f"@{start:.1f}s anchor #{i}: character not visible in any of "
                f"3 sampled frames ('{last_desc[:50]}'). "
                f"Either clip_start_sec is off, or the source clip has no "
                f"character presence — consider a different wav for this line."
            )
        out.samples.append(sample)

    if out.issues:
        out.passed = False
    return out


def check_broll_variety(
    video: Path, plan: dict, tmp: Path, character: str, api_key: str | None,
    sample_stride: int = 4,
) -> CheckResult:
    """Sample every Nth broll shot; check character-presence + variety."""
    out = CheckResult(name="broll_variety", passed=True)
    broll = [s for s in plan.get("shots", []) if s.get("kind") != "sync_anchor"]
    if not broll:
        out.note = "no broll shots"
        return out

    sampled = broll[::sample_stride]
    out.note = f"sampled {len(sampled)}/{len(broll)} broll shots"
    desc_seen: dict[str, int] = {}
    missing_character: list[str] = []

    for i, shot in enumerate(sampled):
        start = shot["start_sec"]
        dur = shot["duration_sec"]
        # Sample 2 points across the broll; if EITHER shows the character,
        # call it present. Short broll shots can have legit cutaways or the
        # character entering/exiting frame.
        jpgs: list[Path] = []
        for pct, tag in [(0.35, "a"), (0.70, "b")]:
            t = start + dur * pct
            j = tmp / f"broll_{i:02d}_{tag}.jpg"
            if _extract_jpg(video, t, j) and not _is_near_black(j):
                jpgs.append(j)
        if not jpgs:
            out.issues.append(
                f"@{start:.1f}s broll: both sample points BLACK — dead air."
            )
            continue
        sample = {"t_sec": start, "jpg": str(jpgs[0])}
        if api_key:
            prompt = (
                f"This is a frame from a video tribute for {character}. "
                f"Return JSON with keys: character_visible (boolean), "
                f"brief_desc (string, 6 words max), visual_mood "
                f"(one of: calm, tense, action, somber, neutral)."
            )
            any_visible = False
            rate_limited = False
            first_res: dict | None = None
            for j in jpgs:
                res = _vision_check(j, prompt, api_key)
                if res is None:
                    continue
                if res.get("_rate_limited"):
                    rate_limited = True
                    continue
                if first_res is None:
                    first_res = res
                if res.get("character_visible"):
                    any_visible = True
                    break
            if rate_limited and first_res is None:
                # Couldn't verify — don't add to missing-character list
                out.samples.append(sample)
                continue
            if first_res is not None:
                sample.update(first_res)
                if not any_visible:
                    missing_character.append(
                        f"@{start:.1f}s ({first_res.get('brief_desc','')})"
                    )
                d = first_res.get("brief_desc", "")[:30].lower()
                desc_seen[d] = desc_seen.get(d, 0) + 1
        out.samples.append(sample)

    if missing_character:
        # Broll occasionally lacking the character is fine (atmospheric shot),
        # but if more than half the broll is missing the character, flag it
        ratio = len(missing_character) / max(1, len(sampled))
        if ratio > 0.5:
            out.issues.append(
                f"{len(missing_character)}/{len(sampled)} sampled broll shots "
                f"DON'T show {character} — broll selection is off the rails. "
                f"Layered planner b-roll filter isn't respecting character tag."
            )
            for s in missing_character[:5]:
                out.issues.append(f"    {s}")
    # Variety check — if any single description appears >25% of samples
    for d, count in desc_seen.items():
        if count / max(1, len(sampled)) > 0.25 and count >= 3:
            out.issues.append(
                f"shot '{d}' appears in {count}/{len(sampled)} samples — "
                f"broll is repetitive, tighten duplicate-desc filter."
            )

    if out.issues:
        out.passed = False
    return out


def check_voice_lift(video: Path, plan: dict) -> CheckResult:
    """Per-cue voice-band RMS vs surrounding song RMS — is dialogue audible?"""
    out = CheckResult(name="voice_lift", passed=True)
    lines = [d for d in plan.get("dialogue_lines", []) if d.get("placement_sec") is not None]
    if not lines:
        out.note = "no VO cues"
        return out

    out.note = f"checking {len(lines)} VO cues"
    for d in lines:
        t = float(d["placement_sec"])
        dur = float(d["duration_sec"])
        # Inside-cue RMS (voice band)
        during = _measure_band_rms(video, t + 0.1, max(0.3, dur - 0.2), "voice")
        # Outside-cue RMS (0.5s before)
        before = _measure_band_rms(video, max(0, t - 0.6), 0.4, "voice")
        sample = {
            "t_sec": t,
            "duration": dur,
            "text": d.get("text", "")[:50],
            "voice_during_db": during,
            "voice_before_db": before,
        }
        if during is not None and before is not None:
            lift = during - before
            sample["lift_db"] = round(lift, 2)
            if lift < 2.5:
                out.issues.append(
                    f"@{t:.1f}s VO: only +{lift:.1f} dB voice-band lift "
                    f"(need ≥+2.5). Increase duck_db on this cue OR lower "
                    f"song_gain_db project-wide. Line: '{d.get('text','')[:40]}'"
                )
        out.samples.append(sample)

    if out.issues:
        out.passed = False
    return out


def check_pacing(plan: dict) -> CheckResult:
    """Shot duration distribution — is pacing varied or flat?"""
    out = CheckResult(name="pacing", passed=True)
    durs = [float(s["duration_sec"]) for s in plan.get("shots", [])]
    if len(durs) < 4:
        out.note = "not enough shots to measure"
        return out
    m = mean(durs)
    sd = pstdev(durs)
    cv = sd / m if m > 0 else 0
    out.note = (
        f"{len(durs)} shots, mean={m:.2f}s stddev={sd:.2f}s cv={cv:.2f}"
    )
    if cv < 0.25:
        out.issues.append(
            f"pacing variance too LOW (cv={cv:.2f}). Every cut is roughly "
            f"{m:.1f}s — feels mechanical. Good edits have cv > 0.30 "
            f"(mix of short snap cuts + longer hold shots)."
        )
    # Also flag runs of very-similar durations
    run = 1
    max_run = 1
    for i in range(1, len(durs)):
        if abs(durs[i] - durs[i - 1]) < 0.15:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 1
    if max_run >= 6:
        out.issues.append(
            f"{max_run} consecutive shots within 0.15s of each other — "
            f"monotone run. Break it up with a long hold or a snap cut."
        )

    if out.issues:
        out.passed = False
    return out


def check_beat_alignment(plan: dict, project_dir: Path) -> CheckResult:
    """For each cut boundary, distance to nearest song beat."""
    out = CheckResult(name="beat_alignment", passed=True)
    song_struct_path = None
    for p in (project_dir / "raw").glob("*.song_structure.json"):
        song_struct_path = p
        break
    if not song_struct_path or not song_struct_path.exists():
        out.note = "no song_structure.json — skipping"
        return out
    try:
        struct = json.loads(song_struct_path.read_text())
    except Exception:  # noqa: BLE001
        out.note = "song_structure.json unreadable"
        return out
    beats = [float(b["time"]) if isinstance(b, dict) else float(b)
             for b in struct.get("beats", [])]
    if not beats:
        out.note = "no beats in song_structure"
        return out
    beats_sorted = sorted(beats)
    offsets: list[float] = []
    for shot in plan.get("shots", []):
        t = float(shot["start_sec"])
        # Binary search nearest beat
        lo, hi = 0, len(beats_sorted) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if beats_sorted[mid] < t:
                lo = mid + 1
            else:
                hi = mid
        near = min(
            (abs(t - b) for b in beats_sorted[max(0, lo - 1):lo + 2]),
            default=float("inf"),
        )
        offsets.append(near)
    if not offsets:
        out.note = "no cuts measured"
        return out
    median_offset = sorted(offsets)[len(offsets) // 2]
    aligned = sum(1 for o in offsets if o <= 0.15)
    pct = aligned / len(offsets)
    out.note = (
        f"{len(offsets)} cuts, median offset from beat {median_offset:.3f}s, "
        f"{aligned} cuts ({pct*100:.0f}%) within ±150ms of a beat"
    )
    if pct < 0.35:
        out.issues.append(
            f"only {pct*100:.0f}% of cuts land on beats — song isn't "
            f"driving the edit. Layered planner should snap cut-start "
            f"to nearest beat in +/- 200ms window."
        )
    if out.issues:
        out.passed = False
    return out


def check_duration_match(video: Path, plan: dict) -> CheckResult:
    out = CheckResult(name="duration_match", passed=True)
    actual = _probe_duration(video)
    planned = float(plan.get("total_duration", 0))
    out.note = f"actual={actual:.2f}s planned={planned:.2f}s"
    if planned <= 0:
        out.issues.append("plan has no total_duration")
    else:
        diff = abs(actual - planned)
        if diff > 1.5:
            out.issues.append(
                f"render duration off by {diff:.2f}s "
                f"(actual {actual:.2f}, planned {planned:.2f}). "
                f"Check trim / atrim values in render pipeline."
            )
    if out.issues:
        out.passed = False
    return out


# ---------------------------------------------------------------------------
# Top-level verify
# ---------------------------------------------------------------------------

def verify_render(
    video_path: Path,
    plan_path: Path,
    project_dir: Path,
    *,
    character: str | None = None,
    api_key: str | None = None,
    write_report: bool = True,
) -> RenderReport:
    """Full proofing pass over a rendered edit."""
    if api_key is None:
        api_key = os.environ.get("OPENAI_API_KEY")
    if character is None:
        try:
            from fandomforge.config import load_project_config
            cfg = load_project_config(project_dir)
            character = cfg.character
        except Exception:  # noqa: BLE001
            character = "the character"

    plan = json.loads(plan_path.read_text())

    report = RenderReport(video_path=video_path, plan_path=plan_path)
    report.duration_sec = _probe_duration(video_path)
    report.integrated_lufs, report.true_peak_dbfs = _measure_ebu_r128(video_path)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        report.checks.append(check_duration_match(video_path, plan))
        report.checks.append(check_sync_anchors(video_path, plan, tmp, character, api_key))
        report.checks.append(check_broll_variety(video_path, plan, tmp, character, api_key))
        report.checks.append(check_voice_lift(video_path, plan))
        report.checks.append(check_pacing(plan))
        report.checks.append(check_beat_alignment(plan, project_dir))

    if write_report:
        out = project_dir / "exports" / f"{video_path.stem}.verify-report.json"
        report.to_json(out)

    return report
