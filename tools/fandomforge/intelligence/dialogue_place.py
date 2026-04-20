"""Dialogue place stage (Phase 6.4) — assign lipsync-approved candidates
to SAFE windows on the beat map.

Per amendment A4 negotiate-down rule: when SAFE windows < line count,
the function refuses to over-pack and returns N (line count) -
overflow as REJECTED with a suggestion to drop or shorten lines.

Output is a placement plan compatible with assembly/mixer.py's
DialogueCue list — line_index → (audio source_id + start_sec on song
timeline) so the orchestrator can build the dialogue track.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LinePlacement:
    line_index: int
    line_text: str
    decision: str  # PLACE | SHIFT | REJECT
    chosen_candidate: dict[str, Any] | None = None
    placed_song_time_sec: float = 0.0
    safe_window_used: dict[str, Any] | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = {
            "line_index": self.line_index,
            "line_text": self.line_text,
            "decision": self.decision,
            "placed_song_time_sec": round(self.placed_song_time_sec, 3),
            "reason": self.reason,
        }
        if self.chosen_candidate:
            out["chosen_candidate"] = {
                "source_id": self.chosen_candidate.get("source_id"),
                "start_sec": self.chosen_candidate.get("start_sec"),
                "end_sec": self.chosen_candidate.get("end_sec"),
                "transcript_text": self.chosen_candidate.get("transcript_text"),
                "composite_score": self.chosen_candidate.get("composite_score"),
                "lipsync_plausibility": (self.chosen_candidate.get("lipsync") or {}).get("plausibility"),
            }
        if self.safe_window_used:
            out["safe_window"] = {
                "start_sec": self.safe_window_used.get("start_sec"),
                "end_sec": self.safe_window_used.get("end_sec"),
                "min_duration_available_sec": self.safe_window_used.get("min_duration_available_sec"),
            }
        return out


def _safe_windows_with_room(windows: list[dict[str, Any]], min_duration_sec: float) -> list[dict[str, Any]]:
    """Return windows with flag=SAFE and min_duration_available_sec >= required."""
    return [
        w for w in windows
        if w.get("flag") == "SAFE"
        and float(w.get("min_duration_available_sec", 0)) >= min_duration_sec
    ]


def assign_lines_to_windows(
    script: dict[str, Any],
    candidates_per_line: dict[str, list[dict[str, Any]]],
    dialogue_windows: dict[str, Any],
) -> list[LinePlacement]:
    """Walk the script in order, assign each line to the next available
    SAFE window with enough room. Per amendment A4 — if there aren't enough
    SAFE windows, REJECT the overflow lines rather than over-pack."""
    windows = dialogue_windows.get("windows") or []
    placements: list[LinePlacement] = []
    cursor_t = 0.0  # Song time we've consumed up to
    used_windows: set[float] = set()  # window starts we've already placed into

    for line in script.get("lines") or []:
        line_idx = int(line.get("index", 0))
        line_text = str(line.get("text", ""))
        target_dur_sec = float(line.get("target_duration_ms", 1500)) / 1000.0
        cands = candidates_per_line.get(str(line_idx)) or []

        # Pick the highest-scored candidate (already sorted)
        best_cand = cands[0] if cands else None
        if not best_cand:
            placements.append(LinePlacement(
                line_index=line_idx, line_text=line_text,
                decision="REJECT",
                reason="no candidate snippet found in any transcript",
            ))
            continue

        # Find the next SAFE window with room, after cursor_t
        room_needed = max(target_dur_sec, float(best_cand.get("end_sec", 0)) - float(best_cand.get("start_sec", 0)))
        forward = [
            w for w in _safe_windows_with_room(windows, room_needed)
            if float(w.get("start_sec", 0)) >= cursor_t
            and float(w.get("start_sec", 0)) not in used_windows
        ]

        if not forward:
            placements.append(LinePlacement(
                line_index=line_idx, line_text=line_text,
                chosen_candidate=best_cand,
                decision="REJECT",
                reason=f"no SAFE window of ≥{room_needed:.2f}s remaining after t={cursor_t:.2f}s",
            ))
            continue

        chosen_window = forward[0]
        win_start = float(chosen_window.get("start_sec", 0))
        used_windows.add(win_start)
        cursor_t = win_start + room_needed
        placements.append(LinePlacement(
            line_index=line_idx, line_text=line_text,
            chosen_candidate=best_cand,
            decision="PLACE",
            placed_song_time_sec=win_start,
            safe_window_used=chosen_window,
            reason=f"placed in SAFE window @{win_start:.2f}s ({chosen_window.get('reason_codes', [''])[0]})",
        ))

    return placements


def build_mixer_cues(placements: list[LinePlacement]) -> list[dict[str, Any]]:
    """Convert PLACE'd placements to dialogue.json cue format the mixer
    consumes. Each cue points at a source clip + start time on the song."""
    cues: list[dict[str, Any]] = []
    for p in placements:
        if p.decision != "PLACE" or not p.chosen_candidate:
            continue
        cand = p.chosen_candidate
        # Mixer expects audio path + start time on the song timeline. The
        # actual audio extraction (source clip → wav file) is deferred to
        # the orchestrator's dialogue prep pass.
        cues.append({
            "audio": f"{cand.get('source_id')}_{int(cand.get('start_sec', 0)*1000)}.wav",
            "start": p.placed_song_time_sec,
            "duration": float(cand.get("end_sec", 0)) - float(cand.get("start_sec", 0)),
            "gain_db": -3.0,
            "duck_db": -10.0,
            "character": p.chosen_candidate.get("source_id", ""),
            "line": cand.get("transcript_text", ""),
            "source": cand.get("source_id", ""),
            "source_start_sec": float(cand.get("start_sec", 0)),
        })
    return cues


__all__ = [
    "LinePlacement",
    "assign_lines_to_windows",
    "build_mixer_cues",
]
