"""Director — GPT-powered edit suggestions and holistic edit-plan review.

Given theme + song structure + source materials, GPT proposes:
- Act breakdown with emotional goals
- Shot order recommendations
- Transition suggestions per cut
- Pacing notes (hold longer here, cut faster there)
- Critical issues flagged (overused shots, theme drift)

The holistic reviewer (review_edit_plan) runs a full GPT-4o pass across the
complete edit plan, narrative template, song structure, and style profile.
It returns structured scores and ordered revision suggestions.

Inspired by Instacut's "Director" module and ViMax's script-to-storyboard pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fandomforge.intelligence.openai_helper import _load_env


@dataclass
class DirectorSuggestion:
    success: bool
    text: str = ""
    json_path: Path | None = None
    error: str = ""


# ---------------------------------------------------------------------------
# Holistic review dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PacingFlag:
    """A single pacing issue identified in the holistic review.

    Attributes:
        section_name: Name of the act or slot where the issue occurs.
        time_range: [start_sec, end_sec] of the flagged section.
        issue: Description of the pacing problem (too long, too uniform, etc.).
        severity: 'minor', 'moderate', or 'critical'.
    """

    section_name: str
    time_range: list[float]
    issue: str
    severity: str = "moderate"


@dataclass
class ActTransitionQuality:
    """Assessment of how well act boundaries are marked visually.

    Attributes:
        from_act: Name or number of the outgoing act.
        to_act: Name or number of the incoming act.
        quality: One of 'strong', 'adequate', 'weak', 'missing'.
        notes: Specific observations about what makes the transition strong or weak.
    """

    from_act: str
    to_act: str
    quality: str
    notes: str


@dataclass
class RevisionSuggestion:
    """A single ordered revision to apply to the shot list.

    Attributes:
        priority: 1-based order; fix priority 1 before priority 2.
        shot_numbers: The shot(s) this revision targets.
        action: One of 'replace', 'reorder', 'trim', 'add', 'remove', 'retag'.
        description: Specific instruction (what to change and why).
        impact: Expected improvement from applying this fix.
    """

    priority: int
    shot_numbers: list[int]
    action: str
    description: str
    impact: str


@dataclass
class DirectorReview:
    """Holistic edit-plan review result from GPT-4o.

    Attributes:
        success: False if the API call failed or returned unparseable output.
        story_arc_score: 0.0-1.0. How well the emotional progression matches
            the narrative template. 1.0 = perfect slot-to-slot alignment.
        continuity_flags: Characters that appear before being properly introduced,
            or whose presence creates timeline discontinuities.
        pacing_flags: Sections that feel too long, too short, or too uniform.
        act_transition_quality: Quality assessment for each act boundary.
        climax_earned: True if the peak moment is preceded by sufficient
            build-up and emotional escalation to feel deserved.
        climax_notes: Prose explanation of the climax assessment.
        prose_review: Full narrative prose review from GPT-4o.
        specific_revision_suggestions: Ordered list of concrete fixes.
        overall_readiness: 'ready', 'needs_minor_fixes', or 'needs_major_rework'.
        error: Error message if success is False.
    """

    success: bool
    story_arc_score: float = 0.0
    continuity_flags: list[str] = field(default_factory=list)
    pacing_flags: list[PacingFlag] = field(default_factory=list)
    act_transition_quality: list[ActTransitionQuality] = field(default_factory=list)
    climax_earned: bool = False
    climax_notes: str = ""
    prose_review: str = ""
    specific_revision_suggestions: list[RevisionSuggestion] = field(default_factory=list)
    overall_readiness: str = "needs_major_rework"
    error: str = ""


DIRECTOR_SYSTEM_PROMPT = """You are the Edit Strategist for FandomForge — a director-level AI that plans multifandom video edits.

You think in:
- ENERGY CURVES (how energy rises and falls across the edit)
- ACT STRUCTURE (intro/rise/drop/climax/outro)
- THEME COHERENCE (every shot must serve the one-sentence theme)
- BEAT SYNC (cuts must land on song beats)
- TRANSITION LANGUAGE (hard cuts dominate; match cuts + flash-stacks for meaning)

