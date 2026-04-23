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

## Paste-link forensic UI (`ff serve`)

Separate from the Next.js dashboard — FastAPI app at `tools/fandomforge/web/` that runs via `tools/.venv/bin/ff serve` (default port 4321, same port as the dashboard so pick one or use `--port`). Paste-link workflow:

1. User pastes a YouTube URL
2. `/api/analyze` downloads via yt-dlp, runs `deconstruct_video`, returns job_id (or reuses cached forensic)
3. UI polls `/api/job/<id>` until status=done
4. User sees auto-tagged bucket + craft analysis + inline video preview
5. User corrects via `/api/correct` — bucket, tags, craft-weight sliders, notes
6. Correction writes to `.cache/ff/training/corrections.jsonl` and pulls that bucket's craft-weight profile 40% toward the user's values for every future render

Four-layer bias stack: table → forensic corpus (20%) → training journal (30%) → human corrections (40%). Each layer has an env toggle (`FF_FORENSIC_BIAS`, `FF_TRAINING_BIAS`, `FF_CORRECTIONS_BIAS`).

Never bypass the JobStore — the pipeline is slow (minutes) and the client polls. Never inject raw HTML into the UI; all dynamic content uses DOM-building in `app.js` via the `el()` helper. Full details in `docs/WEB_UI.md`.

## Autonomous mode

`ff install-agent --interval 3600` registers a macOS launchd agent that runs `ff auto --limit 2` every hour. Each run: ingest new corpus URLs → re-mine bucket priors → synthesize bucket-reports → bootstrap training journal → legacy-priors migration. Logs at `.cache/ff/auto.log` (auto-rotated at 5MB, keeps 5 archives). `ff uninstall-agent` to remove.

## Rules specific to this project

- **Grab is unrestricted.** `ff grab video` and `ff grab song` pull from any yt-dlp-supported URL without license gating. Nick decides what's fair game on a per-project basis — the tool doesn't police it.
- **Credits still get generated.** `ff credit generate` writes `credits.md` with song + source attribution at publish time. Copyright awareness lives there, not at the download step.
- **Every edit plan should include the song credit and source disclosure** when publishing. `credits.md` covers this automatically from the edit plan and source catalog.
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

- Don't claim fair use blankets in generated credits — fair use is case-by-case, flag uncertainty when it matters.
- Don't invent fandom scenes — if the fandom-researcher doesn't know, say so and ask the user.
- Don't skip the beat map. It's the foundation of everything.
