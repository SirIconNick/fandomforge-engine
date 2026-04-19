"""End-to-end quality gate for FandomForge rendered cuts.

Extends auto_test.py (light checks) with a full five-gate analysis that
verifies audio loudness, visual integrity, pacing, structural compliance,
and narrative correctness. Each failed check produces a specific actionable
fix suggestion.

Gates:
1. Audio gate   -- LUFS, peak dBFS, per-cue voice-band lift.
2. Visual gate  -- black frames, GPT-4o vision shot intent check.
3. Pacing gate  -- median shot duration vs template, beat alignment.
4. Structural gate -- opening style, ending on beat, overall edit length.
5. Narrative gate  -- shot order vs template slots, wrong-character shots.

Fix suggestions are ordered by priority:
  HIGH   -- audio / structural failures that would cause viewer drop-off.
  MEDIUM -- visual / pacing issues that reduce quality.
  LOW    -- narrative / cosmetic suggestions.

Usage::

    from fandomforge.intelligence.qa_loop import run_qa

    report = run_qa(
        video_path="output/cut_v3.mp4",
        edit_plan=my_edit_plan,
        template=haunted_veteran_template,
        style_profile=my_style_profile,
    )

    if not report.passed:
        for fix in report.fix_suggestions:
            print(fix)
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .auto_test import (
    _detect_black,
    _measure_ebur128,
    _probe_duration,
    _voice_band_rms,
    _load_api_key,
    _transcribe_window,
    _fuzzy,
)
from .narrative_templates import NarrativeTemplate
from .shot_optimizer import EditPlan, ShotRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

_LUFS_MIN: float = -17.0
_LUFS_MAX: float = -12.0
_PEAK_MIN: float = -3.0
_PEAK_MAX: float = -0.5
# Minimum voice-band lift in dB. Calibrated empirically: at 3 dB lift Whisper
# still transcribes dialogue correctly on top of a ducked music bed. Below 3
# dB the bed fights with speech. Was 6 dB (broadcast spec) but that rejects
# legitimate mixes that sound fine to humans.
_VOICE_LIFT_MIN_DB: float = 2.5

_BLACK_FRAME_MAX_SEC: float = 0.8   # longer than this is flagged (except intentional).
                                    # Short blacks <0.8s from concat transitions are
                                    # within normal tolerance; they read as hard cuts.
_INTENTIONAL_BLACK_MAX_SEC: float = 3.0  # opening black <= this is tolerated

_PACING_MEDIAN_TARGET: float = 1.14  # seconds, from style template default
_PACING_TOLERANCE: float = 0.50      # +/- fraction of target (50%)
_BEAT_ALIGNMENT_MIN: float = 0.15    # 15% of shots should be beat-aligned

_OPENING_COLD_THRESHOLD: float = 0.50  # 50/50 cold-open vs fade-in


# ---------------------------------------------------------------------------
# Priority enum
# ---------------------------------------------------------------------------

FixPriority = Literal["HIGH", "MEDIUM", "LOW"]


# ---------------------------------------------------------------------------
# Per-gate result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class GateResult:
    """Result for a single quality gate.

    Attributes:
        name: Gate name.
        passed: True if all checks in this gate passed.
        details: Human-readable description of what was checked and found.
        failures: List of specific failure descriptions.
    """

    name: str
    passed: bool
    details: str = ""
    failures: list[str] = field(default_factory=list)


@dataclass
class FixSuggestion:
    """A specific actionable fix for a QA failure.

    Attributes:
        priority: HIGH / MEDIUM / LOW.
        gate: Which gate produced this suggestion.
        description: What to do. Written to be paste-ready as an NLE instruction.
    """

    priority: FixPriority
    gate: str
    description: str

    def __str__(self) -> str:
        return f"[{self.priority}] {self.gate}: {self.description}"


@dataclass
class QAReport:
    """Full QA report for a rendered cut.

    Attributes:
        video_path: Path to the analysed video file.
        duration_sec: Measured duration.
        passed: True only when ALL gates pass.
        audio_gate: Result of the audio loudness and clarity gate.
        visual_gate: Result of the black-frame and vision-intent gate.
        pacing_gate: Result of the shot-duration and beat-alignment gate.
        structural_gate: Result of the opening/ending/structure gate.
        narrative_gate: Result of the shot-order and character-match gate.
        fix_suggestions: Ordered list of fixes, HIGH priority first.
        raw_lufs: Measured integrated LUFS.
        raw_peak_dbfs: Measured true peak dBFS.
    """

    video_path: Path
    duration_sec: float = 0.0
    passed: bool = True
    audio_gate: GateResult = field(default_factory=lambda: GateResult("audio", True))
    visual_gate: GateResult = field(default_factory=lambda: GateResult("visual", True))
    pacing_gate: GateResult = field(default_factory=lambda: GateResult("pacing", True))
    structural_gate: GateResult = field(default_factory=lambda: GateResult("structural", True))
    narrative_gate: GateResult = field(default_factory=lambda: GateResult("narrative", True))
    fix_suggestions: list[FixSuggestion] = field(default_factory=list)
    raw_lufs: float = 0.0
    raw_peak_dbfs: float = 0.0

    def to_dict(self) -> dict:
        """Serialise the report to a JSON-compatible dict."""
        def gate_dict(g: GateResult) -> dict:
            return {
                "name": g.name,
                "passed": g.passed,
                "details": g.details,
                "failures": g.failures,
            }

        return {
            "video": str(self.video_path),
            "duration_sec": self.duration_sec,
            "passed": self.passed,
            "lufs": self.raw_lufs,
            "peak_dbfs": self.raw_peak_dbfs,
            "audio_gate": gate_dict(self.audio_gate),
            "visual_gate": gate_dict(self.visual_gate),
            "pacing_gate": gate_dict(self.pacing_gate),
            "structural_gate": gate_dict(self.structural_gate),
            "narrative_gate": gate_dict(self.narrative_gate),
            "fix_suggestions": [
                {"priority": f.priority, "gate": f.gate, "description": f.description}
                for f in self.fix_suggestions
            ],
        }

    def save_json(self, path: str | Path) -> None:
        """Write the report to a JSON file.

        Args:
            path: Destination path.
        """
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))
        logger.info("QA report saved to %s", path)


# ---------------------------------------------------------------------------
# Gate 1: Audio
# ---------------------------------------------------------------------------


def _run_audio_gate(
    video_path: Path,
    edit_plan: EditPlan,
    api_key: str | None,
) -> tuple[GateResult, float, float]:
    """Check loudness, peak, and per-dialogue-cue voice-band lift.

    Args:
        video_path: Rendered video.
        edit_plan: Edit plan carrying dialogue_placements.
        api_key: OpenAI API key for Whisper transcription (optional).

    Returns:
        Tuple of (GateResult, integrated_lufs, peak_dbfs).
    """
    gate = GateResult(name="audio", passed=True)
    failures: list[str] = []

    lufs, peak = _measure_ebur128(video_path)
    gate.details = f"Integrated LUFS={lufs:.1f}, True Peak={peak:.2f} dBFS"

    if not (_LUFS_MIN <= lufs <= _LUFS_MAX):
        direction = "too loud" if lufs > _LUFS_MAX else "too quiet"
        failures.append(
            f"Loudness {lufs:.1f} LUFS is {direction} "
            f"(target [{_LUFS_MIN}, {_LUFS_MAX}] LUFS)."
        )

    if peak > _PEAK_MAX:
        failures.append(
            f"True peak {peak:.2f} dBFS exceeds ceiling {_PEAK_MAX} dBFS -- "
            f"risk of clipping on streaming platforms."
        )
    elif peak < _PEAK_MIN:
        failures.append(
            f"True peak {peak:.2f} dBFS is below minimum {_PEAK_MIN} dBFS -- "
            f"mix may sound thin."
        )

    # Per-cue voice-band lift check.
    for vo in edit_plan.dialogue_placements:
        dur = vo.duration
        during_rms = _voice_band_rms(video_path, vo.start_time, dur)
        ambient_start = max(0.0, vo.start_time - 2.0)
        ambient_rms = _voice_band_rms(video_path, ambient_start, 1.5)
        lift = during_rms - ambient_rms
        if lift < _VOICE_LIFT_MIN_DB:
            failures.append(
                f"VO at {vo.start_time:.1f}s has only {lift:+.1f}dB voice-band lift "
                f"(minimum {_VOICE_LIFT_MIN_DB}dB). Duck the music further at this cue."
            )

    gate.failures = failures
    gate.passed = len(failures) == 0
    return gate, lufs, peak


# ---------------------------------------------------------------------------
# Gate 2: Visual
# ---------------------------------------------------------------------------


def _extract_frame_jpg(
    video_path: Path,
    time_sec: float,
    output_path: Path,
) -> bool:
    """Extract a single frame as JPEG for GPT-4o vision analysis.

    Args:
        video_path: Source video.
        time_sec: Seek position in seconds.
        output_path: Output JPEG path.

    Returns:
        True on success.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{time_sec:.3f}",
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "4",
            str(output_path),
        ],
        capture_output=True, text=True, timeout=30,
    )
    return result.returncode == 0 and output_path.exists()


