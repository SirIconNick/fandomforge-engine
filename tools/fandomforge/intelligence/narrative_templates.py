"""Narrative arc templates for FandomForge shot planning.

Each template describes a sequence of StorySlot instances that map story beats
to positions in the edit timeline. Templates are pure data -- they carry no
song-specific timing. The shot_optimizer module takes a template and maps its
slots onto actual song timestamps.

All dataclasses are JSON-serialisable via dataclasses.asdict().
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal


# ---------------------------------------------------------------------------
# Vocabulary types
# ---------------------------------------------------------------------------

MoodProfile = Literal["calm", "tense", "peak", "breather"]


# ---------------------------------------------------------------------------
# Core dataclass: a single story slot
# ---------------------------------------------------------------------------

@dataclass
class StorySlot:
    """One narrative beat in a template.

    Attributes:
        name:
            Human-readable label for this beat, e.g. "brooding-present".
        relative_position:
            Where this slot starts in the edit, expressed as a fraction
            of total runtime. 0.0 = start, 1.0 = end. Slots should be
            ordered and non-overlapping, but small overlaps are tolerated.
        duration_pct:
            Fraction of total runtime this slot occupies. The slot ends
            at ``relative_position + duration_pct``. Values must sum to
            approximately 1.0 across all slots in a template.
        required_shot_tags:
            Shot attributes that candidates SHOULD match. The optimizer
            treats these as soft preferences, scored by how many tags
            match, not hard filters (so the plan degrades gracefully when
            the library has sparse coverage). Tags correspond to the
            structured fields in the Shot dataclass: character_main,
            action, emotion, setting, era strings.
        excluded_shot_tags:
            Tags that must NOT appear in any shot assigned to this slot.
            These are enforced strictly.
        character_allowed_to_speak:
            When False the optimizer restricts this slot to
            ``character_speaks=False`` shots and places VO cues here
            instead of under the character's mouth.
        ideal_cut_count:
            Tuple of (min_cuts, max_cuts) for this slot. The optimizer
            targets the midpoint and treats the range as a soft guard.
        mood_profile:
            Editorial feel for this slot. Influences which song sections
            the optimizer maps this slot to (calm/breather -> verse/intro,
            tense -> building/pre-chorus, peak -> chorus/drop).
    """

    name: str
    relative_position: float
    duration_pct: float
    required_shot_tags: list[str]
    excluded_shot_tags: list[str]
    character_allowed_to_speak: bool
    ideal_cut_count: tuple[int, int]
    mood_profile: MoodProfile


# ---------------------------------------------------------------------------
# Template base dataclass
# ---------------------------------------------------------------------------

@dataclass
class NarrativeTemplate:
    """A complete narrative arc made up of ordered story slots.

    Attributes:
        name:
            Template identifier, e.g. "HauntedVeteran".
        description:
            One-sentence summary of what this template conveys.
        slots:
            Ordered list of StorySlot instances. Positions should run
            from 0.0 to ~1.0 without gaps.
        min_total_duration_sec:
            Minimum sensible edit length in seconds. Below this the slot
            durations become too compressed to work.
        max_total_duration_sec:
            Maximum sensible edit length. Beyond this pacing feels loose.
        notes:
            Optional freeform production notes for the editor.
    """

    name: str
    description: str
    slots: list[StorySlot]
    min_total_duration_sec: float
    max_total_duration_sec: float
    notes: str = ""

    def to_json(self, path: str | Path) -> None:
        """Serialise the template to a JSON file.

        Args:
            path: Destination path. Parent directories must exist.
        """
        data = asdict(self)
        Path(path).write_text(json.dumps(data, indent=2))

    @classmethod
    def from_json(cls, path: str | Path) -> NarrativeTemplate:
        """Load a NarrativeTemplate from a JSON file written by to_json().

        Args:
            path: JSON file path.

        Returns:
            Reconstructed NarrativeTemplate instance.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        raw = json.loads(Path(path).read_text())
        raw["slots"] = [
            StorySlot(
                **{**s, "ideal_cut_count": tuple(s["ideal_cut_count"])}
            )
            for s in raw["slots"]
        ]
        return cls(**raw)

    def slot_at(self, name: str) -> StorySlot | None:
        """Return the first slot with the given name, or None."""
        for s in self.slots:
            if s.name == name:
                return s
        return None

    def validate(self) -> list[str]:
        """Check internal consistency and return a list of warning strings.

        Checks:
        - Slots are ordered by relative_position.
        - duration_pct values sum to approximately 1.0 (within 0.05).
        - No slot extends past 1.0.
        - All mood_profile values are valid literals.
        """
        warnings: list[str] = []
        positions = [s.relative_position for s in self.slots]
        if positions != sorted(positions):
            warnings.append("Slots are not ordered by relative_position.")

        total_pct = sum(s.duration_pct for s in self.slots)
        if abs(total_pct - 1.0) > 0.05:
            warnings.append(
                f"duration_pct values sum to {total_pct:.3f}, expected ~1.0."
            )

        valid_moods = {"calm", "tense", "peak", "breather"}
        for s in self.slots:
            end = s.relative_position + s.duration_pct
            if end > 1.05:
                warnings.append(
                    f"Slot '{s.name}' extends to {end:.3f}, past 1.0."
                )
            if s.mood_profile not in valid_moods:
                warnings.append(
                    f"Slot '{s.name}' has invalid mood_profile '{s.mood_profile}'."
                )

        return warnings


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------

