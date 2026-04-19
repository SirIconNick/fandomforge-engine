# Expert Agents

Twelve specialized agents that handle every dimension of multifandom video creation. Each lives in its own markdown file with frontmatter that Claude Code reads to understand when and how to invoke them.

## Installation

The project ships with these agents pre-installed at `/Users/damato/Projects/fandomforge-engine/.claude/agents/`. When you open Claude Code in this repo they auto-register and can be invoked via the Agent tool.

If you ever edit the sources in `agents/` and want to re-sync them into the active location:

```bash
# From the project root
cp agents/*.md .claude/agents/
# README.md is never copied since it would be parsed as a bogus agent
```

We use `cp` not `ln -s` because symlinks break if the project moves, and because having real files under `.claude/agents/` means the whole repo is portable without post-clone setup.

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
| [pipeline-tuner](pipeline-tuner.md) | ffmpeg params, preset choice, speed vs quality | Before a big render, or diagnosing a disappointing run |
| [qa-reviewer](qa-reviewer.md) | Post-pipeline quality gate, frame/audio/duration checks | After a rough cut completes, before shipping |

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
      ├─→ editor-guide  (execute in NLE)
      ├─→ pipeline-tuner  (tune the render flags)
      └─→ qa-reviewer  (check the rough cut before ship)
```

Almost every project starts with edit-strategist and beat-mapper. Pipeline-tuner and qa-reviewer come at the end of the chain when you're about to render or ship.

## Tool allowlists

Each agent gets a scoped tool set rather than the full suite, so you get fewer approval prompts and less risk of an agent taking an unrelated action. Summary:

| Agent | Tools |
|---|---|
| edit-strategist | Read, Write, Glob, Grep, WebSearch |
| beat-mapper | Bash, Read, Write, Glob |
| story-weaver | Read, Write, Glob, WebSearch |
| shot-curator | Read, Write, Glob, Grep |
| color-grader | Read, Write, Glob |
| transition-architect | Read, Write, Glob |
| fandom-researcher | Read, Glob, Grep, WebSearch |
| editor-guide | Read, Glob, WebSearch |
| audio-producer | Read, Write, Glob |
| title-designer | Read, Write, Glob |
| pipeline-tuner | Bash, Read, Write, Glob |
| qa-reviewer | Bash, Read, Glob, Grep |

Bash is only granted to the three agents that actually run CLI commands (beat-mapper, pipeline-tuner, qa-reviewer). Everyone else stays file-and-research only.

## Adding new experts

1. Create `agents/<name>.md` with the frontmatter block:

```yaml
---
name: your-agent-name
description: One paragraph describing what this agent does, when to use it, and 2-3 example invocations.
model: sonnet
color: your-color
tools:
  - Read
  - Write
---
```

2. Write the system prompt below the frontmatter — start with a clear statement of what the agent is and what it owns.
3. Sync to the active location: `cp agents/*.md .claude/agents/`

## Expert design principles

Every agent here follows these rules:

- **Single responsibility.** One domain, done well. Beat-mapper doesn't do color.
- **Clear delegation.** Each agent knows which other agents to hand off to.
- **Structured output.** They return markdown/JSON artifacts the next agent can consume.
- **Specific tone.** Each has a voice (colorist, producer, researcher). Not generic AI assistant.
- **Opinionated.** They reject bad ideas. They say no. They name anti-patterns.
- **Tight tool access.** Each agent's allowlist covers what it needs and nothing more.
