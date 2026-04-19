"""Agentic orchestration layer — natural-language directives for edit plans.

Inspired by HKUDS/VideoAgent's intent→graph→reflect pattern, scoped down to
what FandomForge actually needs: a user says "make it sadder" or "more Sam
scenes" or "cut the first 20 seconds," and the engine adjusts without
re-running the whole pipeline.

Public surface:

    from fandomforge.intelligence.agentic import Agent, AgentContext
    ctx = AgentContext.from_project(project_dir)
    agent = Agent(ctx)
    result = agent.run("make it sadder and lean on the brother arc")

The agent:
    1. Parses the directive into structured intent (mood shift, character
       emphasis, pacing change, content include/exclude).
    2. Picks tools — re-score dialogue lines, re-rank shot library, nudge
       narrative_priorities, adjust LUT intensity, regenerate layered plan.
    3. Executes in order.
    4. Reflects on the output against the directive and flags anything
       that clearly doesn't match, returning a summary the user can accept
       or push back on.

All LLM calls go through OPENAI_API_KEY + gpt-4o-mini by default. The
intent parser has a deterministic rule-based fallback for simple prompts
so the agent works without any LLM if the user is offline.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intent model
# ---------------------------------------------------------------------------

@dataclass
class Intent:
    """Structured representation of a user directive.

    Multiple fields can be set at once — a directive like "sadder and more
    Sam" produces {mood_shift: 'sadder', character_emphasis: ['sam']}.
    """

    mood_shift: str | None = None
    character_emphasis: list[str] = field(default_factory=list)
    character_deemphasis: list[str] = field(default_factory=list)
    narrative_boost: list[str] = field(default_factory=list)
    narrative_mute: list[str] = field(default_factory=list)
    pacing: str | None = None   # "faster" | "slower" | None
    duration_delta_sec: float | None = None
    lut_nudge: float | None = None
    replace_song: str | None = None
    raw_prompt: str = ""
    notes: list[str] = field(default_factory=list)

    def is_noop(self) -> bool:
        return not any([
            self.mood_shift, self.character_emphasis, self.character_deemphasis,
            self.narrative_boost, self.narrative_mute, self.pacing,
            self.duration_delta_sec, self.lut_nudge, self.replace_song,
        ])


# ---------------------------------------------------------------------------
# Deterministic intent parser (no LLM needed for common phrasings)
# ---------------------------------------------------------------------------

_MOOD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "sadder": ("sadder", "sad", "melancholy", "heavier", "emotional", "gloomier"),
    "happier": ("happier", "lighter", "upbeat", "joyful"),
    "tenser": ("tenser", "tense", "anxious", "dread", "scarier"),
    "angrier": ("angrier", "angry", "aggressive", "hostile", "fired up"),
    "calmer": ("calmer", "softer", "quieter", "gentler"),
    "more-hopeful": ("hopeful", "uplifting", "triumphant", "more hopeful"),
}

_PACING_WORDS = {
    "faster": ("faster", "snappier", "quicker", "tight", "tighter"),
    "slower": ("slower", "breathe", "slow it down", "more space"),
}

_MUTE_VERBS = ("cut", "drop", "remove", "mute", "deemphasize", "less", "no more")
_BOOST_VERBS = ("more", "emphasize", "boost", "lean on", "focus on", "add")


def parse_intent_rulebased(prompt: str) -> Intent:
    """Parse simple English directives with zero LLM calls."""
    intent = Intent(raw_prompt=prompt)
    p = prompt.lower()

    # Mood
    for canon, syns in _MOOD_SYNONYMS.items():
        if any(s in p for s in syns):
            intent.mood_shift = canon
            break

    # Pacing
    for canon, syns in _PACING_WORDS.items():
        if any(s in p for s in syns):
            intent.pacing = canon
            break

    # Duration delta
    m = re.search(r"(cut|trim|add)\s+(\d+)\s*(?:s|sec|seconds)", p)
    if m:
        sign = -1 if m.group(1) in {"cut", "trim"} else +1
        intent.duration_delta_sec = sign * float(m.group(2))

    # Character emphasis / deemphasis — naive name-keyword extraction
    # Format: "more X", "less X", "no more X", "lean on X"
    for verb in _BOOST_VERBS:
        for m in re.finditer(
            rf"{verb}\s+([a-z][a-z]+(?:\s+[a-z][a-z]+)?)", p,
        ):
            name = m.group(1).strip()
            if name not in {"space", "emotional", "emphasis", "pacing", "energy"}:
                intent.character_emphasis.append(name)
    for verb in _MUTE_VERBS:
        for m in re.finditer(
            rf"{verb}\s+([a-z][a-z]+(?:\s+[a-z][a-z]+)?)", p,
        ):
            name = m.group(1).strip()
            if len(name) >= 3:
                intent.character_deemphasis.append(name)

    # Arc emphasis ("brother arc", "dying arc")
    for m in re.finditer(r"([a-z]+)\s+arc\b", p):
        intent.narrative_boost.append(m.group(1))

    # LUT nudge from "darker / brighter / more color / less color"
    if "darker" in p or "moodier" in p:
        intent.lut_nudge = 0.15
    elif "lighter" in p or "cleaner" in p:
        intent.lut_nudge = -0.15

    return intent


def parse_intent_llm(prompt: str, *, api_key: str | None = None) -> Intent | None:
    """Parse via gpt-4o-mini. Returns None on any failure (caller should
    fall back to parse_intent_rulebased)."""
    api_key = api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    import urllib.request

    sys_prompt = (
        "You extract edit directives for a video tribute engine. "
        "Return ONLY a JSON object with these optional keys (omit any you "
        "can't infer): mood_shift (one of: sadder, happier, tenser, angrier, "
        "calmer, more-hopeful), character_emphasis (list of lowercase names), "
        "character_deemphasis (list), narrative_boost (list of arc themes "
        "like 'brother' or 'dying'), narrative_mute (list), pacing (faster "
        "or slower), duration_delta_sec (signed int), lut_nudge (signed float "
        "between -0.3 and 0.3), notes (string list of anything else). No prose."
    )
    body = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read())
        content = j["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM intent parse failed: %s", exc)
        return None

    return Intent(
        raw_prompt=prompt,
        mood_shift=parsed.get("mood_shift"),
        character_emphasis=list(parsed.get("character_emphasis") or []),
        character_deemphasis=list(parsed.get("character_deemphasis") or []),
        narrative_boost=list(parsed.get("narrative_boost") or []),
        narrative_mute=list(parsed.get("narrative_mute") or []),
        pacing=parsed.get("pacing"),
        duration_delta_sec=parsed.get("duration_delta_sec"),
        lut_nudge=parsed.get("lut_nudge"),
        notes=list(parsed.get("notes") or []),
    )


def parse_intent(prompt: str, *, prefer_llm: bool = True) -> Intent:
    """Best-effort intent parse. Uses LLM when possible, falls back to rules."""
    if prefer_llm:
        out = parse_intent_llm(prompt)
        if out is not None and not out.is_noop():
            return out
    return parse_intent_rulebased(prompt)


# ---------------------------------------------------------------------------
# Agent context — what the agent can read + mutate
# ---------------------------------------------------------------------------

@dataclass
class AgentContext:
    """Everything the agent needs to adjust an edit without running the
    whole pipeline. Loaded from the project directory; mutated in place
    and persisted back on commit()."""

    project_dir: Path
    config: Any  # ProjectConfig
    style_profile: dict[str, Any] = field(default_factory=dict)
    layered_plan: dict[str, Any] | None = None

    @classmethod
    def from_project(cls, project_dir: Path) -> "AgentContext":
        from fandomforge.config import load_project_config
        cfg = load_project_config(project_dir)

        style_profile: dict[str, Any] = {}
        style_path = project_dir / ".style-template.json"
        if style_path.exists():
            style_profile = json.loads(style_path.read_text())

        plan_path = project_dir / ".layered-plan-final.json"
        layered_plan = None
        if plan_path.exists():
            layered_plan = json.loads(plan_path.read_text())

        return cls(
            project_dir=project_dir,
            config=cfg,
            style_profile=style_profile,
            layered_plan=layered_plan,
        )

    def commit_config(self) -> Path:
        """Write current config back to project-config.yaml."""
        from fandomforge.config import save_project_config
        path = self.project_dir / "project-config.yaml"
        return save_project_config(self.config, path)


# ---------------------------------------------------------------------------
# Tools — atomic operations an agent can compose
# ---------------------------------------------------------------------------

class Tool(Protocol):
    name: str
    def applies(self, intent: Intent) -> bool: ...
    def run(self, intent: Intent, ctx: AgentContext) -> str: ...


@dataclass
class AdjustNarrativePriorities:
    name: str = "adjust_narrative_priorities"

    _MOOD_KEYWORDS: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            "sadder": ("loss", "couldn't save", "gone", "grief", "goodbye", "alone"),
            "happier": ("together", "win", "made it", "smile", "back"),
            "tenser": ("danger", "hurry", "they're coming", "run", "now"),
            "angrier": ("bastards", "kill", "never again", "enough"),
            "calmer": ("thinking", "quiet", "still", "breath"),
            "more-hopeful": ("we can", "future", "hope", "tomorrow"),
        }
    )

    def applies(self, intent: Intent) -> bool:
        return bool(
            intent.mood_shift or intent.narrative_boost or intent.narrative_mute
        )

    def run(self, intent: Intent, ctx: AgentContext) -> str:
        priorities = list(ctx.config.narrative_priorities)
        added: list[str] = []
        removed: list[str] = []

        if intent.mood_shift in self._MOOD_KEYWORDS:
            for kw in self._MOOD_KEYWORDS[intent.mood_shift]:
                if kw not in priorities:
                    priorities.append(kw)
                    added.append(kw)

        for arc in intent.narrative_boost:
            if arc not in priorities:
                priorities.append(arc)
                added.append(arc)

        for mute in intent.narrative_mute:
            priorities = [p for p in priorities if mute not in p.lower()]
            removed.append(mute)

        ctx.config.narrative_priorities = priorities
        return (
            f"narrative_priorities: +{added or 'none'} -{removed or 'none'} "
            f"({len(priorities)} total)"
        )


@dataclass
class AdjustLUTIntensity:
    name: str = "adjust_lut_intensity"

    def applies(self, intent: Intent) -> bool:
        return intent.lut_nudge is not None

    def run(self, intent: Intent, ctx: AgentContext) -> str:
        if intent.lut_nudge is None:
            return "skip"
        old = ctx.config.lut_intensity
        new = max(0.0, min(1.0, old + intent.lut_nudge))
        ctx.config.lut_intensity = new
        return f"lut_intensity {old:.2f} → {new:.2f}"


@dataclass
class AdjustDuration:
    name: str = "adjust_duration"

    def applies(self, intent: Intent) -> bool:
        return intent.duration_delta_sec is not None

    def run(self, intent: Intent, ctx: AgentContext) -> str:
        if intent.duration_delta_sec is None:
            return "skip"
        current = ctx.config.target_duration_sec or 90.0
        new = max(15.0, current + intent.duration_delta_sec)
        ctx.config.target_duration_sec = new
        return f"target_duration_sec {current:.0f} → {new:.0f}"


@dataclass
class AdjustCharacterAliases:
    """When the user emphasizes a supporting character, add them as aliases
    so dialogue lines mentioning that character score higher."""

    name: str = "adjust_character_aliases"

    def applies(self, intent: Intent) -> bool:
        return bool(intent.character_emphasis)

    def run(self, intent: Intent, ctx: AgentContext) -> str:
        existing = {a.lower() for a in ctx.config.character_aliases}
        primary = ctx.config.character.lower()
        added: list[str] = []
        for name in intent.character_emphasis:
            if not name or name.lower() == primary:
                continue
            if name.lower() in existing:
                continue
            ctx.config.character_aliases.append(name.lower())
            added.append(name.lower())
        return f"character_aliases +{added or 'none'}"


@dataclass
class AdjustPacingHint:
    """Pacing hint stored in config — future pipeline read."""

    name: str = "adjust_pacing_hint"

    def applies(self, intent: Intent) -> bool:
        return intent.pacing is not None

    def run(self, intent: Intent, ctx: AgentContext) -> str:
        # Stored as a note in style_profile; the layered_planner reads
        # this in future work. For now, just record the intent.
        ctx.style_profile.setdefault("agent_hints", {})["pacing"] = intent.pacing
        return f"pacing_hint = {intent.pacing}"


DEFAULT_TOOLS: list[Tool] = [
    AdjustNarrativePriorities(),
    AdjustLUTIntensity(),
    AdjustDuration(),
    AdjustCharacterAliases(),
    AdjustPacingHint(),
]


# ---------------------------------------------------------------------------
# Agent — orchestrates parse → plan → execute → reflect
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    intent: Intent
    actions: list[str]
    warnings: list[str]
    committed: bool


class Agent:
    """Top-level orchestrator."""

    def __init__(
        self,
        ctx: AgentContext,
        *,
        tools: list[Tool] | None = None,
        prefer_llm: bool = True,
    ) -> None:
        self.ctx = ctx
        self.tools = tools or DEFAULT_TOOLS
        self.prefer_llm = prefer_llm

    def run(self, prompt: str, *, commit: bool = True) -> AgentResult:
        intent = parse_intent(prompt, prefer_llm=self.prefer_llm)
        actions: list[str] = []
        warnings: list[str] = []

        if intent.is_noop():
            warnings.append("Couldn't parse any actionable directive from prompt.")
            return AgentResult(intent=intent, actions=[], warnings=warnings, committed=False)

        for tool in self.tools:
            if not tool.applies(intent):
                continue
            try:
                msg = tool.run(intent, self.ctx)
                actions.append(f"{tool.name}: {msg}")
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{tool.name} failed: {exc}")

        committed = False
        if commit and actions:
            try:
                self.ctx.commit_config()
                committed = True
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"commit failed: {exc}")

        # Reflection — simple sanity check
        warnings.extend(self._reflect(intent, actions))
        return AgentResult(intent=intent, actions=actions, warnings=warnings, committed=committed)

    def _reflect(self, intent: Intent, actions: list[str]) -> list[str]:
        """Post-execution sanity checks. Returns list of warnings."""
        warnings: list[str] = []
        if intent.mood_shift and not any("narrative_priorities" in a for a in actions):
            warnings.append(
                f"mood_shift={intent.mood_shift} requested but no priority "
                f"keywords matched — mood won't change until next render."
            )
        if intent.character_emphasis and not any(
            "character_aliases" in a for a in actions
        ):
            warnings.append(
                f"character_emphasis={intent.character_emphasis} but nothing "
                f"was added (maybe already aliased)."
            )
        return warnings


def save_intent_log(project_dir: Path, prompt: str, result: AgentResult) -> Path:
    """Append an agent interaction to .agent-log.jsonl for later review."""
    path = project_dir / ".agent-log.jsonl"
    entry = {
        "prompt": prompt,
        "intent": {
            "mood_shift": result.intent.mood_shift,
            "character_emphasis": result.intent.character_emphasis,
            "character_deemphasis": result.intent.character_deemphasis,
            "narrative_boost": result.intent.narrative_boost,
            "narrative_mute": result.intent.narrative_mute,
            "pacing": result.intent.pacing,
            "duration_delta_sec": result.intent.duration_delta_sec,
            "lut_nudge": result.intent.lut_nudge,
        },
        "actions": result.actions,
        "warnings": result.warnings,
        "committed": result.committed,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return path
