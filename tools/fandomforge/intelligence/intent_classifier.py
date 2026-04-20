"""Intent classifier — first pipeline stage.

Reads a user prompt + project config + optional source/song hints, produces
the intent.schema.json artifact. Downstream every stage (edit-plan, arc
architect, slot-fit, dialogue script, color grade) keys off this output.

Heuristic-first design: keyword + regex with confidence scoring. LLM
upgrade hook is provided (set ANTHROPIC_API_KEY and the engine will
augment the heuristic with a structured-output classifier call). Without
the key the heuristic alone produces a usable intent.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

from fandomforge.intelligence.edit_classifier import (
    DEFAULT_TYPE,
    available_types,
    classify_edit_type,
)


# 8-dim tone vector order: [grief, triumph, fear, awe, tension, release, sorrow, elation]
TONE_DIMS = ("grief", "triumph", "fear", "awe", "tension", "release", "sorrow", "elation")

_TONE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "grief":   ("grief", "loss", "mourning", "death", "died", "killed", "funeral", "missing", "gone"),
    "triumph": ("triumph", "victory", "win", "champion", "rises", "rebirth", "reborn", "comeback", "earned"),
    "fear":    ("fear", "terror", "dread", "afraid", "horror", "nightmare", "trauma", "haunted"),
    "awe":     ("awe", "wonder", "epic", "majestic", "vast", "cosmic", "godlike", "legendary"),
    "tension": ("tension", "stakes", "pressure", "urgent", "frantic", "desperate", "edge", "crucible"),
    "release": ("release", "freedom", "calm", "rest", "peace", "exhale", "letting go", "resolution"),
    "sorrow":  ("sorrow", "sad", "tears", "weep", "broken", "lonely", "alone", "regret", "yearning"),
    "elation": ("elation", "joy", "ecstatic", "celebration", "party", "wild", "euphoric", "alive"),
}

_DURATION_PATTERNS = [
    # explicit seconds
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:s|sec|second|seconds)\b"), lambda v: float(v)),
    # explicit minutes
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:min|minute|minutes)\b"), lambda v: float(v) * 60),
    # ranges like "30-second"
    (re.compile(r"(\d+(?:\.\d+)?)[-\s]second"), lambda v: float(v)),
    (re.compile(r"(\d+(?:\.\d+)?)[-\s]minute"), lambda v: float(v) * 60),
    # "full song" or "full length" → defer to song duration
    (re.compile(r"\bfull\s+(song|length|track)\b"), lambda v: -1.0),  # sentinel
]

_TEMPLATE_TO_INTENT: dict[str, str] = {
    "action": "action",
    "emotional": "emotional",
    "tribute": "tribute",
    "shipping": "shipping",
    "speed_amv": "speed_amv",
    "cinematic": "cinematic",
    "comedy": "comedy",
    "hype_trailer": "hype_trailer",
}

# Vocabulary that maps to NEW v2 edit-types beyond the existing 8
_DIALOGUE_NARRATIVE_HINTS = (
    "monologue", "speech", "voiceover", "narrate", "narration",
    "speaks", "says", "tells", "answers", "asks", "stitched dialogue",
    "found footage storytelling", "character says", "delivers a line",
)
_DANCE_MOVEMENT_HINTS = (
    "dance", "fancam", "choreography", "choreo", "step", "moves",
    "groove", "movement", "rhythm match",
)
_SAD_HINTS = (
    "tribute to a loss", "in memory", "elegy", "lament", "tearjerker",
    "heartbreak", "weep", "mourn",
)


def _normalize_tone(raw: dict[str, float]) -> list[float]:
    """Normalize an 8-dim raw tone vector to [0, 1] preserving relative magnitudes."""
    vec = [float(raw.get(dim, 0.0)) for dim in TONE_DIMS]
    peak = max(vec) if vec else 0.0
    if peak <= 0:
        return [0.0] * 8
    return [round(v / peak, 3) for v in vec]


def _infer_tone_vector(text: str) -> list[float]:
    raw = {dim: 0.0 for dim in TONE_DIMS}
    if not text:
        return _normalize_tone(raw)
    lower = text.lower()
    for dim, needles in _TONE_KEYWORDS.items():
        for n in needles:
            if n in lower:
                raw[dim] += 1.0
    return _normalize_tone(raw)


def _parse_target_duration(text: str | None, song_duration_sec: float | None) -> tuple[float, str]:
    """Return (target_duration_sec, source_label)."""
    if text:
        lower = text.lower()
        for pattern, transform in _DURATION_PATTERNS:
            m = pattern.search(lower)
            if m:
                if m.lastindex:
                    raw = transform(m.group(1))
                else:
                    raw = transform(m.group(0))
                if raw < 0:
                    if song_duration_sec:
                        return float(song_duration_sec), "prompt"
                    return 60.0, "default"
                return raw, "prompt"
    if song_duration_sec:
        return float(song_duration_sec), "song"
    return 60.0, "default"


def _infer_speakers(
    text: str | None,
    *,
    fandom_roster: list[dict[str, Any]] | None = None,
    source_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Light heuristic: pick proper-noun-like tokens in the prompt that
    appear in the project's fandom roster, in known character names, or
    as source filenames. Records evidence for each."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not text:
        return out

    # Names heuristic — capitalized 1-3 word sequences not at sentence start
    candidates = re.findall(r"\b([A-Z][a-z'\-]+(?:\s+[A-Z][a-z'\-]+){0,2})\b", text)
    fandom_names = {f.get("name", "").lower() for f in (fandom_roster or [])}
    fandom_names.discard("")

    for cand in candidates:
        key = cand.lower()
        if key in seen:
            continue
        seen.add(key)
        evidence = "prompt_capitalized_token"
        role = "unknown"
        fandom = ""
        if key in fandom_names:
            evidence = "fandom_roster_match"
            fandom = cand
            role = "ensemble"
        elif source_ids:
            for sid in source_ids:
                if cand.lower().split()[0] in sid.lower():
                    evidence = f"source_filename_match:{sid}"
                    break
        out.append({
            "name": cand,
            "role": role,
            "fandom": fandom,
            "evidence": evidence,
        })
    return out


def _detect_extended_type(text: str | None) -> str | None:
    """Look for v2-only edit types that aren't in the legacy classifier."""
    if not text:
        return None
    lower = text.lower()
    if any(h in lower for h in _DIALOGUE_NARRATIVE_HINTS):
        return "dialogue_narrative"
    if any(h in lower for h in _DANCE_MOVEMENT_HINTS):
        return "dance_movement"
    if any(h in lower for h in _SAD_HINTS):
        return "sad_emotional"
    return None