def _single_character_journey() -> NarrativeTemplate:
    """Classic hero-journey arc for a solo character.

    Works for any character with enough footage. Pacing is steady
    escalation with a strong visual climax and a short emotional close.
    """
    return NarrativeTemplate(
        name="SingleCharacterJourney",
        description=(
            "One character faces a challenge, escalates through conflict, "
            "hits a climax, and resolves. The canonical badass-tribute arc."
        ),
        min_total_duration_sec=60.0,
        max_total_duration_sec=300.0,
        notes=(
            "Best with a song that has a clear verse/chorus split. "
            "Map intro to song intro, climax to first big drop."
        ),
        slots=[
            StorySlot(
                name="intro-establish",
                relative_position=0.0,
                duration_pct=0.12,
                required_shot_tags=["leon", "calm", "standing"],
                excluded_shot_tags=["fighting", "dead", "chaotic"],
                character_allowed_to_speak=True,
                ideal_cut_count=(2, 4),
                mood_profile="calm",
            ),
            StorySlot(
                name="complication",
                relative_position=0.12,
                duration_pct=0.15,
                required_shot_tags=["tense"],
                excluded_shot_tags=["calm", "warm"],
                character_allowed_to_speak=True,
                ideal_cut_count=(4, 8),
                mood_profile="tense",
            ),
            StorySlot(
                name="escalation",
                relative_position=0.27,
                duration_pct=0.20,
                required_shot_tags=["aiming", "tense"],
                excluded_shot_tags=["calm", "warm"],
                character_allowed_to_speak=False,
                ideal_cut_count=(8, 14),
                mood_profile="tense",
            ),
            StorySlot(
                name="confrontation",
                relative_position=0.47,
                duration_pct=0.18,
                required_shot_tags=["fighting", "chaotic"],
                excluded_shot_tags=["calm", "still"],
                character_allowed_to_speak=False,
                ideal_cut_count=(10, 18),
                mood_profile="peak",
            ),
            StorySlot(
                name="climax",
                relative_position=0.65,
                duration_pct=0.20,
                required_shot_tags=["leon", "brutal", "shooting"],
                excluded_shot_tags=["calm", "quiet"],
                character_allowed_to_speak=False,
                ideal_cut_count=(12, 22),
                mood_profile="peak",
            ),
            StorySlot(
                name="resolution",
                relative_position=0.85,
                duration_pct=0.15,
                required_shot_tags=["leon", "calm", "quiet"],
                excluded_shot_tags=["chaotic", "fighting"],
                character_allowed_to_speak=True,
                ideal_cut_count=(2, 5),
                mood_profile="breather",
            ),
        ],
    )


