"""Action SFX engine.

Picks punchy sound effects for action shots and aligns them to the nearest
beat/drop so the edit hits like a hammer instead of floating. Rotates through
variant packs so ten gunshots don't all sound like the same gunshot.

The engine emits an `sfx-plan.json` artifact. The mixer consumes the plan and
layers the SFX at the specified offsets under the main music bed. Scene audio
blend settings travel in the same plan — one place to tune how much of the
original clip audio bleeds through the song.

No ML — this is mood-tag heuristics + beat snapping + rotation.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from fandomforge.validation import validate

logger = logging.getLogger(__name__)


# Which shot cues fire which SFX kinds. Order matters — first match wins so
# more specific cues are listed first.
_CUE_TO_SFX: list[tuple[tuple[str, ...], str]] = [
    (("gunshot", "shoot", "shooting", "shot", "fire"), "gunshot"),
    (("cock", "reload", "rack"), "gun_cock"),
    (("punch", "jab", "hook", "fist"), "punch"),
    (("kick", "stomp", "roundhouse"), "kick"),
    (("sword", "blade", "clash", "stab"), "sword_clash"),
    (("glass", "shatter"), "glass_break"),
    (("explosion", "blast", "boom"), "explosion"),
    (("whoosh", "swoosh", "pass"), "whoosh"),
]


_DEFAULT_ACTION_TAGS = frozenset({
    "action", "combat", "fight", "violence", "chase", "badass",
    "intense", "battle", "fighting",
})


@dataclass
class SfxPack:
    """One rotation pack of SFX variants for a given kind."""

    kind: str
    variants: tuple[str, ...]

    def pick(self, index: int) -> str:
        if not self.variants:
            raise ValueError(f"SfxPack('{self.kind}') has no variants")
        return self.variants[index % len(self.variants)]


def default_sfx_library() -> dict[str, SfxPack]:
    """Built-in SFX variant rotation names.

    The engine doesn't ship audio — users drop .wav files into
    `~/.fandomforge/sfx/<kind>/` or the project's `sfx/` folder. The plan
    references files by name; the mixer resolves them at render time.
    Variants are hand-tuned so a sequence of gunshots doesn't repeat.
    """
    return {
        "gunshot": SfxPack("gunshot", (
            "pistol-suppressed-01.wav", "pistol-suppressed-02.wav",
            "rifle-short-01.wav", "rifle-short-02.wav",
            "shotgun-01.wav", "pistol-loud-01.wav",
        )),
        "gun_cock": SfxPack("gun_cock", (
            "pistol-rack-01.wav", "pistol-rack-02.wav", "shotgun-rack-01.wav",
        )),
        "punch": SfxPack("punch", (
            "punch-heavy-01.wav", "punch-heavy-02.wav", "punch-heavy-03.wav",
            "punch-light-01.wav", "bone-crack-01.wav",
        )),
        "kick": SfxPack("kick", (
            "kick-heavy-01.wav", "kick-heavy-02.wav", "kick-stomp-01.wav",
        )),
        "impact": SfxPack("impact", (
            "impact-heavy-01.wav", "impact-heavy-02.wav",
            "impact-thud-01.wav", "impact-metal-01.wav",
        )),
        "whoosh": SfxPack("whoosh", (
            "whoosh-fast-01.wav", "whoosh-fast-02.wav", "whoosh-low-01.wav",
        )),
        "sub_boom": SfxPack("sub_boom", (
            "sub-boom-01.wav", "sub-boom-02.wav",
        )),
        "riser": SfxPack("riser", ("riser-long-01.wav", "riser-short-01.wav")),
        "reverse_cymbal": SfxPack("reverse_cymbal", ("rev-cym-01.wav",)),
        "glass_break": SfxPack("glass_break", (
            "glass-break-01.wav", "glass-shatter-01.wav",
        )),
        "explosion": SfxPack("explosion", (
            "explosion-dry-01.wav", "explosion-wet-01.wav",
        )),
        "sword_clash": SfxPack("sword_clash", (
            "sword-clash-01.wav", "sword-clash-02.wav", "sword-draw-01.wav",
        )),
    }


def _shot_cues(shot: dict[str, Any]) -> list[str]:
    """Extract every matchable text cue from a shot (mood tags + description)."""
    cues: list[str] = []
    for tag in shot.get("mood_tags") or []:
        cues.append(str(tag).lower())
    desc = str(shot.get("description") or "").lower()
    if desc:
        cues.append(desc)
    role = str(shot.get("role") or "").lower()
    if role:
        cues.append(role)
    return cues


def _sfx_kinds_for_shot(shot: dict[str, Any]) -> list[str]:
    """Pick the SFX kinds this shot should trigger (0..n, in order of fit)."""
    cues = _shot_cues(shot)
    hits: list[str] = []
    seen: set[str] = set()
    for needles, kind in _CUE_TO_SFX:
        if kind in seen:
            continue
        for cue in cues:
            if any(n in cue for n in needles):
                hits.append(kind)
                seen.add(kind)
                break

    # Generic action fallback — big impact on obvious action shots.
    if not hits:
        mood_set = {str(t).lower() for t in (shot.get("mood_tags") or [])}
        role = str(shot.get("role") or "").lower()
        if mood_set & _DEFAULT_ACTION_TAGS or role in ("action", "cut-on-action", "motion"):
            hits.append("impact")
    return hits


def _snap_to_beat(time_sec: float, beats: Iterable[float], window_sec: float = 0.15) -> tuple[float, bool]:
    """Return (snapped_time, was_aligned). Snaps to the nearest beat within window."""
    best: float | None = None
    best_delta = window_sec
    for b in beats:
        d = abs(float(b) - time_sec)
        if d < best_delta:
            best_delta = d
            best = float(b)
    if best is not None:
        return best, True
    return time_sec, False


def build_sfx_plan(
    *,
    project_slug: str,
    shot_list: dict[str, Any],
    beat_map: dict[str, Any] | None = None,
    library: dict[str, SfxPack] | None = None,
    scene_audio_enabled: bool = True,
    scene_audio_gain_db: float = -20.0,
    scene_audio_duck_db: float = -8.0,
    scene_audio_duck_db_during_dialogue: float = -60.0,
    snap_window_sec: float = 0.15,
    seed: int | None = None,
) -> dict[str, Any]:
    """Build an sfx-plan dict from shots + beat map.

    Each shot that matches an action cue gets one or more SFX events. Events
    snap to the nearest beat when a beat map is provided and a beat is within
    `snap_window_sec`. Variant rotation uses a stable random draw per kind so
    sequential gunshots get different sound files.
    """
    lib = library or default_sfx_library()
    rng = random.Random(seed) if seed is not None else random.Random(0xFF)
    fps = float(shot_list.get("fps") or 24.0)
    beats = list(beat_map.get("beats") or []) if beat_map else []

    rotations: dict[str, int] = {}

    def next_variant(kind: str) -> str | None:
        pack = lib.get(kind)
        if not pack:
            return None
        # Random-seeded offset per kind, then rotate deterministically.
        if kind not in rotations:
            rotations[kind] = rng.randint(0, max(0, len(pack.variants) - 1))
        variant = pack.pick(rotations[kind])
        rotations[kind] += 1
        return variant

    events: list[dict[str, Any]] = []
    next_id = 1

    for shot in shot_list.get("shots") or []:
        kinds = _sfx_kinds_for_shot(shot)
        if not kinds:
            continue
        start_frame = float(shot.get("start_frame") or 0)
        start_sec = start_frame / fps if fps > 0 else 0.0

        # Gunshot / punch / kick / impact — fire at the shot start.
        primary_time, aligned = _snap_to_beat(start_sec, beats, snap_window_sec)
        primary_variant = next_variant(kinds[0])
        if primary_variant is not None:
            events.append({
                "id": f"sfx{next_id:03d}",
                "time_sec": round(primary_time, 3),
                "kind": kinds[0],
                "variant": primary_variant,
                "gain_db": 0.0,
                "source_shot_id": shot.get("id"),
                "beat_aligned": aligned,
                "reason": f"cue={shot.get('role')};mood={','.join(shot.get('mood_tags') or [])[:60]}",
            })
            next_id += 1

        # Secondary impact slightly after for compound hits (punch+impact etc.)
        if len(kinds) > 1:
            secondary_time = primary_time + 0.08
            secondary_variant = next_variant(kinds[1])
            if secondary_variant is not None:
                events.append({
                    "id": f"sfx{next_id:03d}",
                    "time_sec": round(secondary_time, 3),
                    "kind": kinds[1],
                    "variant": secondary_variant,
                    "gain_db": -2.0,
                    "source_shot_id": shot.get("id"),
                    "beat_aligned": False,
                    "reason": "compound-hit",
                })
                next_id += 1

    # Drop impacts — always drop a sub_boom / impact on every detected drop
    # to give it extra thud. Use a different kind from the shot SFX so they
    # layer rather than clash.
    if beat_map:
        for i, drop in enumerate(beat_map.get("drops") or []):
            drop_time = float(drop.get("time", 0.0))
            variant = next_variant("sub_boom")
            if variant is not None:
                events.append({
                    "id": f"sfx{next_id:03d}",
                    "time_sec": round(drop_time, 3),
                    "kind": "sub_boom",
                    "variant": variant,
                    "gain_db": -1.0,
                    "beat_aligned": True,
                    "reason": f"drop_{i + 1}",
                })
                next_id += 1

    events.sort(key=lambda e: e["time_sec"])

    plan: dict[str, Any] = {
        "schema_version": 1,
        "project_slug": project_slug,
        "scene_audio_blend": {
            "enabled": bool(scene_audio_enabled),
            "gain_db": float(scene_audio_gain_db),
            "duck_to_song_db": float(scene_audio_duck_db),
            "duck_db_during_dialogue": float(scene_audio_duck_db_during_dialogue),
        },
        "events": events,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "ff sfx plan",
    }
    validate(plan, "sfx-plan")
    return plan


def write_sfx_plan(plan: dict[str, Any], project_dir: Path) -> Path:
    out = project_dir / "data" / "sfx-plan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return out


def resolve_sfx_file(variant_name: str, kind: str, project_dir: Path) -> Path | None:
    """Locate the audio file for a given SFX variant.

    Lookup order:
      1. <project>/sfx/<kind>/<variant>
      2. ~/.fandomforge/sfx/<kind>/<variant>
      3. <project>/sfx/<variant>      (flat layout)

    Returns None when the file isn't found — the mixer then warns and skips
    the event rather than blowing up the render.
    """
    candidates = [
        project_dir / "sfx" / kind / variant_name,
        Path.home() / ".fandomforge" / "sfx" / kind / variant_name,
        project_dir / "sfx" / variant_name,
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


__all__ = [
    "SfxPack",
    "build_sfx_plan",
    "default_sfx_library",
    "resolve_sfx_file",
    "write_sfx_plan",
]