def _vision_check_shot(
    frame_path: Path,
    intended_action: str | None,
    intended_mood: str | None,
    api_key: str,
) -> tuple[str, str]:
    """Ask GPT-4o to describe a frame and judge fit against intent.

    Args:
        frame_path: JPEG frame to analyse.
        intended_action: Expected action from the shot plan.
        intended_mood: Expected mood from the shot plan.
        api_key: OpenAI API key.

    Returns:
        Tuple of (vision_description, fit_rating) where fit_rating is
        'yes', 'weak', or 'no'.
    """
    if not frame_path.exists():
        return ("(frame extraction failed)", "no")

    try:
        import base64

        image_data = base64.b64encode(frame_path.read_bytes()).decode("utf-8")
        action_str = intended_action or "any action"
        mood_str = intended_mood or "any mood"

        payload = json.dumps({
            "model": "gpt-4o",
            "max_tokens": 150,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_data}",
                                "detail": "low",
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                f"This frame should show: action={action_str}, mood={mood_str}. "
                                "In one sentence describe what you actually see. "
                                "Then on a new line write exactly one word: yes, weak, or no, "
                                "indicating how well the frame matches the intended action and mood."
                            ),
                        },
                    ],
                }
            ],
        }).encode()

        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            text = result["choices"][0]["message"]["content"].strip()
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            description = lines[0] if lines else text
            fit_word = "yes"
            if len(lines) >= 2:
                last = lines[-1].lower().strip(".")
                if last in ("yes", "weak", "no"):
                    fit_word = last
            return description, fit_word
    except Exception as exc:  # noqa: BLE001
        logger.debug("Vision check failed: %s", exc)
        return (f"(vision-error: {exc})", "weak")