def _multi_era_flashback() -> NarrativeTemplate:
    """Cross-era retrospective that earns its emotional weight through contrast.

    Works best when you have footage from 3+ eras or games. The flashback
    montage should feel deliberately fragmented -- many short cuts from
    different eras, then a return to the present for the payoff.
    """
    return NarrativeTemplate(
        name="MultiEraFlashback",
        description=(
            "Opens in the present, collapses into rapid era-spanning flashbacks, "
            "returns to present for the climax. Good for anniversary tributes."
        ),
        min_total_duration_sec=90.0,
        max_total_duration_sec=360.0,
        notes=(
            "Vary era order in the flashback montage for maximum disorientation. "
            "Color grade the flashbacks slightly warmer/de-saturated to signal memory."
        ),
        slots=[
            StorySlot(
                name="present-setup",
                relative_position=0.0,
                duration_pct=0.10,
                required_shot_tags=["leon", "tense", "standing"],
                excluded_shot_tags=["dead", "unconscious"],
                character_allowed_to_speak=True,
                ideal_cut_count=(2, 4),
                mood_profile="calm",
            ),
            StorySlot(
                name="flashback-early-era",
                relative_position=0.10,
                duration_pct=0.15,
                required_shot_tags=["RE2R-1998", "leon"],
                excluded_shot_tags=["calm", "warm"],
                character_allowed_to_speak=False,
                ideal_cut_count=(6, 12),
                mood_profile="tense",
            ),
            StorySlot(
                name="flashback-mid-era",
                relative_position=0.25,
                duration_pct=0.20,
                required_shot_tags=["RE4R-2004", "leon", "tense"],
                excluded_shot_tags=["calm"],
                character_allowed_to_speak=False,
                ideal_cut_count=(8, 16),
                mood_profile="tense",
            ),
            StorySlot(
                name="flashback-late-era",
                relative_position=0.45,
                duration_pct=0.15,
                required_shot_tags=["RE6-2013", "leon", "chaotic"],
                excluded_shot_tags=["calm", "quiet"],
                character_allowed_to_speak=False,
                ideal_cut_count=(8, 14),
                mood_profile="peak",
            ),
            StorySlot(
                name="return-to-present",
                relative_position=0.60,
                duration_pct=0.08,
                required_shot_tags=["leon", "tense"],
                excluded_shot_tags=["dead"],
                character_allowed_to_speak=True,
                ideal_cut_count=(2, 4),
                mood_profile="breather",
            ),
            StorySlot(
                name="climax",
                relative_position=0.68,
                duration_pct=0.22,
                required_shot_tags=["leon", "brutal", "fighting"],
                excluded_shot_tags=["calm", "quiet", "still"],
                character_allowed_to_speak=False,
                ideal_cut_count=(14, 24),
                mood_profile="peak",
            ),
            StorySlot(
                name="outro-still",
                relative_position=0.90,
                duration_pct=0.10,
                required_shot_tags=["leon", "quiet", "still"],
                excluded_shot_tags=["chaotic", "fighting"],
                character_allowed_to_speak=False,
                ideal_cut_count=(1, 3),
                mood_profile="breather",
            ),
        ],
    )


def _rise_and_fall() -> NarrativeTemplate:
    """Tragic arc: origin, rise, pride, catastrophic fall, aftermath.

    The only template where the climax is destructive rather than
    triumphant. Aftermath should feel empty, not resolved.
    """
    return NarrativeTemplate(
        name="RiseAndFall",
        description=(
            "Origin to peak confidence to catastrophe. Good for villain "
            "tributes or a character who pays a cost."
        ),
        min_total_duration_sec=90.0,
        max_total_duration_sec=300.0,
        notes=(
            "The fall section benefits from extremely fast cuts mixed with "
            "single long static shots for shock contrast. "
            "Aftermath should be a single long static shot if possible."
        ),
        slots=[
            StorySlot(
                name="origin",
                relative_position=0.0,
                duration_pct=0.15,
                required_shot_tags=["leon", "calm", "quiet"],
                excluded_shot_tags=["brutal", "fighting", "chaotic"],
                character_allowed_to_speak=True,
                ideal_cut_count=(2, 5),
                mood_profile="calm",
            ),
            StorySlot(
                name="rise",
                relative_position=0.15,
                duration_pct=0.22,
                required_shot_tags=["leon", "aiming", "tense"],
                excluded_shot_tags=["dead", "unconscious", "wounded"],
                character_allowed_to_speak=False,
                ideal_cut_count=(8, 16),
                mood_profile="tense",
            ),
            StorySlot(
                name="pride",
                relative_position=0.37,
                duration_pct=0.13,
                required_shot_tags=["leon", "standing", "calm"],
                excluded_shot_tags=["dead", "wounded", "chaotic"],
                character_allowed_to_speak=True,
                ideal_cut_count=(3, 6),
                mood_profile="calm",
            ),
            StorySlot(
                name="fall",
                relative_position=0.50,
                duration_pct=0.25,
                required_shot_tags=["chaotic", "brutal", "fighting"],
                excluded_shot_tags=["calm", "standing", "still"],
                character_allowed_to_speak=False,
                ideal_cut_count=(14, 26),
                mood_profile="peak",
            ),
            StorySlot(
                name="aftermath",
                relative_position=0.75,
                duration_pct=0.25,
                required_shot_tags=["wounded", "grim", "quiet"],
                excluded_shot_tags=["fighting", "brutal"],
                character_allowed_to_speak=True,
                ideal_cut_count=(2, 6),
                mood_profile="breather",
            ),
        ],
    )


