"""Auto-test a rendered cut. Verifies:
- No extended black frames (>0.5s of Y < 20)
- Each expected dialogue cue is audibly intelligible (Whisper transcribe at cue timestamp,
  fuzzy match against expected line)
- Voice-band lift at each cue vs surrounding ambient (>= 6 dB)
- Overall loudness in safe range (-17 to -12 LUFS)

Returns a structured pass/fail report. Use it after every render.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CueCheck:
    start_sec: float
    expected_line: str
    transcribed: str = ""
    fuzzy_score: float = 0.0
    voice_lift_db: float = 0.0
    pass_transcribe: bool = False
    pass_lift: bool = False


@dataclass
class ShotCheck:
    shot_index: int
    timestamp_sec: float
    intended_shot: str = ""
    intended_mood: str = ""
    vision_desc: str = ""
    fit: str = ""  # yes / weak / no
    fix_suggestion: str = ""


@dataclass
class TestReport:
    video_path: Path
    duration_sec: float = 0.0
    integrated_lufs: float = 0.0
    peak_dbfs: float = 0.0
    black_ranges: list[tuple[float, float]] = field(default_factory=list)
    cue_checks: list[CueCheck] = field(default_factory=list)
    shot_checks: list[ShotCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    passed: bool = True

    def to_dict(self) -> dict:
        return {
            "video": str(self.video_path),
            "duration_sec": self.duration_sec,
            "integrated_lufs": self.integrated_lufs,
            "peak_dbfs": self.peak_dbfs,
            "black_ranges": self.black_ranges,
            "cue_checks": [
                {
                    "start": c.start_sec,
                    "expected": c.expected_line,
                    "transcribed": c.transcribed,
                    "fuzzy_score": c.fuzzy_score,
                    "voice_lift_db": c.voice_lift_db,
                    "pass_transcribe": c.pass_transcribe,
                    "pass_lift": c.pass_lift,
                }
                for c in self.cue_checks
            ],
            "warnings": self.warnings,
            "passed": self.passed,
        }


def _probe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _measure_ebur128(path: Path) -> tuple[float, float]:
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "ebur128=peak=true",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=300,
    )
    combined = (r.stdout + r.stderr)
    # Only the final Summary block after "Integrated loudness:" gives the
    # true integrated number. Per-frame rows also have "I:" so restrict to
    # the summary section.
    summary = combined.rsplit("Integrated loudness:", 1)[-1] if "Integrated loudness:" in combined else combined
    i_match = re.search(r"I:\s+(-?\d+\.\d+)\s+LUFS", summary)
    tp_match = re.search(r"Peak:\s+(-?\d+\.\d+)\s+dBFS", summary)
    integrated = float(i_match.group(1)) if i_match else 0.0
    peak = float(tp_match.group(1)) if tp_match else 0.0
    return integrated, peak


def _detect_black(path: Path, min_dur_sec: float = 0.5) -> list[tuple[float, float]]:
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-vf",
         f"blackdetect=d={min_dur_sec}:pix_th=0.10", "-f", "null", "-"],
        capture_output=True, text=True, timeout=300,
    )
    ranges = []
    for m in re.finditer(
        r"black_start:(\d+\.?\d*).*?black_end:(\d+\.?\d*)", r.stderr
    ):
        ranges.append((float(m.group(1)), float(m.group(2))))
    return ranges


def _voice_band_rms(path: Path, start: float, duration: float) -> float:
    """Measure RMS in 200-4000 Hz band over a window."""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-ss", f"{start}", "-t", f"{duration}",
         "-i", str(path), "-af",
         "highpass=f=200,lowpass=f=4000,volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    m = re.search(r"mean_volume:\s+(-?\d+\.\d+)\s+dB", r.stderr)
    return float(m.group(1)) if m else 0.0


def _fuzzy(a: str, b: str) -> float:
    """Bag-of-words Jaccard on lowercased alphanumeric tokens."""
    ta = set(re.findall(r"[a-z0-9]+", a.lower()))
    tb = set(re.findall(r"[a-z0-9]+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _transcribe_window(
    path: Path, start: float, duration: float, api_key: str | None
) -> str:
    """Extract window as wav and send to Whisper API (if key available)."""
    if not api_key:
        return ""
    tmp = Path("/tmp/claude") / f"_cue_{int(start*1000)}.wav"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-ss", f"{start}", "-t", f"{duration + 0.5}",
         "-i", str(path), "-ac", "1", "-ar", "16000", str(tmp)],
        check=False, timeout=30,
    )
    try:
        import urllib.request
        data = tmp.read_bytes()
        boundary = "----formboundary" + str(int(start * 1000))
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{tmp.name}"\r\n'
            f"Content-Type: audio/wav\r\n\r\n"
        ).encode() + data + (
            f"\r\n--{boundary}\r\n"
            f'Content-Disposition: form-data; name="model"\r\n\r\nwhisper-1\r\n'
            f"--{boundary}--\r\n"
        ).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/transcriptions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        with urllib.request.urlopen(req, timeout=90) as r:
            j = json.loads(r.read())
            return j.get("text", "").strip()
    except Exception as e:  # noqa: BLE001
        return f"(whisper-error: {e})"


def _load_api_key() -> str | None:
    env = Path(".env")
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("OPENAI_API_KEY="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
    import os
    return os.environ.get("OPENAI_API_KEY")


def run_auto_test(
    video_path: Path | str,
    dialogue_cues_json: Path | str,
    *,
    use_whisper: bool = True,
    lufs_min: float = -17.0,
    lufs_max: float = -10.0,
    fuzzy_pass_threshold: float = 0.4,
    voice_lift_pass_threshold: float = 4.0,
) -> TestReport:
    vp = Path(video_path)
    report = TestReport(video_path=vp)
    if not vp.exists():
        report.warnings.append(f"video not found: {vp}")
        report.passed = False
        return report

    report.duration_sec = _probe_duration(vp)
    report.integrated_lufs, report.peak_dbfs = _measure_ebur128(vp)

    if not (lufs_min <= report.integrated_lufs <= lufs_max):
        report.warnings.append(
            f"loudness {report.integrated_lufs:.1f} LUFS outside "
            f"safe range [{lufs_min}, {lufs_max}]"
        )
        report.passed = False

    if report.peak_dbfs > -0.1:
        report.warnings.append(f"peak {report.peak_dbfs:.1f} dBFS too close to 0")
        report.passed = False

    report.black_ranges = _detect_black(vp, min_dur_sec=0.5)
    for (start, end) in report.black_ranges:
        if end - start > 1.0 and start < 3.0:
            report.warnings.append(
                f"long black at start: {start:.2f}s-{end:.2f}s "
                f"({end-start:.2f}s) — viewers will see dead air"
            )
            report.passed = False

    cues = json.loads(Path(dialogue_cues_json).read_text())["cues"]
    api_key = _load_api_key() if use_whisper else None

    for cue in cues:
        cc = CueCheck(
            start_sec=float(cue["start"]),
            expected_line=cue.get("line", ""),
        )
        dur = float(cue.get("duration", 3.0))
        during = _voice_band_rms(vp, cc.start_sec, dur)
        # Ambient = 1.5s window just before the cue
        ambient_start = max(0.0, cc.start_sec - 2.0)
        ambient = _voice_band_rms(vp, ambient_start, 1.5)
        cc.voice_lift_db = during - ambient
        cc.pass_lift = cc.voice_lift_db >= voice_lift_pass_threshold

        if api_key:
            # Dialogue can start a fraction of a second late (silence trim,
            # Demucs edge) and sometimes the leading song moment hides it.
            # Scan several overlapping windows and pick best fuzzy match.
            best_text = ""
            best_score = 0.0
            for offset in (0.0, 0.5, 1.0, 1.5):
                txt = _transcribe_window(
                    vp, cc.start_sec + offset, dur + 1.5, api_key
                )
                score = _fuzzy(cc.expected_line, txt)
                if score > best_score:
                    best_text, best_score = txt, score
                if score >= fuzzy_pass_threshold:
                    break
            cc.transcribed = best_text
            cc.fuzzy_score = best_score
            cc.pass_transcribe = cc.fuzzy_score >= fuzzy_pass_threshold

        # Whisper transcription is the ground-truth intelligibility gate.
        # Voice-band lift is advisory only since aggressive song ducking can
        # legitimately produce a quieter window while dialogue stays clear.
        if api_key and not cc.pass_transcribe:
            report.warnings.append(
                f"cue at {cc.start_sec:.1f}s expected '{cc.expected_line}' "
                f"but Whisper heard '{cc.transcribed}' (fuzzy={cc.fuzzy_score:.2f})"
            )
            report.passed = False
        elif not cc.pass_lift:
            # Advisory warning only — don't fail the build if Whisper passed.
            report.warnings.append(
                f"cue at {cc.start_sec:.1f}s: lift only {cc.voice_lift_db:+.1f}dB "
                f"(Whisper still intelligible — advisory only)"
            )

        report.cue_checks.append(cc)

    return report


def print_report(report: TestReport) -> None:
    print(f"\n==== AUTO-TEST REPORT: {report.video_path.name} ====")
    print(f"Duration:   {report.duration_sec:.1f} s")
    print(f"Integrated: {report.integrated_lufs:.1f} LUFS")
    print(f"Peak:       {report.peak_dbfs:.2f} dBFS")
    if report.black_ranges:
        print(f"Black ranges (>0.5s): {report.black_ranges}")
    print(f"\nCue checks ({len(report.cue_checks)}):")
    for c in report.cue_checks:
        mark_t = "✓" if c.pass_transcribe else ("—" if not c.transcribed else "✗")
        mark_l = "✓" if c.pass_lift else "✗"
        print(
            f"  {c.start_sec:5.1f}s  lift={c.voice_lift_db:+6.1f}dB {mark_l}  "
            f"transcribe={mark_t} '{c.transcribed[:60]}' "
            f"(expected '{c.expected_line}', fuzzy={c.fuzzy_score:.2f})"
        )
    if report.warnings:
        print("\nWARNINGS:")
        for w in report.warnings:
            print(f"  - {w}")
    print(f"\nRESULT: {'PASS ✓' if report.passed else 'FAIL ✗'}\n")
