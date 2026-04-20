"""Aspect ratio arbiter (Phase 3.1).

For each shot, decide how to fit the source's native AR into the target
output AR without destroying critical content. Five output decisions:
  none        — source AR matches target, no-op
  pillarbox   — source narrower (4:3 in 16:9), pad sides with black
  letterbox   — source wider (2.39:1 in 16:9), pad top/bottom with black
  scale       — non-destructive resize (pixel ARs differ but display same)
  crop        — discard side margins to fill (forced; loses content)
  smart_crop  — crop respecting subject safe-zone (face/center-of-action)

Cross-type by design (A3): no per-edit-type branches. The decision
rests on source AR vs target AR + content sensitivity (does cropping
risk losing faces? then prefer pillar/letter; if it's a wide landscape
without faces, smart_crop is fine).

Phase 3.1 ships heuristic-only: face/action safe-zone detection is
deferred to Phase 8 ML hooks. Until then the safe zone defaults to a
center-weighted 80% rect.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# How big the source/target ratio difference must be before we pillar/letter
# instead of treating it as a no-op.
AR_TOLERANCE = 0.02


def parse_ar(s: str) -> float:
    """'16:9' → 1.778, '4:3' → 1.333. Returns 0.0 on parse error."""
    if not s or ":" not in s:
        return 0.0
    a, b = s.split(":", 1)
    try:
        a_f = float(a)
        b_f = float(b)
        if b_f <= 0:
            return 0.0
        return a_f / b_f
    except ValueError:
        return 0.0


def _safe_zone_from_profile(source_profile: dict[str, Any] | None) -> dict[str, float]:
    """Default safe-zone rect — center-weighted 80%. Phase 8 ML upgrade
    will replace this with face/action-detection from the source profile."""
    return {"x": 0.10, "y": 0.10, "w": 0.80, "h": 0.80}


def _decide_for_pair(source_ar: float, target_ar: float) -> str:
    if source_ar <= 0 or target_ar <= 0:
        return "none"
    diff = abs(source_ar - target_ar) / target_ar
    if diff <= AR_TOLERANCE:
        return "none"
    if source_ar < target_ar:
        # Source narrower → pillarbox is the safe default
        return "pillarbox"
    # Source wider → letterbox is the safe default
    return "letterbox"


def _ffmpeg_filter_for(decision: str, source_ar: float, target_ar: float) -> str:
    """Build the ffmpeg -vf chain for the decision. Caller passes target
    width/height into the orchestrator's render command as needed."""
    if decision == "none":
        return ""
    if decision == "pillarbox":
        # Pad horizontal margins with black to reach target AR
        return "scale=iw*sar:ih,pad='ih*(16/9)':ih:'(ih*(16/9)-iw)/2':0:black"
    if decision == "letterbox":
        return "scale=iw*sar:ih,pad=iw:'iw*9/16':0:'(iw*9/16-ih)/2':black"
    if decision == "crop":
        # Center-crop to target
        return "crop='if(gt(iw/ih,16/9),ih*16/9,iw)':'if(gt(iw/ih,16/9),ih,iw*9/16)'"
    if decision == "scale":
        return "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black"
    if decision == "smart_crop":
        # Same as crop until ML safe-zone tracking ships
        return "crop='if(gt(iw/ih,16/9),ih*16/9,iw)':'if(gt(iw/ih,16/9),ih,iw*9/16)'"
    return ""


@dataclass
class AspectDecision:
    shot_id: str
    source_id: str
    source_ar: str
    decision: str
    safe_zone: dict[str, float] = field(default_factory=lambda: {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8})
    transition_from_prev: str = "hard"
    reason: str = ""
    ffmpeg_filter: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = {
            "shot_id": self.shot_id,
            "source_id": self.source_id,
            "source_ar": self.source_ar,
            "decision": self.decision,
        }
        if self.safe_zone:
            out["safe_zone"] = self.safe_zone
        if self.transition_from_prev:
            out["transition_from_prev"] = self.transition_from_prev
        if self.reason:
            out["reason"] = self.reason
        if self.ffmpeg_filter:
            out["ffmpeg_filter"] = self.ffmpeg_filter
        return out


def build_aspect_plan(
    shot_list: dict[str, Any],
    *,
    target_ar: str = "16:9",
    source_profiles: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the per-shot aspect plan from a shot list + source profiles."""
    target_ar_value = parse_ar(target_ar)
    decisions: list[AspectDecision] = []
    prev_decision: str | None = None
    counts = {
        "no_op_count": 0, "pillarbox_count": 0, "letterbox_count": 0,
        "crop_count": 0, "scale_count": 0, "ar_change_count": 0,
    }

    for shot in shot_list.get("shots") or []:
        shot_id = str(shot.get("id", ""))
        source_id = str(shot.get("source_id", ""))
        profile = (source_profiles or {}).get(source_id) or {}
        source_ar_str = str(profile.get("aspect_ratio_native") or target_ar)
        source_ar_value = parse_ar(source_ar_str)
        decision = _decide_for_pair(source_ar_value, target_ar_value)

        # AR change tracking
        if prev_decision is not None and decision != prev_decision and decision != "none" and prev_decision != "none":
            counts["ar_change_count"] += 1
            transition = "smooth"
        else:
            transition = "hard"

        if decision == "none":
            counts["no_op_count"] += 1
        elif decision == "pillarbox":
            counts["pillarbox_count"] += 1
        elif decision == "letterbox":
            counts["letterbox_count"] += 1
        elif decision == "crop" or decision == "smart_crop":
            counts["crop_count"] += 1
        elif decision == "scale":
            counts["scale_count"] += 1

        reason = ""
        if decision == "pillarbox":
            reason = f"source AR {source_ar_str} narrower than target {target_ar}"
        elif decision == "letterbox":
            reason = f"source AR {source_ar_str} wider than target {target_ar}"
        elif decision == "none":
            reason = "matches target"

        decisions.append(AspectDecision(
            shot_id=shot_id,
            source_id=source_id,
            source_ar=source_ar_str,
            decision=decision,
            safe_zone=_safe_zone_from_profile(profile),
            transition_from_prev=transition,
            reason=reason,
            ffmpeg_filter=_ffmpeg_filter_for(decision, source_ar_value, target_ar_value),
        ))
        prev_decision = decision

    return {
        "schema_version": 1,
        "project_slug": str(shot_list.get("project_slug", "")),
        "target_ar": target_ar,
        "decisions": [d.to_dict() for d in decisions],
        "summary": counts,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "ff aspect arbiter (Phase 3.1)",
    }


def load_source_profiles(project_dir: Path) -> dict[str, dict[str, Any]]:
    """Load all source profiles for a project keyed by source_id."""
    profiles: dict[str, dict[str, Any]] = {}
    profiles_dir = project_dir / "data" / "source-profiles"
    if not profiles_dir.exists():
        return profiles
    for p in profiles_dir.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            sid = data.get("source_id")
            if sid:
                profiles[sid] = data
        except (json.JSONDecodeError, OSError):
            continue
    return profiles


def write_aspect_plan(plan: dict[str, Any], out_path: Path) -> Path:
    from fandomforge.validation import validate_and_write
    out_path.parent.mkdir(parents=True, exist_ok=True)
    validate_and_write(plan, "aspect-plan", out_path)
    return out_path


__all__ = [
    "AR_TOLERANCE",
    "AspectDecision",
    "build_aspect_plan",
    "load_source_profiles",
    "parse_ar",
    "write_aspect_plan",
]
