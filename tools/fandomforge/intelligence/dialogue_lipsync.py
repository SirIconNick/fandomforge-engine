"""Dialogue lipsync scorer (Phase 6.3) — score how plausible each
candidate's mouth movement is for the spoken line.

True viseme alignment requires face-detection + mouth-ROI tracking
(deferred to Phase 8 ML hooks). Until then we ship a HEURISTIC scorer
that returns a confidence in [0, 1] based on:
  - Whisper transcript word-density at the candidate timestamp
    (proxy for "mouth is moving and speaking")
  - Visual_quality score from the source profile (proxy for "face is
    visible and well-lit enough to read mouth")
  - Static-shot penalty (motion_vector null + low action_intensity
    means the camera is off the speaker — penalize)

A composite 0-1 score lets dialogue_place reject candidates that score
< 0.4 ("implausible — no mouth movement detected" placeholder).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PLAUSIBILITY_FLOOR = 0.4  # Reject candidates below this composite


@dataclass
class LipsyncResult:
    candidate_index: int
    line_index: int
    plausibility: float
    reasons: list[str]
    word_density_score: float = 0.0
    visual_quality_score: float = 0.0
    static_shot_penalty: float = 0.0
    accepted: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_index": self.candidate_index,
            "line_index": self.line_index,
            "plausibility": round(self.plausibility, 3),
            "reasons": list(self.reasons),
            "word_density_score": round(self.word_density_score, 3),
            "visual_quality_score": round(self.visual_quality_score, 3),
            "static_shot_penalty": round(self.static_shot_penalty, 3),
            "accepted": self.accepted,
        }


def _word_density_score(transcript: dict[str, Any], start_sec: float, end_sec: float) -> float:
    """Words per second within the candidate's window. Normal speech is
    ~2-4 words/sec. Convert to 0-1 with a soft cap at 5 wps."""
    words = transcript.get("words") or []
    in_window = sum(
        1 for w in words
        if start_sec <= float(w.get("start_sec", 0)) < end_sec
    )
    duration = max(0.1, end_sec - start_sec)
    wps = in_window / duration
    return min(1.0, wps / 4.0)


def _visual_quality_score(scene_data: dict[str, Any] | None, source_timecode_sec: float) -> float:
    """Pull visual_quality from the scene catalog if available."""
    if not scene_data:
        return 0.5
    scenes = scene_data.get("scenes") or []
    for sc in scenes:
        if float(sc.get("start_sec", 0)) <= source_timecode_sec < float(sc.get("end_sec", 0)):
            vq = sc.get("visual_quality")
            if isinstance(vq, (int, float)):
                return min(1.0, float(vq) / 100.0)
    return 0.5


def _static_shot_penalty(scene_data: dict[str, Any] | None, source_timecode_sec: float) -> float:
    """If the scene has very low motion AND the candidate is in a fight
    compilation, we likely have an action shot with no speaker on camera.
    Returns 0 (no penalty) for moderate-motion scenes, up to 0.4 penalty
    for extremely static OR extremely chaotic shots."""
    if not scene_data:
        return 0.1
    scenes = scene_data.get("scenes") or []
    for sc in scenes:
        if float(sc.get("start_sec", 0)) <= source_timecode_sec < float(sc.get("end_sec", 0)):
            motion = float(sc.get("motion", 0.5))
            # Low motion (<0.05) = static shot, possibly a wide of a room
            # High motion (>0.7) = chase/fight, no time for dialogue
            if motion < 0.05:
                return 0.2
            if motion > 0.7:
                return 0.4
            return 0.0
    return 0.1


def score_candidate(
    candidate: dict[str, Any],
    *,
    transcript: dict[str, Any] | None = None,
    scene_data: dict[str, Any] | None = None,
) -> LipsyncResult:
    """Compute a plausibility score for a dialogue candidate.

    candidate is the dict produced by dialogue_search.search_for_line, with
    line_index, source_id, start_sec, end_sec, transcript_text fields.
    """
    line_idx = int(candidate.get("line_index", 0))
    cand_idx = int(candidate.get("candidate_index", 0))
    start = float(candidate.get("start_sec", 0))
    end = float(candidate.get("end_sec", start + 1.0))

    word_density = _word_density_score(transcript or {}, start, end)
    visual_quality = _visual_quality_score(scene_data, start)
    penalty = _static_shot_penalty(scene_data, start)

    # Composite: 50% word density (proxy for speaking) + 30% visual quality
    # (proxy for face visible) - penalty (no speaker on screen).
    composite = 0.5 * word_density + 0.3 * visual_quality + 0.2 * (1 - penalty)
    composite = max(0.0, min(1.0, composite))

    reasons: list[str] = []
    if word_density < 0.2:
        reasons.append("low word density — mouth likely not moving")
    if visual_quality < 0.4:
        reasons.append("low visual quality — face hard to read")
    if penalty > 0.3:
        reasons.append("scene motion suggests no speaker on camera")
    if not reasons and composite >= 0.7:
        reasons.append("plausible — words present, face visible, moderate motion")

    accepted = composite >= PLAUSIBILITY_FLOOR

    return LipsyncResult(
        candidate_index=cand_idx,
        line_index=line_idx,
        plausibility=composite,
        reasons=reasons,
        word_density_score=word_density,
        visual_quality_score=visual_quality,
        static_shot_penalty=penalty,
        accepted=accepted,
    )


def filter_accepted(
    candidates_per_line: dict[str, list[Any]],
    transcripts: dict[str, dict[str, Any]],
    scenes_by_source: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Walk every (line, candidate) pair, score, return only accepted."""
    out: dict[str, list[dict[str, Any]]] = {}
    for line_idx, cands in candidates_per_line.items():
        accepted: list[dict[str, Any]] = []
        for i, cand in enumerate(cands):
            cand_dict = cand.to_dict() if hasattr(cand, "to_dict") else dict(cand)
            cand_dict["candidate_index"] = i
            transcript = transcripts.get(cand_dict.get("source_id", ""))
            scene_data = scenes_by_source.get(cand_dict.get("source_id", ""))
            res = score_candidate(cand_dict, transcript=transcript, scene_data=scene_data)
            cand_dict["lipsync"] = res.to_dict()
            if res.accepted:
                accepted.append(cand_dict)
        out[line_idx] = accepted
    return out


__all__ = [
    "PLAUSIBILITY_FLOOR",
    "LipsyncResult",
    "score_candidate",
    "filter_accepted",
]
