# Using the Experts

Ten experts, each owning one dimension of multifandom editing. Here's how to work with them effectively.

## The handoff model

Experts pass work to each other. You, the user, are the orchestrator. When you invoke one, they tell you which others to call next.

Typical flow:

```
edit-strategist  (master plan)
   ↓
beat-mapper      (timing skeleton)
   ↓
story-weaver     (theme arc)
   ↓
fandom-researcher → shot-curator  (content)
   ↓
color-grader + transition-architect + audio-producer + title-designer  (treatment)
   ↓
editor-guide     (execute in NLE)
```

## Invoking in Claude Code

Inside this project in Claude Code:

```
@edit-strategist [your question or request]
```

The agent file in `agents/<name>.md` loads automatically, giving the agent its domain-specific instructions, tone, and rules.

## When to use which

### Starting anything new? → edit-strategist
Every edit starts here. Tells you structure, archetype, delegation plan.

### Need timing / BPM / drop detection? → beat-mapper
Before you cut a single clip, your beat map needs to be real (not guessed).

### Theme feels weak? → story-weaver
Flat edit, unclear point, "why is this Marvel clip here" — story-weaver owns it.

### Don't know what scenes exist? → fandom-researcher
Confirm-or-deny whether a specific scene exists. Gets timestamps.

### Have candidates, need to pick? → shot-curator
Runs the 4-criterion filter, cuts weak shots, verifies 40% rule and visual matching.

### Sources look mismatched? → color-grader
Unified grade / source-proud / section-graded — picks strategy + per-source adjustments.

### Transitions feel choppy or boring? → transition-architect
Designs transition language, picks cut types per section.

### No song picked, or mix feels weak? → audio-producer
Recommends songs, designs SFX layers, handles loudness.

### Need text decisions? → title-designer
Usually the answer is "no text" — title-designer tells you when it IS earned and how to execute.

### How-to in your specific NLE? → editor-guide
Project settings, keyboard shortcuts, node trees, export settings.

## Brief them well

Each expert works better with context. Always pass them:

- The theme (one sentence)
- The relevant section of your plan
- Specific question or task

Don't make them re-derive things. If edit-strategist already drafted a plan, paste it when calling story-weaver. If beat-mapper already analyzed, reference the beat-map.json path.

## When experts disagree

They shouldn't, but if they do:

- Structure disagreement → edit-strategist wins
- Theme / narrative disagreement → story-weaver wins
- Timing disagreement → beat-mapper wins
- Anything else → ask edit-strategist to arbitrate

## When they push back

Experts are opinionated. They'll reject ideas that don't serve the theme or archetype. Don't fight this. If shot-curator says your favorite clip doesn't fit, trust the system and find a replacement.

If you really want to override an expert, name the override:

> "I know shot-curator flagged this as off-theme, but I want to include it because [reason]. Acknowledge the trade-off and continue."

Explicit overrides beat silent arguments.

## Saving their outputs

Each expert returns structured markdown / JSON. Save their outputs into your project:

- edit-strategist → `projects/<slug>/edit-plan.md`
- beat-mapper → `projects/<slug>/beat-map.json` + `beat-map.md`
- story-weaver → into the act sections of `edit-plan.md`
- shot-curator → `projects/<slug>/shot-list.md`
- color-grader → `projects/<slug>/color-plan.md` (create this)
- transition-architect → `projects/<slug>/transition-plan.md` (create this)
- audio-producer → `projects/<slug>/audio-plan.md` (create this)
- title-designer → `projects/<slug>/text-plan.md` (create this) or skip if no text
- editor-guide → `projects/<slug>/nle-notes.md` (create this)

## The expert personalities

Each has a distinct voice. Don't try to change it.

- **edit-strategist** — decisive director. Says no. Ends with next action.
- **beat-mapper** — clinical, numerical. Backs every recommendation with a timestamp.
- **story-weaver** — film school story editor. Asks "why" a lot. Rejects clichés.
- **shot-curator** — film editor at an assembly cut. Says exact timestamps.
- **color-grader** — colorist. Names specific values. Cares about scopes.
- **transition-architect** — assistant editor who watched 10,000 music videos. Says "frames" not "seconds."
- **fandom-researcher** — encyclopedia with opinions. Specific, never fabricates.
- **editor-guide** — tech lead. Exact menu paths and shortcuts.
- **audio-producer** — music producer. dB values and LUFS. Opinionated.
- **title-designer** — graphic designer. Talks tracking and weight. Pushes back on unnecessary text.

These voices are intentional. They produce specific, opinionated output — which is exactly what you want from a specialist.

## What experts won't do

- Violate copyright for you
- Make up scenes that don't exist
- Agree with bad ideas just to be agreeable
- Generate video content (they plan; you edit)
- Guarantee anything about your fandom's trending status — they know what's current as of their training data

## Extending

Want to add an 11th expert (say, a vertical-cutdown specialist for reframing horizontal edits to vertical)?

1. Create `agents/vertical-cutdown-specialist.md` with the frontmatter format
2. Write the system prompt following the existing experts' pattern
3. Add it to the README and handoff map
4. Re-symlink to `.claude/agents/`

The pattern is: **single responsibility, clear delegation, structured output, specific tone, opinionated rules.**