You give SPECIFIC, ACTIONABLE feedback. No hedging. No corporate language.
Back every recommendation with a timestamp, a shot number, or a beat reference.

When proposing edits you return structured JSON with:
- act_plan: list of acts with time ranges and emotional goals
- shot_placements: recommended song_time per shot (optional adjustments)
- transition_map: transition type between each consecutive shot pair
- warnings: issues spotted (overused shots, pacing flat, theme drift)
- next_action: the single most important thing for the user to do next
"""


def propose_edit(
    *,
    theme: str,
    song_structure_hint: str = "",
    shots_summary: list[dict],  # [{number, hero, description, source_id, duration_sec, song_time_sec}]
    target_runtime_sec: float = 60.0,
    output_json: str | Path | None = None,
    project_root: Path | str = ".",
) -> DirectorSuggestion:
    """Ask GPT to propose an edit plan given the theme + shots + song structure."""
    _load_env(project_root)

    try:
        from openai import OpenAI
    except ImportError:
        return DirectorSuggestion(
            success=False, error="OpenAI SDK not installed"
        )

    import os
    if not os.environ.get("OPENAI_API_KEY"):
        return DirectorSuggestion(
            success=False, error="OPENAI_API_KEY not set in .env"
        )

    # Compact the shots for the prompt (OpenAI has token limits; keep this tight)
    compact_shots = [
        {
            "n": s["number"],
            "hero": s.get("hero", ""),
            "desc": (s.get("description") or "")[:80],
            "src": s.get("source_id", ""),
            "dur": s.get("duration_sec", 0),
            "t": s.get("song_time_sec", 0),
        }
        for s in shots_summary[:80]  # cap at 80 shots to stay within context
    ]

    user_prompt = f"""THEME: {theme}

TARGET RUNTIME: {target_runtime_sec:.0f} seconds

SONG STRUCTURE NOTES:
{song_structure_hint or '(no song structure provided — infer from shot count)'}

CURRENT SHOT LIST ({len(shots_summary)} shots):
{json.dumps(compact_shots, indent=1)}

TASK:
1. Review the shot list against the theme.
2. Identify 3-5 ACTS with emotional goals and time ranges.
3. Flag any shots that don't serve the theme.
4. Recommend transition types (hard_cut | match_cut | whip_pan | flash_stack | dip_to_black | cross_dissolve) between consecutive shots.
5. Flag pacing issues (too uniform, no valleys, drop fatigue).
6. Give ONE concrete next action.

Return ONLY JSON matching this schema:
{{
  "theme_fit": "assessment, 1-2 sentences",
  "act_plan": [
    {{"act": 1, "name": "...", "time_range": [0.0, 15.0], "emotional_goal": "...", "energy_level": "low|rising|peak|valley|high", "shot_numbers": [1,2,3]}}
  ],
  "transition_map": [
    {{"from_shot": 1, "to_shot": 2, "type": "hard_cut", "reason": "..."}}
  ],
  "problem_shots": [
    {{"shot_number": 14, "issue": "doesn't serve theme", "fix": "replace with..."}}
  ],
  "pacing_notes": "...",
  "next_action": "single concrete action"
}}
"""

    try:
        client = OpenAI()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": DIRECTOR_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        text = response.choices[0].message.content or "{}"
    except Exception as exc:
        return DirectorSuggestion(success=False, error=str(exc))

    # Validate it's JSON
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return DirectorSuggestion(success=False, text=text, error=f"Invalid JSON: {exc}")

    json_path = None
    if output_json:
        path = Path(output_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(parsed, indent=2))
        json_path = path

    return DirectorSuggestion(success=True, text=json.dumps(parsed, indent=2), json_path=json_path)



# ---------------------------------------------------------------------------
# System prompt for holistic review
# ---------------------------------------------------------------------------

_HOLISTIC_REVIEW_SYSTEM_PROMPT = """You are the senior Edit Strategist for FandomForge — a director-level AI that reviews complete multifandom video edit plans.

You think at the FILM level, not the shot level. Your job is holistic assessment:
- Does the emotional arc BUILD and LAND correctly from start to finish?
- Does each act transition feel earned and visually distinct?
- Is the climax the most powerful moment because of what came before it?
- Are characters introduced before they are relied upon for emotional impact?
- Is pacing varied enough to sustain viewer attention for the full runtime?

