"""Quality-gap mitigation (Phase 3.2).

Per-source quality_tier (S/A/B/C/D from source profiler) drives ffmpeg
filter recommendations the orchestrator can append before color grade:
  S/A → no-op (clean digital, leave alone)
  B   → light denoise (hqdn3d=2:1:2:3)
  C   → denoise + light unsharp (hqdn3d=3:2:3:3, unsharp=5:5:0.5)
  D   → flag for review; if forced-allow, denoise+sharpen aggressive

Per amendment open-decision-2: D-tier default REFUSE. The
allow_dtier flag must be explicitly set to include D-tier shots.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


TIER_FILTERS: dict[str, str] = {
    "S": "",
    "A": "",
    "B": "hqdn3d=2:1:2:3",
    "C": "hqdn3d=3:2:3:3,unsharp=5:5:0.5",
    "D": "hqdn3d=4:3:6:6,unsharp=5:5:1.0",
}


@dataclass
class TierTreatment:
    source_id: str
    quality_tier: str
    ffmpeg_filter: str
    flagged_for_review: bool
    refused: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "quality_tier": self.quality_tier,
            "ffmpeg_filter": self.ffmpeg_filter,
            "flagged_for_review": self.flagged_for_review,
            "refused": self.refused,
            "reason": self.reason,
        }


def treat_source(
    profile: dict[str, Any],
    *,
    allow_dtier: bool = False,
) -> TierTreatment:
    sid = str(profile.get("source_id", ""))
    tier = str(profile.get("quality_tier", "C"))
    flagged = tier in ("C", "D")
    refused = (tier == "D" and not allow_dtier)
    reason = ""
    if refused:
        reason = "D-tier source refused by default (pass --allow-dtier to include)"
    elif tier == "D":
        reason = "D-tier — aggressive denoise + sharpen applied; flag for manual review"
    elif tier == "C":
        reason = "C-tier — denoise + light unsharp"
    elif tier == "B":
        reason = "B-tier — light denoise"
    else:
        reason = f"{tier}-tier — clean, no treatment"
    return TierTreatment(
        source_id=sid,
        quality_tier=tier,
        ffmpeg_filter=TIER_FILTERS.get(tier, ""),
        flagged_for_review=flagged,
        refused=refused,
        reason=reason,
    )


def treat_all(
    profiles: list[dict[str, Any]],
    *,
    allow_dtier: bool = False,
) -> list[dict[str, Any]]:
    return [treat_source(p, allow_dtier=allow_dtier).to_dict() for p in profiles]


def quality_distribution(profiles: list[dict[str, Any]]) -> dict[str, int]:
    out = {"S": 0, "A": 0, "B": 0, "C": 0, "D": 0}
    for p in profiles:
        tier = str(p.get("quality_tier", "C"))
        if tier in out:
            out[tier] += 1
    return out


__all__ = ["TIER_FILTERS", "TierTreatment", "treat_source", "treat_all",
           "quality_distribution"]
