"""Feedback loop for iterative user correction of rendered edit plans.

After a user watches a rendered cut and flags problems, this module applies
targeted corrections without touching unaffected shots. Each correction is
logged, the original plan is preserved for diffing, and the returned plan is
a new EditPlan with only the corrected regions mutated.

Usage examples (mirrors the CLI interface):
    ff fix --shot 14 --reason "that's victor not leon"
    ff fix --cue 2 --reason "dialogue too late"
    ff fix --pacing "too slow in act 2"
    ff fix --color "too teal"
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .shot_optimizer import EditPlan, EditPlanMeta, ShotRecord, VOPlacement

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Correction kinds
# ---------------------------------------------------------------------------

CorrectionKind = Literal["shot", "cue", "pacing", "color"]


@dataclass
class FeedbackCorrection:
    """A single user correction applied to an edit plan.

    Attributes:
        kind: Category of correction -- shot, cue, pacing, or color.
        target_id: Shot cut_index, VO cue index, act name, or color descriptor.
        reason: Free-text reason from the user explaining what was wrong.
        applied_delta: Human-readable description of what was changed and by
            how much. Populated after apply_feedback() runs.
    """

    kind: CorrectionKind
    target_id: str
    reason: str
    applied_delta: str = ""


@dataclass
class PlanRevision:
    """A versioned snapshot of an EditPlan after one correction pass.

    Attributes:
        version: Monotonically increasing version number.
        plan: The revised EditPlan.
        corrections: All corrections applied to produce this version.
        parent_version: Version number this was derived from. None for v1.
    """

    version: int
    plan: EditPlan
    corrections: list[FeedbackCorrection]
    parent_version: int | None = None

    def diff_summary(self, parent: "PlanRevision") -> list[str]:
        """Return human-readable diff lines comparing this revision to parent.

        Args:
            parent: The PlanRevision this was derived from.

        Returns:
            List of diff description strings.
        """
        lines: list[str] = []

        old_shots = {s.cut_index: s for s in parent.plan.shots}
        new_shots = {s.cut_index: s for s in self.plan.shots}

        for idx, new_shot in new_shots.items():
            old_shot = old_shots.get(idx)
            if old_shot is None:
                lines.append(f"  + shot {idx:03d} added: {new_shot.source}")
                continue
            if old_shot.source != new_shot.source:
                lines.append(
                    f"  ~ shot {idx:03d} source: {old_shot.source} -> {new_shot.source}"
                )
            if abs(old_shot.duration - new_shot.duration) > 0.01:
                lines.append(
                    f"  ~ shot {idx:03d} duration: {old_shot.duration:.2f}s -> {new_shot.duration:.2f}s"
                )
            if old_shot.start_time != new_shot.start_time:
                lines.append(
                    f"  ~ shot {idx:03d} start_time: {old_shot.start_time:.3f}s -> {new_shot.start_time:.3f}s"
                )

        for idx in old_shots:
            if idx not in new_shots:
                lines.append(f"  - shot {idx:03d} removed")

        old_cues = {i: c for i, c in enumerate(parent.plan.dialogue_placements)}
        new_cues = {i: c for i, c in enumerate(self.plan.dialogue_placements)}
        for i, new_cue in new_cues.items():
            old_cue = old_cues.get(i)
            if old_cue and abs(old_cue.start_time - new_cue.start_time) > 0.01:
                lines.append(
                    f"  ~ cue {i}: start_time {old_cue.start_time:.3f}s -> {new_cue.start_time:.3f}s"
                )

        if not lines:
            lines.append("  (no structural changes detected)")

        return lines


# ---------------------------------------------------------------------------
# Core correction functions
# ---------------------------------------------------------------------------

def _find_shot_window(
    plan: EditPlan,
    cut_index: int,
    window: int = 1,
) -> list[int]:
    """Return a list of cut_indices to re-evaluate around cut_index.

    Args:
        plan: The current EditPlan.
        cut_index: Centre shot to fix.
        window: Number of shots on each side to include in the re-solve window.

    Returns:
        Sorted list of cut_indices within the window that exist in the plan.
    """
    indices = {s.cut_index for s in plan.shots}
    result: list[int] = []
    for delta in range(-window, window + 1):
        candidate = cut_index + delta
        if candidate in indices:
            result.append(candidate)
    return sorted(result)


def _block_shot_source(
    plan: EditPlan,
    cut_index: int,
    reason: str,
    blocked_sources: set[str],
) -> tuple[EditPlan, FeedbackCorrection]:
    """Block the source of a specific shot and re-assign from remaining shots.

    Finds the next best shot that does not share the blocked source, pulling
    from shots already present in the plan that are NOT in the re-solve window.
    This keeps the surrounding context intact.

    When no suitable replacement exists in the plan (edge case with very sparse
    libraries), the original shot is kept but its intent is flagged so the user
    can manually override.

    Args:
        plan: Current EditPlan.
        cut_index: The shot to replace.
        reason: User-supplied reason for the block.
        blocked_sources: Set of source identifiers to exclude. Updated in place
            with the source of the flagged shot.

    Returns:
        Tuple of (revised_plan, correction_record).
    """
    shots = list(plan.shots)
    target_idx = next((i for i, s in enumerate(shots) if s.cut_index == cut_index), None)

    if target_idx is None:
        correction = FeedbackCorrection(
            kind="shot",
            target_id=str(cut_index),
            reason=reason,
            applied_delta=f"shot {cut_index} not found in plan",
        )
        return plan, correction

    target_shot = shots[target_idx]
    blocked_sources.add(target_shot.source)

    # Find a donor shot: same slot, different source, not already in the
    # re-solve window, preferring matching emotion/action.
    same_slot = [
        s for s in shots
        if s.slot_name == target_shot.slot_name
        and s.source not in blocked_sources
        and s.cut_index != cut_index
    ]

    # Score donors by attribute overlap with the target slot mood
    def _donor_score(s: ShotRecord) -> float:
        score = 0.0
        if s.emotion == target_shot.emotion:
            score += 2.0
        if s.action == target_shot.action:
            score += 1.5
        if s.mood_profile == target_shot.mood_profile:
            score += 1.0
        return score

    same_slot.sort(key=_donor_score, reverse=True)

    if same_slot:
        donor = same_slot[0]
        replacement = ShotRecord(
            cut_index=target_shot.cut_index,
            slot_name=target_shot.slot_name,
            start_time=target_shot.start_time,
            duration=target_shot.duration,
            source=donor.source,
            clip_start_sec=donor.clip_start_sec,
            clip_end_sec=donor.clip_end_sec,
            era=donor.era,
            character_main=donor.character_main,
            character_speaks=donor.character_speaks,
            action=donor.action,
            emotion=donor.emotion,
            mood_profile=target_shot.mood_profile,
            beat_aligned=target_shot.beat_aligned,
            is_downbeat=target_shot.is_downbeat,
            shot_library_id=donor.shot_library_id,
            desc=donor.desc,
            intent=f"[CORRECTED] {donor.source} substituted for {target_shot.source}. {donor.intent}",
        )
        shots[target_idx] = replacement
        delta = f"replaced shot {cut_index}: {target_shot.source} -> {donor.source}"
    else:
        # Flag only, no replacement available
        flagged = ShotRecord(
            **{
                **asdict(target_shot),
                "intent": f"[FLAGGED: {reason}] {target_shot.intent}",
            }
        )
        shots[target_idx] = flagged
        delta = f"flagged shot {cut_index} (no replacement found for {target_shot.source})"

    revised = EditPlan(
        shots=shots,
        dialogue_placements=list(plan.dialogue_placements),
        metadata=plan.metadata,
    )
    correction = FeedbackCorrection(
        kind="shot",
        target_id=str(cut_index),
        reason=reason,
        applied_delta=delta,
    )
    return revised, correction


def _fix_cue_timing(
    plan: EditPlan,
    cue_index: int,
    reason: str,
    shift_sec: float = -0.5,
) -> tuple[EditPlan, FeedbackCorrection]:
    """Shift a VO cue's start time by shift_sec, or move it to an adjacent slot.

    If the shifted position would overlap an existing cue by more than 50%,
    this function tries placing the cue on the next silent shot instead.

    Args:
        plan: Current EditPlan.
        cue_index: Index into plan.dialogue_placements.
        reason: User reason for the correction.
        shift_sec: How many seconds to shift the cue start. Negative = earlier.

    Returns:
        Tuple of (revised_plan, correction_record).
    """
    cues = list(plan.dialogue_placements)

    if cue_index < 0 or cue_index >= len(cues):
        correction = FeedbackCorrection(
            kind="cue",
            target_id=str(cue_index),
            reason=reason,
            applied_delta=f"cue index {cue_index} out of range",
        )
        return plan, correction

    target_cue = cues[cue_index]
    old_start = target_cue.start_time
    new_start = max(0.0, round(old_start + shift_sec, 4))

    # Check for overlap with adjacent cues
    other_intervals = [
        (c.start_time, c.start_time + c.duration)
        for i, c in enumerate(cues)
        if i != cue_index
    ]

    def _overlap(a_start: float, a_dur: float, b_start: float, b_end: float) -> float:
        """Return overlap fraction relative to a's duration."""
        a_end = a_start + a_dur
        overlap = max(0.0, min(a_end, b_end) - max(a_start, b_start))
        return overlap / max(a_dur, 0.001)

    bad_overlap = any(
        _overlap(new_start, target_cue.duration, b_start, b_end) > 0.5
        for b_start, b_end in other_intervals
    )

    if bad_overlap:
        # Try to find adjacent shot slot
        current_cut_idx = target_cue.cut_index
        shot_map = {s.cut_index: s for s in plan.shots}
        existing_cue_cuts = {c.cut_index for i, c in enumerate(cues) if i != cue_index}

        adjacent_candidates = [
            s for s in plan.shots
            if s.cut_index in (current_cut_idx - 1, current_cut_idx + 1)
            and not s.character_speaks
            and s.cut_index not in existing_cue_cuts
            and s.duration >= target_cue.duration
        ]

        if adjacent_candidates:
            new_host = adjacent_candidates[0]
            new_start = round(new_host.start_time, 4)
            updated_cue = VOPlacement(
                cut_index=new_host.cut_index,
                audio_path=target_cue.audio_path,
                expected_line=target_cue.expected_line,
                start_time=new_start,
                duration=target_cue.duration,
                pre_cut_frames=target_cue.pre_cut_frames,
                slot_name=new_host.slot_name,
            )
            delta = (
                f"moved cue {cue_index} from cut {current_cut_idx} "
                f"to cut {new_host.cut_index} (overlap conflict)"
            )
        else:
            # Just clamp the shift to avoid the worst of the overlap
            new_start = old_start + shift_sec * 0.25
            new_start = max(0.0, round(new_start, 4))
            updated_cue = VOPlacement(
                cut_index=target_cue.cut_index,
                audio_path=target_cue.audio_path,
                expected_line=target_cue.expected_line,
                start_time=new_start,
                duration=target_cue.duration,
                pre_cut_frames=target_cue.pre_cut_frames,
                slot_name=target_cue.slot_name,
            )
            delta = f"cue {cue_index}: partial shift {old_start:.3f}s -> {new_start:.3f}s (overlap limited)"
    else:
        updated_cue = VOPlacement(
            cut_index=target_cue.cut_index,
            audio_path=target_cue.audio_path,
            expected_line=target_cue.expected_line,
            start_time=new_start,
            duration=target_cue.duration,
            pre_cut_frames=target_cue.pre_cut_frames,
            slot_name=target_cue.slot_name,
        )
        delta = f"cue {cue_index}: start_time {old_start:.3f}s -> {new_start:.3f}s (shift {shift_sec:+.2f}s)"

    cues[cue_index] = updated_cue
    revised = EditPlan(
        shots=list(plan.shots),
        dialogue_placements=cues,
        metadata=plan.metadata,
    )
    correction = FeedbackCorrection(
        kind="cue",
        target_id=str(cue_index),
        reason=reason,
        applied_delta=delta,
    )
    return revised, correction


