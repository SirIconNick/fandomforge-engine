# Expert Agents

Ten specialized agents that handle every dimension of multifandom video creation. Each lives in its own markdown file with frontmatter that Claude Code reads to understand when and how to invoke them.

## Installation

To make these agents available in Claude Code, symlink or copy them into `.claude/agents/` for this project:

```bash
# From the project root
mkdir -p .claude/agents
ln -sf "$PWD"/agents/*.md .claude/agents/
```

Or globally for all projects:

```bash
cp agents/*.md ~/.claude/agents/
```

## The roster

| Agent | Owns | Call when |
|---|---|---|
| [edit-strategist](edit-strategist.md) | Structure, act breakdown, project orchestration | Starting a new edit, a stuck edit, need a master plan |
| [beat-mapper](beat-mapper.md) | Audio analysis, BPM, drops, sync points | You have a song and need timing |
| [story-weaver](story-weaver.md) | Theme, narrative arc, character parallels | Edit feels hollow, need coherence across fandoms |
| [shot-curator](shot-curator.md) | Shot selection, visual matching, iconic moments | You have a plan and need actual clips |
| [color-grader](color-grader.md) | Color consistency, LUTs, per-source adjustments | Sources look mismatched |
| [transition-architect](transition-architect.md) | Cut design, match cuts, whip pans, flash cuts | Edit feels choppy, designing cut language |
| [fandom-researcher](fandom-researcher.md) | Scene database across major fandoms | "Does [fandom] have a scene where...?" |
| [editor-guide](editor-guide.md) | Software playbooks (Resolve / Premiere / CapCut / Vegas) | Translating plan to NLE steps |
| [audio-producer](audio-producer.md) | Song selection, SFX layers, final mix | Need a song, mix feels weak, loudness issues |
| [title-designer](title-designer.md) | Typography, title cards, on-screen text | Deciding about text, font choices, kinetic type |

## How they work together

```
edit-strategist  (you start here — master plan)
      ├─→ beat-mapper  (song → timing)
      ├─→ story-weaver  (theme + arc)
      │      └─→ fandom-researcher  (find scenes for theme)
      │             └─→ shot-curator  (build shot list)
      ├─→ color-grader  (visual unification)
      ├─→ transition-architect  (cut language)
      ├─→ audio-producer  (song + SFX layers)
      ├─→ title-designer  (text, if any)
      └─→ editor-guide  (execute in NLE)
```

Almost every project starts with edit-strategist and beat-mapper. Everything else branches from there.

## Adding new experts

To add an expert:

1. Create `agents/<name>.md` with the frontmatter block:

```yaml
---
name: your-agent-name
description: One paragraph describing what this agent does, when to use it, and 2-3 example invocations.
model: sonnet
color: your-color
---
```

2. Write the system prompt below the frontmatter — start with a clear statement of what the agent is and what it owns.
3. Re-symlink if you used the symlink install method.

## Expert design principles

Every agent here follows these rules:

- **Single responsibility.** One domain, done well. Beat-mapper doesn't do color.
- **Clear delegation.** Each agent knows which other agents to hand off to.
- **Structured output.** They return markdown/JSON artifacts the next agent can consume.
- **Specific tone.** Each has a voice (colorist, producer, researcher). Not generic AI assistant.
- **Opinionated.** They reject bad ideas. They say no. They name anti-patterns.