You give PRECISE feedback tied to shot numbers, section names, and song timestamps.
You do NOT hedge. You say what is wrong and what to do about it, specifically.
You are aware that the editor will implement your suggestions in an NLE (DaVinci Resolve or Premiere).

Your revision suggestions are ordered by impact: the fix that helps the most comes first.
"""

_HOLISTIC_REVIEW_USER_TEMPLATE = """NARRATIVE TEMPLATE: {template_name}
TEMPLATE DESCRIPTION: {template_description}

TEMPLATE SLOTS (in order):
{template_slots}

SONG STRUCTURE:
{song_structure}

COMPLETE SHOT LIST ({shot_count} shots, {total_duration:.1f}s total):
{shot_list_json}

DIALOGUE PLACEMENTS:
{dialogue_placements}

STYLE PROFILE:
{style_profile}

---

REVIEW TASK:

Conduct a thorough director-level review. Return ONLY valid JSON matching this exact schema:

{{
  "story_arc_score": 0.85,
  "prose_review": "Multi-paragraph narrative assessment of the full edit. Be specific.",
  "continuity_flags": [
    "Shot 14: Leon appears in RE9 footage before his RE9 introduction in Act 2"
  ],
  "pacing_flags": [
    {{
      "section_name": "memory-flashes",
      "time_range": [18.0, 34.0],
      "issue": "16 seconds at uniform 1.1s cuts — no variation, viewer fatigue by shot 22",
      "severity": "moderate"
    }}
  ],
  "act_transition_quality": [
    {{
      "from_act": "brooding-present",
      "to_act": "memory-flashes",
      "quality": "weak",
      "notes": "Hard cut from Leon standing still to action with no visual signal of the flashback"
    }}
  ],
  "climax_earned": true,
  "climax_notes": "The climax at 1:08 is earned because Act 3 builds through 14 consecutive tense shots.",
  "specific_revision_suggestions": [
    {{
      "priority": 1,
      "shot_numbers": [22, 23, 24],
      "action": "trim",
      "description": "Reduce shots 22-24 to 0.6s each. They are the longest in memory-flashes but show low-energy scenes.",
      "impact": "Breaks the monotony and aligns pacing with the song's building pre-chorus."
    }}
  ],
  "overall_readiness": "needs_minor_fixes"
}}