def _fix_pacing(
    plan: EditPlan,
    act_description: str,
    reason: str,
    duration_scale: float = 0.80,
) -> tuple[EditPlan, FeedbackCorrection]:
    """Scale shot durations for shots within a described act region.

    Identifies the act by matching against slot names using the description.
    Falls back to time-based heuristics ("act 1" = first third, "act 2" =
    middle third, "act 3" = final third).

    Args:
        plan: Current EditPlan.
        act_description: User description like "act 2", "chorus", "intro".
        reason: User reason for the pacing correction.
        duration_scale: Multiplier applied to each shot's duration in the
            identified region. Values < 1.0 speed up, > 1.0 slow down.

    Returns:
        Tuple of (revised_plan, correction_record).
    """
    total_dur = plan.metadata.total_duration_sec

    # Determine time range for the act
    act_lower = act_desc_lower = act_description.lower().strip()

    # Named act heuristics
    if "act 1" in act_desc_lower or "first" in act_desc_lower:
        t_lo, t_hi = 0.0, total_dur * 0.33
        act_label = "act 1"
    elif "act 2" in act_desc_lower or "middle" in act_desc_lower or "second" in act_desc_lower:
        t_lo, t_hi = total_dur * 0.33, total_dur * 0.66
        act_label = "act 2"
    elif "act 3" in act_desc_lower or "last" in act_desc_lower or "third" in act_desc_lower or "finale" in act_desc_lower:
        t_lo, t_hi = total_dur * 0.66, total_dur
        act_label = "act 3"
    else:
        # Match against slot names
        matching_slots = {
            s.slot_name
            for s in plan.shots
            if act_lower in s.slot_name.lower() or s.mood_profile == act_lower
        }
        if matching_slots:
            act_shots = [s for s in plan.shots if s.slot_name in matching_slots]
            t_lo = min(s.start_time for s in act_shots)
            t_hi = max(s.start_time + s.duration for s in act_shots)
            act_label = ", ".join(sorted(matching_slots))
        else:
            # Full plan
            t_lo, t_hi = 0.0, total_dur
            act_label = "full plan"

    shots_in_act = [s for s in plan.shots if t_lo <= s.start_time < t_hi]
    modified_count = 0
    revised_shots = list(plan.shots)

    for i, shot in enumerate(revised_shots):
        if shot.cut_index in {s.cut_index for s in shots_in_act}:
            new_dur = round(shot.duration * duration_scale, 4)
            new_dur = max(0.3, new_dur)
            revised_shots[i] = ShotRecord(
                **{**asdict(shot), "duration": new_dur}
            )
            modified_count += 1

    # Recalculate start times after duration changes to keep timeline coherent
    revised_shots = _recalculate_start_times(revised_shots, plan.shots)

    delta = (
        f"scaled {modified_count} shots in '{act_label}' "
        f"(t={t_lo:.1f}s-{t_hi:.1f}s) by {duration_scale:.0%}"
    )

    revised = EditPlan(
        shots=revised_shots,
        dialogue_placements=list(plan.dialogue_placements),
        metadata=plan.metadata,
    )
    correction = FeedbackCorrection(
        kind="pacing",
        target_id=act_description,
        reason=reason,
        applied_delta=delta,
    )
    return revised, correction