def _haunted_veteran() -> NarrativeTemplate:
    """Leon Kennedy's canonical arc: survivor's guilt drives relentless action.

    Brooding opener lets VO land cleanly. Memory flashes are short and
    aggressive. Motivation reveal is the emotional hinge -- usually a
    speaking shot or VO. Then action and a quiet close.

    Designed for 60-120 second edits. Well-suited to In the End / Linkin Park
    style instrumentals where the chorus arrives once in the first half.
    """
    return NarrativeTemplate(
        name="HauntedVeteran",
        description=(
            "Brooding survivor processes trauma through action. Opens quiet, "
            "flashes back to what drives him, then a relentless action sequence "
            "closes on a single still image."
        ),
        min_total_duration_sec=60.0,
        max_total_duration_sec=150.0,
        notes=(
            "VO works best in brooding-present and motivation-reveal slots. "
            "Memory-flashes should cut on every beat or half-beat -- no comfort. "
            "The quiet-close should be a single shot, no cut."
        ),
        slots=[
            StorySlot(
                name="brooding-present",
                relative_position=0.0,
                duration_pct=0.15,
                required_shot_tags=["leon", "calm", "standing"],
                excluded_shot_tags=["fighting", "chaotic", "shooting"],
                character_allowed_to_speak=True,
                ideal_cut_count=(2, 4),
                mood_profile="calm",
            ),
            StorySlot(
                name="memory-flashes",
                relative_position=0.15,
                duration_pct=0.25,
                required_shot_tags=["tense", "leon"],
                excluded_shot_tags=["calm", "still", "quiet"],
                character_allowed_to_speak=False,
                ideal_cut_count=(10, 20),
                mood_profile="tense",
            ),
            StorySlot(
                name="motivation-reveal",
                relative_position=0.40,
                duration_pct=0.12,
                required_shot_tags=["leon", "grim"],
                excluded_shot_tags=["chaotic", "shooting"],
                character_allowed_to_speak=True,
                ideal_cut_count=(2, 5),
                mood_profile="tense",
            ),
            StorySlot(
                name="action-resolve",
                relative_position=0.52,
                duration_pct=0.28,
                required_shot_tags=["leon", "aiming", "shooting", "fighting"],
                excluded_shot_tags=["calm", "still", "quiet"],
                character_allowed_to_speak=False,
                ideal_cut_count=(14, 26),
                mood_profile="peak",
            ),
            StorySlot(
                name="climax",
                relative_position=0.80,
                duration_pct=0.12,
                required_shot_tags=["leon", "brutal", "tense"],
                excluded_shot_tags=["calm", "quiet", "still"],
                character_allowed_to_speak=False,
                ideal_cut_count=(6, 12),
                mood_profile="peak",
            ),
            StorySlot(
                name="quiet-close",
                relative_position=0.92,
                duration_pct=0.08,
                required_shot_tags=["leon", "quiet", "still"],
                excluded_shot_tags=["chaotic", "fighting", "shooting"],
                character_allowed_to_speak=False,
                ideal_cut_count=(1, 2),
                mood_profile="breather",
            ),
        ],
    )