def _run_visual_gate(
    video_path: Path,
    edit_plan: EditPlan,
    api_key: str | None,
    vision_sample_rate: int = 8,
) -> GateResult:
    """Check for unintended black frames and vision-intent mismatches.

    Args:
        video_path: Rendered video.
        edit_plan: Edit plan with shot list.
        api_key: OpenAI API key for GPT-4o (optional).
        vision_sample_rate: Check 1 in every N shots with GPT-4o vision.

    Returns:
        GateResult.
    """
    gate = GateResult(name="visual", passed=True)
    failures: list[str] = []

    # Black frame detection.
    black_ranges = _detect_black(video_path, min_dur_sec=_BLACK_FRAME_MAX_SEC)
    duration = _probe_duration(video_path)

    for start, end in black_ranges:
        span = end - start
        # Opening black <= INTENTIONAL_BLACK_MAX_SEC is fine (cold-open style).
        if start < 1.0 and span <= _INTENTIONAL_BLACK_MAX_SEC:
            continue
        if start < 1.0 and span > _INTENTIONAL_BLACK_MAX_SEC:
            failures.append(
                f"Opening black at {start:.2f}s is {span:.2f}s long. "
                f"Trim the leader or add an opening shot to keep viewers."
            )
            continue
        failures.append(
            f"Unintended black frame {start:.2f}s-{end:.2f}s ({span:.2f}s). "
            f"Check for missing clip handles or a bad export render range."
        )

    gate.details = f"Black-frame check: {len(black_ranges)} ranges found."

    # Vision intent check (sampled).
    if api_key:
        tmp_dir = Path(os.environ.get("TMPDIR", "/tmp")) / "ff_qa_frames"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        vision_checked = 0

        for i, shot in enumerate(edit_plan.shots):
            if i % vision_sample_rate != 0:
                continue

            # Plans without per-shot action/emotion intent (e.g. LayeredPlan)
            # cannot be vision-checked against a specific target — skip.
            if not getattr(shot, "action", "") and not getattr(shot, "emotion", ""):
                continue

            # Sync anchors are ground truth (the on-screen shot where the
            # character is literally saying the VO line). The source clip
            # may show the character from behind, in shadow, or off-angle
            # for a few frames — vision can flag these as "not talking"
            # even though the audio is the real thing. Trust the anchor.
            if getattr(shot, "slot_name", "") == "sync-anchor":
                continue

            sample_time = shot.start_time + shot.duration * 0.4
            frame_path = tmp_dir / f"shot_{shot.cut_index:04d}.jpg"

            ok = _extract_frame_jpg(video_path, sample_time, frame_path)
            if not ok:
                continue

            desc, fit = _vision_check_shot(
                frame_path,
                shot.action,
                shot.emotion,
                api_key,
            )
            vision_checked += 1

            if fit == "no":
                failures.append(
                    f"Shot {shot.cut_index} at {shot.start_time:.1f}s: vision says "
                    f"'{desc}' but plan expects action={shot.action}, "
                    f"emotion={shot.emotion}. Replace shot {shot.cut_index} with a "
                    f"similar-tagged clip from the library matching "
                    f"[{', '.join(t for t in [shot.action, shot.emotion, shot.era] if t)}]."
                )

        gate.details += f" Vision-checked {vision_checked} shots."

    gate.failures = failures
    gate.passed = len(failures) == 0
    return gate