def _recalculate_start_times(
    revised_shots: list[ShotRecord],
    original_shots: list[ShotRecord],
) -> list[ShotRecord]:
    """Propagate start_time changes forward after duration edits.

    Only propagates within the same contiguous block of modified shots.
    Shots outside the modified region keep their original start times,
    preventing a local pacing fix from shifting the whole timeline.

    Args:
        revised_shots: Shot list with modified durations.
        original_shots: Original shots before modification.

    Returns:
        Revised shot list with recalculated start times.
    """
    orig_map = {s.cut_index: s for s in original_shots}
    result: list[ShotRecord] = []
    cursor = 0.0

    for shot in revised_shots:
        orig = orig_map.get(shot.cut_index)
        if orig is None:
            result.append(shot)
            cursor = shot.start_time + shot.duration
            continue

        dur_changed = abs(shot.duration - orig.duration) > 0.001
        if dur_changed:
            # Use the propagated cursor position
            new_start = round(cursor, 4)
            result.append(ShotRecord(**{**asdict(shot), "start_time": new_start}))
            cursor = new_start + shot.duration
        else:
            result.append(shot)
            cursor = shot.start_time + shot.duration

    return result


def _fix_color(
    plan: EditPlan,
    color_description: str,
    reason: str,
    lut_intensity_delta: float = -0.15,
) -> tuple[EditPlan, FeedbackCorrection]:
    """Adjust LUT intensity metadata globally or for a named color attribute.

    This function does not re-render video -- it updates the intent strings and
    metadata of every shot to carry a lut_intensity_override field that the
    NLE export and assembly pipeline can pick up.

    Args:
        plan: Current EditPlan.
        color_description: User description like "too teal", "too warm".
        reason: User reason for the correction.
        lut_intensity_delta: Fractional adjustment to add to current LUT
            intensity. -0.15 means reduce by 15 percentage points.

    Returns:
        Tuple of (revised_plan, correction_record).
    """
    shots = list(plan.shots)
    modified = 0

    for i, shot in enumerate(shots):
        current_intent = shot.intent
        # Append lut intensity override into intent so downstream can parse it
        if "lut_intensity" in current_intent:
            # Update existing value
            import re
            def _update_lut(m: "re.Match[str]") -> str:
                old_val = float(m.group(1))
                new_val = round(max(0.0, min(1.0, old_val + lut_intensity_delta)), 3)
                return f"lut_intensity={new_val}"
            new_intent = re.sub(r"lut_intensity=([\d.]+)", _update_lut, current_intent)
        else:
            current_intensity = 1.0
            new_intensity = round(max(0.0, min(1.0, current_intensity + lut_intensity_delta)), 3)
            new_intent = current_intent + f" [lut_intensity={new_intensity}]"

        shots[i] = ShotRecord(**{**asdict(shot), "intent": new_intent})
        modified += 1

    delta = (
        f"applied lut_intensity_delta={lut_intensity_delta:+.0%} "
        f"to {modified} shots (reason: {color_description})"
    )

    revised = EditPlan(
        shots=shots,
        dialogue_placements=list(plan.dialogue_placements),
        metadata=plan.metadata,
    )
    correction = FeedbackCorrection(
        kind="color",
        target_id=color_description,
        reason=reason,
        applied_delta=delta,
    )
    return revised, correction


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_feedback(
    edit_plan: EditPlan,
    corrections: list[FeedbackCorrection],
    blocked_sources: set[str] | None = None,
) -> EditPlan:
    """Apply a list of corrections to an edit plan and return the revised plan.

    This is the primary public entry point. Each correction in the list is
    applied in order. The original plan is never mutated.

    Args:
        edit_plan: The EditPlan to revise. Not mutated.
        corrections: Ordered list of FeedbackCorrection instances. Applied
            sequentially so later corrections see the effects of earlier ones.
        blocked_sources: Optional set of source identifiers already blocked
            from a previous revision. Updated in place as new shot corrections
            add their blocked sources.

    Returns:
        A new EditPlan with all corrections applied.
    """
    if blocked_sources is None:
        blocked_sources = set()

    plan = copy.deepcopy(edit_plan)

    for idx, correction in enumerate(corrections):
        kind = correction.kind

        if kind == "shot":
            try:
                cut_index = int(correction.target_id)
            except ValueError:
                logger.warning("Invalid shot target_id: %s", correction.target_id)
                corrections[idx].applied_delta = f"invalid target_id: {correction.target_id}"
                continue
            plan, applied = _block_shot_source(
                plan, cut_index, correction.reason, blocked_sources
            )
            corrections[idx].applied_delta = applied.applied_delta

        elif kind == "cue":
            try:
                cue_index = int(correction.target_id)
            except ValueError:
                logger.warning("Invalid cue target_id: %s", correction.target_id)
                corrections[idx].applied_delta = f"invalid target_id: {correction.target_id}"
                continue

            # Parse optional shift from reason string (e.g. "too late, shift -1.5")
            import re
            shift_match = re.search(r"shift\s+([+-]?\d+\.?\d*)", correction.reason, re.IGNORECASE)
            shift_sec = float(shift_match.group(1)) if shift_match else -0.5
            plan, applied = _fix_cue_timing(
                plan, cue_index, correction.reason, shift_sec=shift_sec
            )
            corrections[idx].applied_delta = applied.applied_delta

        elif kind == "pacing":
            # Scale factor can be embedded in reason: "too slow -> faster" = 0.80
            reason_lower = correction.reason.lower()
            if "slow" in reason_lower or "sluggish" in reason_lower or "drag" in reason_lower:
                scale = 0.80
            elif "fast" in reason_lower or "rushed" in reason_lower or "frenetic" in reason_lower:
                scale = 1.20
            else:
                scale = 0.80  # default: speed up
            plan, applied = _fix_pacing(
                plan, correction.target_id, correction.reason, duration_scale=scale
            )
            corrections[idx].applied_delta = applied.applied_delta

        elif kind == "color":
            # Parse direction from reason
            reason_lower = correction.reason.lower()
            if any(w in reason_lower for w in ("too teal", "too blue", "too cyan", "too cold")):
                delta = -0.15
            elif any(w in reason_lower for w in ("too warm", "too orange", "too yellow")):
                delta = -0.15
            elif any(w in reason_lower for w in ("too saturated", "too vibrant")):
                delta = -0.20
            elif any(w in reason_lower for w in ("too dark", "too desaturated", "too grey", "too gray")):
                delta = 0.10
            else:
                delta = -0.15
            plan, applied = _fix_color(
                plan, correction.target_id, correction.reason, lut_intensity_delta=delta
            )
            corrections[idx].applied_delta = applied.applied_delta

        else:
            logger.warning("Unknown correction kind: %s", kind)
            corrections[idx].applied_delta = f"unknown kind: {kind}"

    return plan