Scores and flags must reference actual shot numbers and section names from the shot list above.
story_arc_score must be a float 0.0-1.0.
overall_readiness must be one of: "ready", "needs_minor_fixes", "needs_major_rework".
"""


def review_edit_plan(
    edit_plan: dict[str, Any],
    narrative_template: "Any | None" = None,
    song_structure: "Any | None" = None,
    style_profile: dict[str, Any] | None = None,
    *,
    output_json: str | Path | None = None,
    project_root: Path | str = ".",
) -> DirectorReview:
    """Run a holistic GPT-4o review of a complete edit plan.

    Feeds the full narrative template, shot list with emotion/era/description,
    song section map, and style profile to GPT-4o for a director-level assessment.
    Returns a structured DirectorReview with scores, flags, and ordered revision
    suggestions.

    Args:
        edit_plan: The edit plan dictionary. Expected keys:
            - 'act_plan': list of act dicts from director.propose_edit()
            - 'shots': list of shot dicts with number, hero, description, era,
              emotion/mood, song_time_sec, duration_sec, slot_name
            - 'dialogue_placements': list of VO/dialogue entries
            - Any additional keys are included in the style context.
        narrative_template: NarrativeTemplate instance (or dict) describing the
            arc structure. If None, the template name is inferred from edit_plan.
        song_structure: SongStructure instance (or dict) with section map and
            drop moments. If None, song structure notes from edit_plan are used.
        style_profile: Dict of style parameters (cut rate target, color grade
            notes, era breakdown, etc.). Optional.
        output_json: If given, the raw parsed JSON from GPT-4o is saved here.
        project_root: Root directory for .env loading.

    Returns:
        DirectorReview with all assessment fields populated, or
        DirectorReview(success=False, error=...) on failure.
    """
    _load_env(project_root)

    try:
        from openai import OpenAI
    except ImportError:
        return DirectorReview(success=False, error="OpenAI SDK not installed")

    import os
    if not os.environ.get("OPENAI_API_KEY"):
        return DirectorReview(success=False, error="OPENAI_API_KEY not set in .env")

    # Serialize narrative template
    template_name = "Unknown"
    template_description = ""
    template_slots_text = "(no template provided)"

    if narrative_template is not None:
        if hasattr(narrative_template, "name"):
            # NarrativeTemplate dataclass
            template_name = narrative_template.name
            template_description = narrative_template.description
            slot_lines = []
            for s in narrative_template.slots:
                slot_lines.append(
                    f"  [{s.relative_position:.0%}-"
                    f"{(s.relative_position + s.duration_pct):.0%}] "
                    f"{s.name} ({s.mood_profile}): "
                    f"required={s.required_shot_tags}, "
                    f"cuts={s.ideal_cut_count}"
                )
            template_slots_text = "\n".join(slot_lines)
        elif isinstance(narrative_template, dict):
            template_name = narrative_template.get("name", "Unknown")
            template_description = narrative_template.get("description", "")
            template_slots_text = json.dumps(
                narrative_template.get("slots", []), indent=2
            )
    elif edit_plan.get("template_name"):
        template_name = str(edit_plan["template_name"])

    # Serialize song structure
    if song_structure is not None:
        if hasattr(song_structure, "sections"):
            section_lines = []
            for sec in song_structure.sections:
                section_lines.append(
                    f"  [{sec.start_time:.1f}s-{sec.end_time:.1f}s] "
                    f"{getattr(sec, 'label', sec.kind)} ({getattr(sec, 'energy_level', sec.mood)})"
                )
            drops = getattr(song_structure, "drop_moments", [])
            if drops:
                section_lines.append(f"  DROPS: {drops}")
            song_structure_text = "\n".join(section_lines)
        elif isinstance(song_structure, dict):
            song_structure_text = json.dumps(song_structure, indent=2)
        else:
            song_structure_text = str(song_structure)
    else:
        song_structure_text = edit_plan.get(
            "song_structure_hint",
            "(no song structure provided)"
        )

    # Compact shot list — include the fields the reviewer actually needs
    shots_raw = edit_plan.get("shots", edit_plan.get("shot_list", []))
    if not shots_raw and edit_plan.get("act_plan"):
        # Fall back: reconstruct a minimal shot list from the act plan
        for act in edit_plan.get("act_plan", []):
            shots_raw.extend(
                {"number": n, "slot": act.get("name", ""), "act": act.get("act", 1)}
                for n in act.get("shot_numbers", [])
            )

    compact_shots = [
        {
            "n": s.get("number", s.get("n", 0)),
            "slot": s.get("slot_name", s.get("slot", "")),
            "hero": (s.get("hero") or s.get("character_main") or "")[:30],
            "desc": (s.get("description") or s.get("desc") or "")[:100],
            "era": (s.get("era") or "")[:20],
            "mood": (s.get("mood") or s.get("emotion") or "")[:30],
            "t": round(float(s.get("song_time_sec", s.get("start_time", 0.0))), 2),
            "dur": round(float(s.get("duration_sec", s.get("duration", 2.0))), 2),
            "speaks": bool(s.get("character_speaks", False)),
        }
        for s in shots_raw[:120]  # cap at 120 shots to stay within context budget
    ]

    total_duration = sum(
        float(s.get("duration_sec", s.get("duration", 2.0))) for s in shots_raw
    )

    # Dialogue placements
    dialogue_raw = edit_plan.get("dialogue_placements", edit_plan.get("vo_placements", []))
    dialogue_text = (
        json.dumps(dialogue_raw[:30], indent=1)
        if dialogue_raw
        else "(no dialogue placements)"
    )

    # Style profile
    style_text = json.dumps(style_profile or {}, indent=1) if style_profile else "(default style)"

    user_prompt = _HOLISTIC_REVIEW_USER_TEMPLATE.format(
        template_name=template_name,
        template_description=template_description,
        template_slots=template_slots_text,
        song_structure=song_structure_text,
        shot_count=len(compact_shots),
        total_duration=total_duration,
        shot_list_json=json.dumps(compact_shots, indent=1),
        dialogue_placements=dialogue_text,
        style_profile=style_text,
    )

    try:
        client = OpenAI()
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": _HOLISTIC_REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        raw_text = response.choices[0].message.content or "{}"
    except Exception as exc:
        return DirectorReview(success=False, error=str(exc))

    # Parse the response
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return DirectorReview(
            success=False, error=f"GPT-4o returned invalid JSON: {exc}"
        )

    # Optionally persist raw JSON
    if output_json:
        json_path = Path(output_json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")

    # Map pacing_flags
    pacing_flags: list[PacingFlag] = []
    for pf in parsed.get("pacing_flags", []):
        pacing_flags.append(
            PacingFlag(
                section_name=str(pf.get("section_name", "")),
                time_range=[
                    float(t) for t in (pf.get("time_range") or [0.0, 0.0])
                ],
                issue=str(pf.get("issue", "")),
                severity=str(pf.get("severity", "moderate")),
            )
        )

    # Map act_transition_quality
    act_transitions: list[ActTransitionQuality] = []
    for at in parsed.get("act_transition_quality", []):
        act_transitions.append(
            ActTransitionQuality(
                from_act=str(at.get("from_act", "")),
                to_act=str(at.get("to_act", "")),
                quality=str(at.get("quality", "adequate")),
                notes=str(at.get("notes", "")),
            )
        )

    # Map revision suggestions
    revisions: list[RevisionSuggestion] = []
    for rev in parsed.get("specific_revision_suggestions", []):
        shot_nums_raw = rev.get("shot_numbers", [])
        if isinstance(shot_nums_raw, list):
            shot_nums = [int(n) for n in shot_nums_raw if str(n).lstrip("-").isdigit()]
        else:
            shot_nums = []
        revisions.append(
            RevisionSuggestion(
                priority=int(rev.get("priority", 99)),
                shot_numbers=shot_nums,
                action=str(rev.get("action", "replace")),
                description=str(rev.get("description", "")),
                impact=str(rev.get("impact", "")),
            )
        )
    revisions.sort(key=lambda r: r.priority)

    return DirectorReview(
        success=True,
        story_arc_score=float(parsed.get("story_arc_score", 0.0)),
        continuity_flags=[str(f) for f in parsed.get("continuity_flags", [])],
        pacing_flags=pacing_flags,
        act_transition_quality=act_transitions,
        climax_earned=bool(parsed.get("climax_earned", False)),
        climax_notes=str(parsed.get("climax_notes", "")),
        prose_review=str(parsed.get("prose_review", "")),
        specific_revision_suggestions=revisions,
        overall_readiness=str(parsed.get("overall_readiness", "needs_major_rework")),
    )


def critique_output(
    *,
    theme: str,
    qa_report: str,
    project_root: Path | str = ".",
) -> DirectorSuggestion:
    """Given a QA report, ask GPT for an actionable critique + fix list."""
    _load_env(project_root)

    try:
        from openai import OpenAI
    except ImportError:
        return DirectorSuggestion(success=False, error="OpenAI SDK not installed")

    import os
    if not os.environ.get("OPENAI_API_KEY"):
        return DirectorSuggestion(success=False, error="OPENAI_API_KEY not set")

    prompt = f"""THEME: {theme}

QA REPORT OF MY LATEST ROUGH CUT:
{qa_report}

As the Edit Strategist, tell me:
1. The TOP 3 issues to fix first (ordered by impact on viewer experience)
2. For each issue, the EXACT command/action to take
3. Whether this output is ready to import into an NLE (DaVinci Resolve) or needs pipeline rerun

Return JSON:
{{
  "top_issues": [
    {{"priority": 1, "issue": "...", "fix_command": "...", "impact": "high|medium|low"}}
  ],
  "ready_for_nle": true,
  "reasoning": "one paragraph"
}}
"""

    try:
        client = OpenAI()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": DIRECTOR_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        text = response.choices[0].message.content or "{}"
        return DirectorSuggestion(success=True, text=text)
    except Exception as exc:
        return DirectorSuggestion(success=False, error=str(exc))
