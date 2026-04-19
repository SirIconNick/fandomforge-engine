# FandomForge — Project Instructions for Claude Code

This is the multifandom video creation suite. When working in this project, follow these guidelines.

## Project scope

FandomForge helps the user plan, research, and execute multifandom video edits — including autonomous end-to-end rendering. The pipeline runs from `ff autopilot` (scaffold → beat analyze → edit plan → shot list → emotion arc → QA gate → assembly → color → export) or the user can run each stage manually, or finish the final cut in their NLE of choice (Resolve / Premiere / CapCut / Vegas).

## Core mental model

A multifandom edit is:
1. **A song** (the skeleton — drives timing, energy, emotion)
2. **A theme** (the spine — why these clips together)
3. **A shot list** (the flesh — actual moments from actual sources)
4. **A visual language** (the skin — color, transitions, titles, pacing)
5. **An NLE project** (the output — timeline the user builds from the plan)

Everything we do serves one of those five layers.

## The 12 experts

All expert definitions live in `agents/` and are registered as Claude Code subagents in `.claude/agents/`. When the user asks for something specific to an expert's domain, either answer in that expert's voice/style or delegate via the Agent tool.

The 10 core experts cover the creative layers of an edit:

- **edit-strategist** — master orchestrator, project planning
- **beat-mapper** — audio analysis, timing
- **story-weaver** — narrative, theme, arc
- **shot-curator** — shot selection, iconic moments
- **color-grader** — color consistency, LUTs, mood
- **transition-architect** — transition types, flow
- **fandom-researcher** — fandom knowledge, scene databases
- **editor-guide** — NLE-specific software help
- **audio-producer** — song, mix, SFX
- **title-designer** — text, kinetic type

Plus two experts who handle the render and ship stages:

- **pipeline-tuner** — ffmpeg params, speed-vs-quality tradeoffs before a big render
- **qa-reviewer** — post-pipeline quality gate before shipping the rough cut

## Working with projects

User edit projects live in `projects/<slug>/`. Each project has:
- `edit-plan.md` — overall plan (theme, song, structure)
- `beat-map.json` — timestamps of beats, drops, key moments
- `shot-list.md` — every shot with source, timestamp, mood, sync target
- `color-plan.md` — LUTs, grade notes per source
- `transition-plan.md` — transition between each section
- `export-notes.md` — final render settings, NLE notes

Always work inside one of these files or the project's own folder. Never touch another project's files.

## Python tooling

CLI tools live in `tools/`. The main entry is `tools/cli.py` with subcommands:
- `ff beat analyze <audio>` — full beat map
- `ff beat drops <audio>` — drop detection only
- `ff video info <path>` — metadata + duration
- `ff catalog add <clip>` — add a clip to the catalog

Use `uv` or `pip` for deps. Activate venv before running.

## Web dashboard

Next.js 16 App Router, TypeScript strict, Tailwind, shadcn/ui. Runs on port 4321. Provides visual beat mapping, project browser, and an expert chat interface.

## Rules specific to this project

- **Never rip video from streaming services.** Instruct the user to use legally owned copies or publicly available trailers.
- **Always remind about copyright when publishing.** See `docs/knowledge/copyright-and-fair-use.md`.
- **Every edit plan must include the song credit and source disclosure.** Non-negotiable for ethical publishing.
- **Beat maps are authoritative.** If the shot list conflicts with the beat map, fix the shot list.
- **Templates live in `templates/`** — copy, don't edit the originals.

## When the user says "help me make a video"

Walk them through this sequence:
1. What's the theme in one sentence?
2. What song?
3. What fandoms? How many?
4. What's the vibe? (action / emotional / hype / sad / funny / mixed)
5. What length? (30s short / 1-2min full edit / 3-5min long form)
6. Then kick it to **edit-strategist** to draft a plan.

## What not to do

- Don't scrape copyrighted content.
- Don't claim fair use blankets — fair use is case-by-case, always flag uncertainty.
- Don't invent fandom scenes — if the fandom-researcher doesn't know, say so and ask the user.
- Don't skip the beat map. It's the foundation of everything.