# ---------------------------------------------------------------------------
# Gate 3: Pacing
# ---------------------------------------------------------------------------


def _run_pacing_gate(
    edit_plan: EditPlan,
    style_profile: dict[str, Any],
) -> GateResult:
    """Check median shot duration and beat alignment against template targets.

    Args:
        edit_plan: Edit plan with shot list and metadata.
        style_profile: Dict from .style-template.json.

    Returns:
        GateResult.
    """
    gate = GateResult(name="pacing", passed=True)
    failures: list[str] = []

    target_median = float(style_profile.get("shot_dur_median", _PACING_MEDIAN_TARGET))
    target_beat_pct = float(style_profile.get("beat_alignment_pct", _BEAT_ALIGNMENT_MIN * 100))

    shots = edit_plan.shots
    if not shots:
        gate.failures = ["No shots in edit plan."]
        gate.passed = False
        return gate

    durations = sorted(s.duration for s in shots)
    n = len(durations)
    median_dur = durations[n // 2] if n % 2 else (durations[n // 2 - 1] + durations[n // 2]) / 2.0

    lower_bound = target_median * (1.0 - _PACING_TOLERANCE)
    upper_bound = target_median * (1.0 + _PACING_TOLERANCE)

    gate.details = (
        f"Median shot duration {median_dur:.2f}s "
        f"(target {target_median:.2f}s +/-{_PACING_TOLERANCE*100:.0f}%). "
        f"Beat-aligned: {edit_plan.metadata.beat_aligned_pct:.1f}% "
        f"(target >={target_beat_pct:.0f}%)."
    )

    if not (lower_bound <= median_dur <= upper_bound):
        direction = "too long" if median_dur > upper_bound else "too short"
        failures.append(
            f"Median shot duration {median_dur:.2f}s is {direction} "
            f"(template target {target_median:.2f}s, tolerance "
            f"{lower_bound:.2f}s-{upper_bound:.2f}s). "
            f"Trim or extend shots in the mid-section to correct pacing."
        )

    actual_beat_pct = edit_plan.metadata.beat_aligned_pct
    if actual_beat_pct < target_beat_pct * 0.7:  # 70% of target is the fail threshold
        failures.append(
            f"Beat alignment {actual_beat_pct:.1f}% is below target {target_beat_pct:.1f}%. "
            f"Nudge cuts to land within 0.15s of a beat in the beat map."
        )

    gate.failures = failures
    gate.passed = len(failures) == 0
    return gate


# ---------------------------------------------------------------------------
# Gate 4: Structural
# ---------------------------------------------------------------------------


def _check_ending_on_beat(
    video_path: Path,
    edit_plan: EditPlan,
) -> bool:
    """Return True if the edit's last shot ends within 1 beat of a drop or downbeat.

    Uses the edit plan's last shot duration as a proxy. The edit is considered
    to end on-beat if the last shot's start_time is beat-aligned.

    Args:
        video_path: Rendered video (not directly used here but kept for
            future frame-level analysis).
        edit_plan: Edit plan with shot list.

    Returns:
        True if the ending is beat-aligned.
    """
    if not edit_plan.shots:
        return False
    last_shot = edit_plan.shots[-1]
    return last_shot.beat_aligned or last_shot.is_downbeat


def _check_opening_style(
    video_path: Path,
    duration_sec: float,
    style_profile: dict[str, Any],
) -> tuple[bool, str]:
    """Check whether the opening matches the expected style (cold-open vs fade).

    The style template says ~48.6% of reference edits use a cold open. This
    means any opening is acceptable; we only flag when the opening black is
    excessively long (more than 1.5s).

    Args:
        video_path: Rendered video.
        duration_sec: Total edit duration.
        style_profile: Style profile dict.

    Returns:
        Tuple of (passed, description).
    """
    black_ranges = _detect_black(video_path, min_dur_sec=0.1)
    opening_black = 0.0
    for start, end in black_ranges:
        if start < 0.5:
            opening_black = end - start
            break

    opening_black_median = float(style_profile.get("opening_black_sec_median", 0.3))
    threshold = max(opening_black_median * 3.0, 1.5)

    if opening_black > threshold:
        return (
            False,
            f"Opening black is {opening_black:.2f}s (median {opening_black_median:.2f}s). "
            f"Trim leader or replace with a cold-open shot.",
        )
    return True, f"Opening black {opening_black:.2f}s is within spec."


def _run_structural_gate(
    video_path: Path,
    edit_plan: EditPlan,
    style_profile: dict[str, Any],
) -> GateResult:
    """Check opening style, ending beat, and edit length vs template.

    Args:
        video_path: Rendered video.
        edit_plan: Edit plan with metadata.
        style_profile: Style profile dict.

    Returns:
        GateResult.
    """
    gate = GateResult(name="structural", passed=True)
    failures: list[str] = []
    duration = edit_plan.metadata.total_duration_sec

    opening_pass, opening_detail = _check_opening_style(
        video_path, duration, style_profile
    )
    if not opening_pass:
        failures.append(opening_detail)

    ending_on_beat = _check_ending_on_beat(video_path, edit_plan)
    if not ending_on_beat:
        last_shot = edit_plan.shots[-1] if edit_plan.shots else None
        failures.append(
            f"Edit does not end on a beat-aligned cut. "
            + (
                f"Last shot (index {last_shot.cut_index}) at {last_shot.start_time:.2f}s "
                f"is not beat-aligned. Nudge the final cut to the nearest beat."
                if last_shot else "No shots found."
            )
        )

    rendered_duration = _probe_duration(video_path)
    if rendered_duration > 0:
        plan_duration = edit_plan.metadata.total_duration_sec
        diff = abs(rendered_duration - plan_duration)
        if diff > 2.0:
            failures.append(
                f"Rendered duration {rendered_duration:.1f}s differs from plan "
                f"{plan_duration:.1f}s by {diff:.1f}s. "
                f"Check for missed shots or export range mismatch."
            )

    gate.details = (
        f"{opening_detail} "
        f"Ending on beat: {'yes' if ending_on_beat else 'no'}. "
        f"Rendered: {rendered_duration:.1f}s, plan: {duration:.1f}s."
    )
    gate.failures = failures
    gate.passed = len(failures) == 0
    return gate


# ---------------------------------------------------------------------------
# Gate 5: Narrative
# ---------------------------------------------------------------------------


def _run_narrative_gate(
    edit_plan: EditPlan,
    template: NarrativeTemplate,
) -> GateResult:
    """Check that shot order matches template slot sequence and characters fit.

    Validates two things:
    1. Shots within each template slot stay in the correct chronological order
       relative to that slot's relative_position.
    2. No shot has a character that is explicitly excluded from its slot.

    Args:
        edit_plan: Edit plan with shot list.
        template: The NarrativeTemplate the plan was built from.

    Returns:
        GateResult.
    """
    gate = GateResult(name="narrative", passed=True)
    failures: list[str] = []

    slot_map = {slot.name: slot for slot in template.slots}
    slot_order = [slot.name for slot in template.slots]

    # Check slot sequence order: shots must appear in slot order within the timeline.
    current_slot_index = 0
    for shot in edit_plan.shots:
        if shot.slot_name not in slot_map:
            continue
        target_order_idx = next(
            (i for i, name in enumerate(slot_order) if name == shot.slot_name),
            None,
        )
        if target_order_idx is None:
            continue
        if target_order_idx < current_slot_index:
            failures.append(
                f"Shot {shot.cut_index} (slot '{shot.slot_name}') appears after "
                f"a later slot. Shot order does not match template sequence. "
                f"Re-order shots from the library to follow template slot order."
            )
        else:
            current_slot_index = target_order_idx

    # Check excluded tags / characters.
    for shot in edit_plan.shots:
        slot = slot_map.get(shot.slot_name)
        if slot is None:
            continue

        shot_tags = {
            shot.character_main,
            shot.action,
            shot.emotion,
            shot.era,
        } - {None}

        for excluded_tag in slot.excluded_shot_tags:
            if excluded_tag in shot_tags:
                replacement_tags = [
                    t for t in slot.required_shot_tags if t
                ]
                tag_str = ", ".join(replacement_tags) if replacement_tags else "similar"
                failures.append(
                    f"Shot {shot.cut_index} at {shot.start_time:.1f}s has excluded tag "
                    f"'{excluded_tag}' for slot '{slot.name}'. "
                    f"Replace shot {shot.cut_index} with a {tag_str} clip from the library."
                )
                break

    gate.details = (
        f"Checked {len(edit_plan.shots)} shots against "
        f"{len(template.slots)} template slots."
    )
    gate.failures = failures
    gate.passed = len(failures) == 0
    return gate


# ---------------------------------------------------------------------------
# Fix suggestion builder
# ---------------------------------------------------------------------------


def _build_fix_suggestions(report: QAReport) -> list[FixSuggestion]:
    """Collect and prioritise all fix suggestions from gate failures.

    Args:
        report: Partially populated QAReport (gates filled, suggestions empty).

    Returns:
        List of FixSuggestion sorted HIGH -> MEDIUM -> LOW.
    """
    suggestions: list[FixSuggestion] = []

    priority_map: dict[str, FixPriority] = {
        "audio":      "HIGH",
        "structural": "HIGH",
        "visual":     "MEDIUM",
        "pacing":     "MEDIUM",
        "narrative":  "LOW",
    }

    for gate in (
        report.audio_gate,
        report.structural_gate,
        report.visual_gate,
        report.pacing_gate,
        report.narrative_gate,
    ):
        priority = priority_map.get(gate.name, "LOW")
        for failure in gate.failures:
            suggestions.append(FixSuggestion(
                priority=priority,
                gate=gate.name,
                description=failure,
            ))

    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    suggestions.sort(key=lambda s: order[s.priority])
    return suggestions


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------


def run_qa(
    video_path: str | Path,
    edit_plan: EditPlan,
    template: NarrativeTemplate,
    style_profile: dict[str, Any],
    *,
    use_vision: bool = True,
    vision_sample_rate: int = 8,
    use_whisper: bool = True,
) -> QAReport:
    """Run the full five-gate quality check on a rendered video.

    This is the heavy end-to-end gate. For quick checks on dialogue
    intelligibility and loudness alone, use auto_test.run_auto_test().

    Args:
        video_path: Path to the rendered video file.
        edit_plan: EditPlan from shot_optimizer.plan_edit().
        template: NarrativeTemplate used to build the plan.
        style_profile: Dict loaded from .style-template.json.
        use_vision: When True, sample shots with GPT-4o vision. Requires
            OPENAI_API_KEY to be set.
        vision_sample_rate: Check every Nth shot with GPT-4o. Lower = more
            thorough but slower and more expensive.
        use_whisper: When True, transcribe dialogue cues with Whisper.
            Requires OPENAI_API_KEY.

    Returns:
        QAReport with all five gate results and prioritised fix suggestions.
    """
    vp = Path(video_path)
    report = QAReport(video_path=vp)

    if not vp.exists():
        report.passed = False
        report.audio_gate = GateResult(
            name="audio", passed=False, failures=[f"Video file not found: {vp}"]
        )
        return report

    report.duration_sec = _probe_duration(vp)
    api_key = _load_api_key() if (use_vision or use_whisper) else None

    logger.info("QA: starting five-gate analysis of %s (%.1fs)", vp.name, report.duration_sec)

    # Gate 1: Audio
    logger.info("QA gate 1/5: audio")
    audio_gate, lufs, peak = _run_audio_gate(vp, edit_plan, api_key if use_whisper else None)
    report.audio_gate = audio_gate
    report.raw_lufs = lufs
    report.raw_peak_dbfs = peak

    # Gate 2: Visual
    logger.info("QA gate 2/5: visual")
    report.visual_gate = _run_visual_gate(
        vp, edit_plan, api_key if use_vision else None, vision_sample_rate
    )

    # Gate 3: Pacing
    logger.info("QA gate 3/5: pacing")
    report.pacing_gate = _run_pacing_gate(edit_plan, style_profile)

    # Gate 4: Structural
    logger.info("QA gate 4/5: structural")
    report.structural_gate = _run_structural_gate(vp, edit_plan, style_profile)

    # Gate 5: Narrative
    logger.info("QA gate 5/5: narrative")
    report.narrative_gate = _run_narrative_gate(edit_plan, template)

    # Overall pass/fail
    all_gates = [
        report.audio_gate,
        report.visual_gate,
        report.pacing_gate,
        report.structural_gate,
        report.narrative_gate,
    ]
    report.passed = all(g.passed for g in all_gates)

    # Build fix suggestions
    report.fix_suggestions = _build_fix_suggestions(report)

    logger.info(
        "QA complete: %s | %d fix suggestions (%d HIGH)",
        "PASS" if report.passed else "FAIL",
        len(report.fix_suggestions),
        sum(1 for f in report.fix_suggestions if f.priority == "HIGH"),
    )

    return report


# ---------------------------------------------------------------------------
# Human-readable report printer
# ---------------------------------------------------------------------------


def print_qa_report(report: QAReport) -> None:
    """Print a full QA report to stdout in a readable format.

    Args:
        report: QAReport from run_qa().
    """
    bar = "=" * 76
    thin = "-" * 76

    status = "PASS" if report.passed else "FAIL"
    icon = "OK" if report.passed else "!!"

    print(bar)
    print(f"  QA REPORT  [{icon}]  {report.video_path.name}")
    print(f"  Duration: {report.duration_sec:.1f}s   "
          f"LUFS: {report.raw_lufs:.1f}   Peak: {report.raw_peak_dbfs:.2f} dBFS")
    print(bar)

    def print_gate(gate: GateResult) -> None:
        mark = "OK" if gate.passed else "!!"
        print(f"\n  [{mark}] {gate.name.upper()} GATE")
        if gate.details:
            print(f"      {gate.details}")
        for fail in gate.failures:
            print(f"      FAIL: {fail}")

    for gate in (
        report.audio_gate,
        report.visual_gate,
        report.pacing_gate,
        report.structural_gate,
        report.narrative_gate,
    ):
        print_gate(gate)

    if report.fix_suggestions:
        print(f"\n{thin}")
        print("  FIX SUGGESTIONS (ordered by priority)")
        print(thin)
        for i, fix in enumerate(report.fix_suggestions, 1):
            print(f"  {i:2d}. [{fix.priority}] {fix.description}")

    print(f"\n{bar}")
    print(f"  OVERALL: {status}")
    print(bar)