def build_revision(
    parent: PlanRevision,
    corrections: list[FeedbackCorrection],
    blocked_sources: set[str] | None = None,
) -> PlanRevision:
    """Apply corrections to a PlanRevision and return a new versioned revision.

    Args:
        parent: The PlanRevision to base the new version on.
        corrections: Corrections to apply.
        blocked_sources: Optional set of already-blocked sources, passed
            through to apply_feedback().

    Returns:
        A new PlanRevision with version = parent.version + 1.
    """
    revised_plan = apply_feedback(parent.plan, corrections, blocked_sources)
    return PlanRevision(
        version=parent.version + 1,
        plan=revised_plan,
        corrections=corrections,
        parent_version=parent.version,
    )


def load_feedback_from_file(path: Path | str) -> list[FeedbackCorrection]:
    """Load a list of FeedbackCorrection instances from a JSON file.

    The file should be a JSON array of objects, each with fields:
    kind, target_id, reason. applied_delta is optional.

    Args:
        path: Path to the JSON corrections file.

    Returns:
        List of FeedbackCorrection instances.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the JSON is malformed or missing required fields.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array in {path}")
    corrections: list[FeedbackCorrection] = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError(f"Each correction must be a JSON object, got: {type(item)}")
        corrections.append(FeedbackCorrection(
            kind=item["kind"],
            target_id=str(item["target_id"]),
            reason=item.get("reason", ""),
            applied_delta=item.get("applied_delta", ""),
        ))
    return corrections


def save_feedback_to_file(corrections: list[FeedbackCorrection], path: Path | str) -> None:
    """Serialise a list of FeedbackCorrection instances to JSON.

    Args:
        corrections: List to save.
        path: Output path. Parent directories are created if needed.
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    data = [
        {
            "kind": c.kind,
            "target_id": c.target_id,
            "reason": c.reason,
            "applied_delta": c.applied_delta,
        }
        for c in corrections
    ]
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