def _confidence(
    edit_type: str,
    edit_type_source: str,
    tone_vector: list[float],
    speakers_count: int,
    duration_source: str,
) -> float:
    """Composite confidence score 0–1.

    Base 0.4 (intentionally below the 0.5 user-confirm threshold) so an
    empty prompt or pure default fallback naturally surfaces as low-conf.
    Real signals (explicit type, classified type, tone, duration) add up
    to push above 0.5 quickly.
    """
    score = 0.4  # base — below the 0.5 confirm threshold by design

    if edit_type_source == "explicit":
        score += 0.4
    elif edit_type_source == "classified":
        # Heuristic classifier gets partial credit
        score += 0.25
    # default → no boost

    # Strong tone signal increases confidence
    tone_max = max(tone_vector) if tone_vector else 0
    if tone_max >= 0.8:
        score += 0.10
    elif tone_max >= 0.5:
        score += 0.05

    # Named speakers help dialogue-narrative case
    if speakers_count > 0 and edit_type == "dialogue_narrative":
        score += 0.10

    if duration_source == "prompt":
        score += 0.05

    return round(min(1.0, max(0.0, score)), 3)


def classify_intent(
    prompt: str | None,
    *,
    project_config: dict[str, Any] | None = None,
    song_duration_sec: float | None = None,
    fandom_roster: list[dict[str, Any]] | None = None,
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build an intent dict from a user prompt and surrounding context.

    Args:
        prompt: free-text from the user describing the edit.
        project_config: optional project-config.json dict; explicit edit_type
            takes precedence over classification.
        song_duration_sec: if known, used as the duration default.
        fandom_roster: optional list from project_config.fandoms for speaker
            inference.
        source_ids: list of catalog/source ids — helps speaker inference.

    Returns:
        intent dict ready to validate against intent.schema.json.
    """
    text = prompt or ""

    # Edit type
    explicit_type = None
    if project_config and isinstance(project_config.get("edit_type"), str):
        candidate = project_config["edit_type"]
        # Accept it if it's in the legacy registry OR one of the v2 additions
        if candidate in available_types() or candidate in (
            "dialogue_narrative", "dance_movement", "sad_emotional"
        ):
            explicit_type = candidate

    if explicit_type:
        edit_type = explicit_type
        edit_type_source = "explicit"
    else:
        # Try v2-only first (more specific), then legacy classifier
        extended = _detect_extended_type(text)
        if extended:
            edit_type = extended
            edit_type_source = "classified"
        else:
            classified = classify_edit_type(text)
            if classified == DEFAULT_TYPE and not text:
                edit_type = DEFAULT_TYPE
                edit_type_source = "default"
            else:
                edit_type = classified
                edit_type_source = "classified"

    # Tone vector
    tone_vector = _infer_tone_vector(text)

    # Speakers
    speakers = _infer_speakers(
        text, fandom_roster=fandom_roster, source_ids=source_ids,
    )

    # Auto template
    auto_template = _TEMPLATE_TO_INTENT.get(edit_type, edit_type if edit_type in (
        "dialogue_narrative", "dance_movement", "sad_emotional",
    ) else "custom")

    # Target duration
    target_duration_sec, duration_source = _parse_target_duration(text, song_duration_sec)

    # Fandoms — pull from roster directly
    fandoms = [f.get("name", "") for f in (fandom_roster or []) if f.get("name")]

    confidence = _confidence(
        edit_type, edit_type_source, tone_vector, len(speakers), duration_source
    )
    needs_user_confirmation = confidence < 0.5

    return {
        "schema_version": 1,
        "prompt_text": text,
        "edit_type": edit_type,
        "edit_type_source": edit_type_source,
        "tone_vector": tone_vector,
        "speakers": speakers,
        "auto_template": auto_template,
        "target_duration_sec": round(target_duration_sec, 2),
        "duration_source": duration_source,
        "fandoms": fandoms,
        "confidence": confidence,
        "needs_user_confirmation": needs_user_confirmation,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "ff intent classifier (heuristic)",
    }


__all__ = [
    "TONE_DIMS",
    "classify_intent",
]
