# FandomForge

AI-powered multifandom video creation suite. Turn a song and a theme into a plan, a beat map, a shot list, a color strategy, and a transition scheme — without fighting a blank timeline.

## What this actually is

A workspace that pairs **10 specialized AI experts**, a **Python analysis toolkit**, a **Next.js dashboard**, a **knowledge base**, and **reusable templates** so you can go from raw song to export-ready edit plan in an afternoon instead of a week of flailing.

Built for people who make:
- Multifandom edits (Marvel + Harry Potter + Stranger Things all in one video)
- AMVs (anime music videos)
- Fan trailers and re-trailers
- Hype/action edits for specific franchises
- Emotional, character-study edits
- Beat-drop chaos edits that hit like a truck

## The 10 experts

Each expert lives in `agents/` and can be invoked via Claude Code. They do one job and do it well.

| Expert | What they handle |
|---|---|
| **edit-strategist** | Master planner. Orchestrates the others. Start here. |
| **beat-mapper** | Audio analysis, BPM, drops, buildups, sync points |
| **story-weaver** | Narrative arc across multiple fandoms, theme coherence |
| **shot-curator** | Iconic moments, shot selection, visual matching |
| **color-grader** | LUTs, mood consistency, grading plan across sources |
| **transition-architect** | Whip pans, match cuts, flash cuts, when to use which |
| **fandom-researcher** | Scene databases, character arcs, lore-accurate beats |
| **editor-guide** | Resolve / Premiere / CapCut / Vegas software playbooks |
| **audio-producer** | Song pick, mixing, SFX layering, buildup design |
| **title-designer** | Text overlays, kinetic titles, typography choices |

## Folder map

```
.
├── agents/              Expert agent definitions (the 10 experts)
├── docs/
│   ├── knowledge/       Deep knowledge base — techniques, theory, reference
│   └── guides/          Step-by-step how-to walkthroughs
├── tools/
│   ├── audio/           Beat detection, BPM, drop finder (Python)
│   ├── video/           Video metadata, keyframe extraction
│   └── catalog/         Clip catalog management
├── templates/
│   ├── edit-plan/       The master edit plan document template
│   ├── shot-list/       Shot list with timing, source, mood tags
│   └── beat-map/        Beat map with drops, buildups, energy curve
├── examples/            Fully filled-out example projects
├── projects/            Your actual edit projects live here
├── web/                 Next.js 16 dashboard (beat mapping UI, expert chat)
├── scripts/             Helper scripts (setup, new project, export)
└── assets/              LUTs, SFX, font samples, reference stills
```

## Quick start

### 1. Python tools setup

```bash
cd "/Users/damato/Video Project"
python3 -m venv .venv
source .venv/bin/activate
pip install -e tools/
```

This installs librosa, numpy, ffmpeg-python, and the FandomForge CLI.

### 2. Web dashboard

```bash
cd web
pnpm install
pnpm dev
```

Open http://localhost:4321 (unique port so nothing collides).

### 3. Hook up the experts in Claude Code

The agent files in `agents/` are project-local. To make Claude Code pick them up, symlink or copy them into `.claude/agents/` inside this project:

```bash
mkdir -p ~/.claude/projects-agents/fandomforge
cp agents/*.md ~/.claude/projects-agents/fandomforge/
```

Then invoke any expert by name in Claude Code: `@beat-mapper`, `@story-weaver`, etc.

### 4. Start a project

```bash
./scripts/new-project.sh "marvel-stranger-sacrifice-edit"
```

Drops a fully-templated folder into `projects/` with beat map, shot list, and edit plan ready to fill in.

## Typical workflow

```
song in → beat-mapper analyzes → edit-strategist drafts structure
   ↓
story-weaver builds theme arc across your chosen fandoms
   ↓
shot-curator + fandom-researcher compile shot list with timestamps
   ↓
transition-architect + color-grader plan the visual language
   ↓
editor-guide turns the plan into a concrete timeline for your NLE
   ↓
audio-producer finalizes the audio mix + SFX
   ↓
title-designer adds text/title treatments
   ↓
export, upload, watch the views roll in
```

## Documentation

- [Knowledge base](docs/knowledge/README.md) — deep reference on every technique
- [Guides](docs/guides/README.md) — step-by-step walkthroughs
- [Agents](agents/README.md) — expert agent reference
- [Tools](tools/README.md) — Python CLI reference
- [Copyright & fair use](docs/knowledge/copyright-and-fair-use.md) — what you need to know before publishing

## Requirements

- Python 3.13+
- Node 24+ and pnpm
- ffmpeg (brew install ffmpeg)
- An NLE: DaVinci Resolve (free), Premiere Pro, CapCut, or Vegas
- Claude Code CLI (for the expert agents)
