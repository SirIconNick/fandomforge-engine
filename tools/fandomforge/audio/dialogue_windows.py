"""Dialogue window detection — find moments where spoken audio will land cleanly.

A "dialogue window" is a span of the song where injected dialogue (a character
saying something) won't fight the music. Real fan editors learn this by ear:
post-drop silence, low-mid-density valleys, gaps between vocal lines. This
module makes that judgment programmatic.

For every 250ms slice we compute a flag:
  SAFE     — dialogue lands clean here
  RISKY    — placeable but the music is non-trivial competition
  BLOCKED  — dialogue placed here will be lost or unintelligible

A placement plan is then produced for a list of dialogue cues, mapping each
cue to its best SAFE window or surfacing why it can't.

Reads `energy-zones.json` (output of Phase 1.1) and `beat-map.json`. No new
audio analysis — pure inference from existing intel.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

# Threshold rules. Conservative — better to flag RISKY and let the editor
# decide than silently block.
RMS_FLOOR_FOR_SAFE = 0.35      # bands.mid + bands.bass below this energy = SAFE-eligible
RMS_FLOOR_FOR_RISKY = 0.55     # above this = BLOCKED (no chance dialogue cuts through)
MID_BAND_DENSITY_BLOCK = 0.65  # dense mid frequencies (vocals + lead synths) block dialogue
BEAT_PROXIMITY_RISKY_SEC = 0.12  # dialogue start within ±120ms of a beat = RISKY
DOWNBEAT_PROXIMITY_BLOCK_SEC = 0.10  # within ±100ms of a downbeat = BLOCKED
POST_DROP_SILENCE_SAFE_SEC = 0.8  # 0–800ms after a drop is the canonical SAFE window
INSTRUMENTAL_VALLEY_SAFE_BAND_SUM = 0.45  # bass+mid+treble below this = SAFE


@dataclass
class DialogueWindow:
    """A 250ms (or longer if merged) slice of timeline classified for
    dialogue placement."""
    start_sec: float
    end_sec: float
    flag: str  # SAFE | RISKY | BLOCKED
    reason_codes: list[str] = field(default_factory=list)
    min_duration_available_sec: float = 0.0  # How long this window can fit a cue
    rms_at_start: float = 0.0
    mid_density_at_start: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DialogueWindowsResult:
    """Full window-level classification for a song."""
    schema_version: int
    duration_sec: float
    resolution_sec: float
    windows: list[DialogueWindow] = field(default_factory=list)
    safe_window_count: int = 0
    risky_window_count: int = 0
    blocked_window_count: int = 0
    generator: str = "ff dialogue windows"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "duration_sec": self.duration_sec,
            "resolution_sec": self.resolution_sec,
            "safe_window_count": self.safe_window_count,
            "risky_window_count": self.risky_window_count,
            "blocked_window_count": self.blocked_window_count,
            "windows": [w.to_dict() for w in self.windows],
            "generator": self.generator,
        }


def _bands_at_index(bands: list[dict[str, Any]], idx: int) -> dict[str, float]:
    if 0 <= idx < len(bands):
        b = bands[idx]
        return {
            "bass": float(b.get("bass", 0)),
            "mid": float(b.get("mid", 0)),
            "treble": float(b.get("treble", 0)),
        }
    return {"bass": 0.0, "mid": 0.0, "treble": 0.0}


def _zone_at(zones: list[dict[str, Any]], time_sec: float) -> dict[str, Any] | None:
    for z in zones:
        if float(z.get("start_sec", 0)) <= time_sec < float(z.get("end_sec", 0)):
            return z
    return None


def _nearest_beat_distance(beats: list[float], t: float) -> float:
    if not beats:
        return 999.0
    # Linear scan is fine for the scales we operate at.
    return min(abs(b - t) for b in beats)


def _within_post_drop_window(drops: list[dict[str, Any]], t: float) -> bool:
    """True if `t` is within 0–POST_DROP_SILENCE_SAFE_SEC after any drop."""
    for d in drops:
        dt = t - float(d.get("time", -1))
        if 0.0 <= dt <= POST_DROP_SILENCE_SAFE_SEC:
            return True
    return False


def classify_windows(
    energy_zones: dict[str, Any],
    beat_map: dict[str, Any] | None = None,
) -> DialogueWindowsResult:
    """Walk the band timeline and classify each 250ms window.

    Args:
        energy_zones: parsed energy-zones.json
        beat_map: parsed beat-map.json (optional but recommended — beat / drop
            data refines RISKY and SAFE classification considerably)

    Returns:
        DialogueWindowsResult with one window per band sample.
    """
    bands = energy_zones.get("bands") or []
    zones = energy_zones.get("zones") or []
    res_sec = float(energy_zones.get("resolution_sec", 0.25))
    duration_sec = float(energy_zones.get("duration_sec", 0.0))

    beats: list[float] = []
    downbeats: list[float] = []
    drops: list[dict[str, Any]] = []
    if beat_map:
        beats = [float(b) for b in (beat_map.get("beats") or []) if isinstance(b, (int, float))]
        downbeats = [float(b) for b in (beat_map.get("downbeats") or []) if isinstance(b, (int, float))]
        drops = beat_map.get("drops") or []

    windows: list[DialogueWindow] = []
    for i, b in enumerate(bands):
        t = float(b.get("time_sec", 0))
        bass = float(b.get("bass", 0))
        mid = float(b.get("mid", 0))
        treble = float(b.get("treble", 0))
        band_sum = bass + mid + treble
        # Approximate RMS at this point: average of bands (coarse but
        # consistent — they're all peak-normalized to 1.0)
        rms = (bass + mid + treble) / 3.0

        z = _zone_at(zones, t)
        zone_label = z.get("label", "mid") if z else "mid"

        flag = "RISKY"
        reasons: list[str] = []

        # ---- SAFE conditions (any one of these promotes from RISKY → SAFE) ----
        if _within_post_drop_window(drops, t):
            flag = "SAFE"
            reasons.append("post_drop_window")
        elif zone_label in ("low", "breakdown") and rms < RMS_FLOOR_FOR_SAFE:
            flag = "SAFE"
            reasons.append("low_energy_zone")
        elif band_sum < INSTRUMENTAL_VALLEY_SAFE_BAND_SUM and mid < MID_BAND_DENSITY_BLOCK:
            flag = "SAFE"
            reasons.append("instrumental_valley")

        # ---- BLOCKED conditions (override SAFE) ----
        if rms >= RMS_FLOOR_FOR_RISKY and zone_label in ("high", "drop"):
            flag = "BLOCKED"
            reasons.append("high_energy_zone")
        if mid >= MID_BAND_DENSITY_BLOCK and zone_label != "low":
            # dense mid-frequency content (lead vocals, lead synths, sax) will
            # mask spoken voice frequency-wise no matter the overall RMS.
            if flag != "BLOCKED":
                reasons.append("dense_mid_frequencies")
            flag = "BLOCKED"
        if downbeats and _nearest_beat_distance(downbeats, t) < DOWNBEAT_PROXIMITY_BLOCK_SEC:
            if flag == "SAFE":
                flag = "RISKY"
                reasons.append("downbeat_proximity")
            elif flag != "BLOCKED":
                reasons.append("downbeat_proximity")

        # Risky escalation: SAFE → RISKY if a beat hits within ±120ms
        if flag == "SAFE" and beats:
            if _nearest_beat_distance(beats, t) < BEAT_PROXIMITY_RISKY_SEC:
                flag = "RISKY"
                reasons.append("beat_proximity")

        if not reasons:
            reasons.append("default_risky" if flag == "RISKY" else flag.lower())

        windows.append(DialogueWindow(
            start_sec=round(t, 3),
            end_sec=round(t + res_sec, 3),
            flag=flag,
            reason_codes=reasons,
            min_duration_available_sec=res_sec,
            rms_at_start=round(rms, 3),
            mid_density_at_start=round(mid, 3),
        ))

    # Compute min_duration_available — for each SAFE window, walk forward
    # while subsequent windows are SAFE; that's the longest cue we can fit.
    n = len(windows)
    for i, w in enumerate(windows):
        if w.flag != "SAFE":
            continue
        run = res_sec
        j = i + 1
        while j < n and windows[j].flag == "SAFE":
            run += res_sec
            j += 1
        w.min_duration_available_sec = round(run, 3)

    safe = sum(1 for w in windows if w.flag == "SAFE")
    risky = sum(1 for w in windows if w.flag == "RISKY")
    blocked = sum(1 for w in windows if w.flag == "BLOCKED")

    return DialogueWindowsResult(
        schema_version=1,
        duration_sec=round(duration_sec, 3),
        resolution_sec=res_sec,
        windows=windows,
        safe_window_count=safe,
        risky_window_count=risky,
        blocked_window_count=blocked,
    )


@dataclass
class CuePlacement:
    """How an individual dialogue cue resolved against the windows."""
    cue_index: int
    requested_start_sec: float
    cue_duration_sec: float
    placed_start_sec: float
    decision: str  # PLACE | SHIFT | REJECT
    flag_at_placement: str  # SAFE | RISKY | BLOCKED
    reason: str
    suggested_alternative_sec: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_placement(
    windows: list[DialogueWindow],
    requested_start_sec: float,
    cue_duration_sec: float,
    *,
    cue_index: int = 0,
    allow_shift_sec: float = 1.5,
) -> CuePlacement:
    """Decide whether a single dialogue cue can land at requested_start_sec.

    Logic:
      1. Look at the window covering requested_start_sec.
      2. If SAFE and the SAFE run is long enough → PLACE.
      3. If RISKY but no SAFE alternative within allow_shift_sec → PLACE
         with flag_at_placement=RISKY (the editor knows it's a compromise).
      4. If BLOCKED or SAFE-but-too-short → look ±allow_shift_sec for a SAFE
         window long enough; if found → SHIFT.
      5. Otherwise → REJECT with a suggested_alternative_sec pointing at the
         nearest long SAFE window in the whole song (may be far away).
    """
    if not windows:
        return CuePlacement(
            cue_index=cue_index,
            requested_start_sec=requested_start_sec,
            cue_duration_sec=cue_duration_sec,
            placed_start_sec=requested_start_sec,
            decision="PLACE",
            flag_at_placement="RISKY",
            reason="no_window_data",
        )

    res_sec = windows[0].end_sec - windows[0].start_sec or 0.25

    def _window_at(t: float) -> DialogueWindow | None:
        for w in windows:
            if w.start_sec <= t < w.end_sec:
                return w
        return None

    def _safe_run_at(t: float) -> float:
        w = _window_at(t)
        if w is None or w.flag != "SAFE":
            return 0.0
        return w.min_duration_available_sec

    def _nearest_safe_with_room(target: float, want_dur: float, search_radius: float) -> float | None:
        """Walk outward in resolution-sized steps for the closest SAFE window
        whose run is >= want_dur."""
        steps = int(search_radius / res_sec) + 1
        for offset_steps in range(0, steps + 1):
            for sign in (1, -1):
                t = target + sign * offset_steps * res_sec
                if t < 0:
                    continue
                w = _window_at(t)
                if w and w.flag == "SAFE" and w.min_duration_available_sec >= want_dur:
                    return round(t, 3)
        return None

    direct = _window_at(requested_start_sec)
    if direct is None:
        return CuePlacement(
            cue_index=cue_index,
            requested_start_sec=requested_start_sec,
            cue_duration_sec=cue_duration_sec,
            placed_start_sec=requested_start_sec,
            decision="PLACE",
            flag_at_placement="RISKY",
            reason="outside_window_grid",
        )

    direct_run = _safe_run_at(requested_start_sec)

    if direct.flag == "SAFE" and direct_run >= cue_duration_sec:
        return CuePlacement(
            cue_index=cue_index,
            requested_start_sec=requested_start_sec,
            cue_duration_sec=cue_duration_sec,
            placed_start_sec=requested_start_sec,
            decision="PLACE",
            flag_at_placement="SAFE",
            reason=";".join(direct.reason_codes) or "safe",
        )

    # Try to shift inside allow_shift_sec
    alt = _nearest_safe_with_room(requested_start_sec, cue_duration_sec, allow_shift_sec)
    if alt is not None and abs(alt - requested_start_sec) > 1e-3:
        return CuePlacement(
            cue_index=cue_index,
            requested_start_sec=requested_start_sec,
            cue_duration_sec=cue_duration_sec,
            placed_start_sec=alt,
            decision="SHIFT",
            flag_at_placement="SAFE",
            reason=f"shifted_to_safe_window (delta={alt - requested_start_sec:+.2f}s)",
            suggested_alternative_sec=alt,
        )

    if direct.flag == "RISKY":
        return CuePlacement(
            cue_index=cue_index,
            requested_start_sec=requested_start_sec,
            cue_duration_sec=cue_duration_sec,
            placed_start_sec=requested_start_sec,
            decision="PLACE",
            flag_at_placement="RISKY",
            reason=";".join(direct.reason_codes) or "risky_acceptable",
        )

    # Direct is BLOCKED and shift didn't find anything within radius — search
    # the whole timeline for a long SAFE window
    long_alt = _nearest_safe_with_room(requested_start_sec, cue_duration_sec, search_radius=120.0)
    return CuePlacement(
        cue_index=cue_index,
        requested_start_sec=requested_start_sec,
        cue_duration_sec=cue_duration_sec,
        placed_start_sec=requested_start_sec,
        decision="REJECT",
        flag_at_placement="BLOCKED",
        reason=";".join(direct.reason_codes) or "blocked",
        suggested_alternative_sec=long_alt,
    )


def build_placement_plan(
    cues: list[dict[str, Any]],
    windows: list[DialogueWindow],
    *,
    allow_shift_sec: float = 1.5,
) -> list[CuePlacement]:
    """Apply evaluate_placement to a list of dialogue cues.

    Each cue is expected to look like the dialogue.json entries:
        {"start": float, "duration": float, ...}
    Or alternatively start_sec / cue_duration_sec.
    """
    out: list[CuePlacement] = []
    for i, cue in enumerate(cues):
        start = float(cue.get("start", cue.get("start_sec", 0)))
        dur = float(cue.get("duration", cue.get("cue_duration_sec", 1.0)))
        out.append(evaluate_placement(
            windows, start, dur, cue_index=i, allow_shift_sec=allow_shift_sec,
        ))
    return out


def write_dialogue_windows(result: DialogueWindowsResult, out_path: Path) -> Path:
    import json
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result.to_dict(), indent=2))
    return out_path


def write_placement_plan(
    placements: list[CuePlacement],
    out_path: Path,
    *,
    project_slug: str = "",
) -> Path:
    import json
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "project_slug": project_slug,
        "placements": [p.to_dict() for p in placements],
        "summary": {
            "place": sum(1 for p in placements if p.decision == "PLACE"),
            "shift": sum(1 for p in placements if p.decision == "SHIFT"),
            "reject": sum(1 for p in placements if p.decision == "REJECT"),
        },
        "generator": "ff dialogue windows",
    }
    out_path.write_text(json.dumps(payload, indent=2))
    return out_path