def _ensemble_tribute() -> NarrativeTemplate:
    """Each character gets a focused showcase, then all converge for the finale.

    Suitable for multi-character tributes where equal screen time matters.
    Each character block is a mini-arc (establish, action, impact). The
    finale mixes characters rapidly in a shared sequence.

    Designed for 120-300 second edits.
    """
    return NarrativeTemplate(
        name="EnsembleTribute",
        description=(
            "Multiple characters each get 10-15s of dedicated screen time, "
            "then converge into a shared finale. Equal spotlight, shared climax."
        ),
        min_total_duration_sec=120.0,
        max_total_duration_sec=300.0,
        notes=(
            "Assign each character block a distinct color grade or LUT "
            "so the viewer can track whose section they are in. "
            "The finale mix should feel chaotic but return to order by the last beat."
        ),
        slots=[
            StorySlot(
                name="cold-open",
                relative_position=0.0,
                duration_pct=0.05,
                required_shot_tags=["tense"],
                excluded_shot_tags=["calm", "warm"],
                character_allowed_to_speak=False,
                ideal_cut_count=(1, 2),
                mood_profile="calm",
            ),
            StorySlot(
                name="character-1-leon",
                relative_position=0.05,
                duration_pct=0.14,
                required_shot_tags=["leon", "tense"],
                excluded_shot_tags=["dead", "unconscious"],
                character_allowed_to_speak=True,
                ideal_cut_count=(5, 10),
                mood_profile="tense",
            ),
            StorySlot(
                name="character-2-grace",
                relative_position=0.19,
                duration_pct=0.14,
                required_shot_tags=["grace", "tense"],
                excluded_shot_tags=["dead", "unconscious"],
                character_allowed_to_speak=True,
                ideal_cut_count=(5, 10),
                mood_profile="tense",
            ),
            StorySlot(
                name="character-3-claire",
                relative_position=0.33,
                duration_pct=0.12,
                required_shot_tags=["claire", "tense"],
                excluded_shot_tags=["dead", "unconscious"],
                character_allowed_to_speak=True,
                ideal_cut_count=(4, 9),
                mood_profile="tense",
            ),
            StorySlot(
                name="ensemble-buildup",
                relative_position=0.45,
                duration_pct=0.15,
                required_shot_tags=["tense", "fighting"],
                excluded_shot_tags=["calm", "still"],
                character_allowed_to_speak=False,
                ideal_cut_count=(10, 18),
                mood_profile="peak",
            ),
            StorySlot(
                name="finale-mix",
                relative_position=0.60,
                duration_pct=0.30,
                required_shot_tags=["brutal", "chaotic", "fighting"],
                excluded_shot_tags=["calm", "quiet", "still"],
                character_allowed_to_speak=False,
                ideal_cut_count=(20, 40),
                mood_profile="peak",
            ),
            StorySlot(
                name="shared-close",
                relative_position=0.90,
                duration_pct=0.10,
                required_shot_tags=["quiet", "calm"],
                excluded_shot_tags=["chaotic", "fighting"],
                character_allowed_to_speak=True,
                ideal_cut_count=(2, 4),
                mood_profile="breather",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, NarrativeTemplate] = {}


def _register() -> None:
    """Populate the module-level registry once."""
    for template in [
        _single_character_journey(),
        _multi_era_flashback(),
        _rise_and_fall(),
        _haunted_veteran(),
        _ensemble_tribute(),
    ]:
        _REGISTRY[template.name] = template


_register()


def get_template(name: str) -> NarrativeTemplate:
    """Return a template by name.

    Args:
        name: One of 'SingleCharacterJourney', 'MultiEraFlashback',
              'RiseAndFall', 'HauntedVeteran', 'EnsembleTribute'.

    Returns:
        NarrativeTemplate instance (a fresh copy from the registry).

    Raises:
        KeyError: If name is not in the registry.
    """
    if name not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY.keys()))
        raise KeyError(
            f"Template '{name}' not found. Available: {available}"
        )
    return _REGISTRY[name]


def list_templates() -> list[str]:
    """Return all registered template names."""
    return sorted(_REGISTRY.keys())


def all_templates() -> list[NarrativeTemplate]:
    """Return all registered templates."""
    return [_REGISTRY[k] for k in sorted(_REGISTRY.keys())]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Registered narrative templates:\n")
    for t in all_templates():
        warnings = t.validate()
        status = "OK" if not warnings else f"WARNINGS: {warnings}"
        print(f"  {t.name}")
        print(f"    {t.description}")
        print(f"    Slots  : {len(t.slots)}")
        print(f"    Runtime: {t.min_total_duration_sec:.0f}s - {t.max_total_duration_sec:.0f}s")
        print(f"    Status : {status}")
        print()
