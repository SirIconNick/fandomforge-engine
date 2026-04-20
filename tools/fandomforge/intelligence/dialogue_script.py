"""Dialogue script stage (Phase 6.1) — turn a user prompt into an ordered
list of utterances the dialogue-narrative edit should deliver.

Heuristic-first: extracts quoted strings, sentence-segmented imperatives,
and named-speaker patterns. Falls back to splitting the prompt into
sentences when no explicit script structure is provided. LLM upgrade hook
documented (set ANTHROPIC_API_KEY for richer scripts in a future pass).

Cross-type by design: dialogue scripts only matter when intent.edit_type
is dialogue_narrative, but the schema applies equally to any project
that wants stitched dialogue (e.g. a tribute with a closing voice-over).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# Patterns that signal an explicit script structure in the prompt.
QUOTED_LINE_PATTERN = re.compile(r"[\"\u201c]([^\"\u201d]{3,})[\"\u201d]")
SPEAKER_LINE_PATTERN = re.compile(
    r"^[ \t]*([A-Z][A-Za-z' \-]+?):\s*(.+?)\s*$", flags=re.MULTILINE,
)


# Intent keyword → label table — drives the "intent" field for found lines.
INTENT_KEYWORDS = {
    "defiant":      ("won't", "no", "never", "refuse", "i'm not"),
    "recognition":  ("now i see", "i understand", "of course", "you're"),
    "turn":         ("but", "until", "now", "this time"),
    "declaration":  ("i am", "we are", "this is", "my name"),
    "question":     ("?",),
    "command":      ("!", "go", "stop", "do it", "kill"),
    "lament":       ("why", "should have", "if only"),
}


@dataclass
class ScriptLine:
    index: int
    text: str
    intent: str = "any"
    speaker_role: str = "any"
    target_duration_ms: int = 1500
    voice_register: str = "neutral"
    fandom_constraint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "index": self.index,
            "text": self.text,
            "intent": self.intent,
            "speaker_role": self.speaker_role,
            "target_duration_ms": self.target_duration_ms,
            "voice_register": self.voice_register,
        }
        if self.fandom_constraint:
            out["fandom_constraint"] = self.fandom_constraint
        return out


def _classify_intent(text: str) -> str:
    lower = text.lower()
    for label, needles in INTENT_KEYWORDS.items():
        for n in needles:
            if n in lower:
                return label
    return "any"


def _estimate_duration_ms(text: str) -> int:
    """Roughly 150 words per minute = 400ms per word, plus 200ms padding."""
    words = max(1, len(text.split()))
    return min(8000, max(400, int(words * 400) + 200))


def build_script(
    prompt: str,
    *,
    project_slug: str,
    intent: dict[str, Any] | None = None,
    max_lines: int = 8,
) -> dict[str, Any]:
    """Produce a dialogue-script.schema.json dict from the prompt.

    Args:
        prompt: free-text from the user.
        project_slug: required by schema.
        intent: optional intent.json dict (for speaker_role inference + tone).
        max_lines: cap on extracted lines (per amendment A4 negotiate-down rule).
    """
    if not prompt:
        return {
            "schema_version": 1, "project_slug": project_slug,
            "concept": "", "lines": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": "ff dialogue scriptwriter (Phase 6.1)",
        }

    lines: list[ScriptLine] = []
    seen: set[str] = set()

    # 1. Explicit "Speaker: line" patterns
    for m in SPEAKER_LINE_PATTERN.finditer(prompt):
        speaker, text = m.group(1).strip(), m.group(2).strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        lines.append(ScriptLine(
            index=len(lines), text=text,
            intent=_classify_intent(text),
            speaker_role="any",
            target_duration_ms=_estimate_duration_ms(text),
            fandom_constraint=speaker if speaker else None,
        ))
        if len(lines) >= max_lines:
            break

    # 2. Quoted strings
    if len(lines) < max_lines:
        for m in QUOTED_LINE_PATTERN.finditer(prompt):
            text = m.group(1).strip()
            if not text or text.lower() in seen:
                continue
            seen.add(text.lower())
            lines.append(ScriptLine(
                index=len(lines), text=text,
                intent=_classify_intent(text),
                target_duration_ms=_estimate_duration_ms(text),
            ))
            if len(lines) >= max_lines:
                break

    # 3. Fall back to sentence segmentation (cap at max_lines)
    if not lines:
        # Preserve trailing punctuation by capturing it in the split.
        sentences: list[str] = []
        for match in re.finditer(r"[^.!?]+[.!?]+|[^.!?]+$", prompt):
            text = match.group(0).strip()
            if text:
                sentences.append(text)
        for s in sentences[:max_lines]:
            lines.append(ScriptLine(
                index=len(lines), text=s,
                intent=_classify_intent(s),
                target_duration_ms=_estimate_duration_ms(s),
            ))

    # Speaker-role inference from intent.json speakers list
    if intent and intent.get("speakers"):
        primary = intent["speakers"][0].get("name", "") if intent["speakers"] else ""
        if primary and lines:
            lines[0].speaker_role = "protagonist"
            lines[0].fandom_constraint = lines[0].fandom_constraint or primary

    return {
        "schema_version": 1,
        "project_slug": project_slug,
        "concept": (intent or {}).get("prompt_text", "")[:200] if intent else prompt[:200],
        "lines": [l.to_dict() for l in lines],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "ff dialogue scriptwriter (Phase 6.1)",
    }


__all__ = ["ScriptLine", "build_script"]
