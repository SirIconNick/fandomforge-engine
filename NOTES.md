# FandomForge Engine — Standalone Copy

This is a separated-out copy of the FandomForge video engine, pulled from `~/Video Project/` on 2026-04-18 so it can live and grow as its own project in `~/Projects/`.

## What this folder is

The engine that plans, analyzes, and assembles multifandom video edits. It does NOT render the final video — the user takes the plan + beat map + shot list into their NLE (Resolve, Premiere, CapCut, Vegas) and builds the timeline there.

## What came over

```
fandomforge-engine/
├─ tools/fandomforge/         # THE ENGINE — main pipeline, assembly, intelligence, audio, video
│  ├─ master_pipeline.py      # top-level orchestrator
│  ├─ cli.py                  # `ff` command entry
│  ├─ config.py               # reads project-config.yaml
│  ├─ assembly/               # clip-to-timeline assembly
│  ├─ intelligence/           # shot scoring, QA gate, beat matching
│  ├─ audio/                  # beat detection, song analysis
│  ├─ video/                  # frame sampling, metadata
│  ├─ catalog/                # clip catalog
│  ├─ sources/                # source file handling
│  └─ assets/                 # engine-bundled assets
├─ tools/audio/               # audio helper scripts
├─ tools/catalog/             # catalog helper scripts
├─ tools/video/               # video helper scripts
├─ tools/tests/               # pytest suite
├─ tools/pyproject.toml       # Python package config
├─ agents/                    # the 10 domain experts (edit-strategist, beat-mapper, etc.)
├─ templates/                 # starter project templates — copy, don't edit originals
├─ scripts/                   # shell/python helpers (new-project.sh, markers-to-resolve.py, setup.sh)
├─ docs/                      # knowledge base (copyright, fair use, editing theory)
├─ examples/                  # example projects and outputs
├─ assets/                    # shared assets (LUTs, title templates, etc.)
├─ web/                       # Next.js 16 dashboard (port 4321)
├─ CLAUDE.md                  # project instructions for Claude Code
├─ README.md                  # project readme
├─ package.json               # pnpm workspace root
└─ pnpm-workspace.yaml
```

## What did NOT come over (and why)

- `projects/` — user's personal edit projects (Leon tribute, Dean, etc.). Those stay in `~/Video Project/projects/` because they're content, not engine code. If you extract more from those, copy manually.
- `node_modules/` — reinstall with `pnpm install` at the root.
- Python `__pycache__` / `.egg-info` — regenerated on install.

## Getting it running from scratch

### Python side (the actual engine)
```bash
cd ~/Projects/fandomforge-engine/tools
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Then the CLI is available as `ff`:
```bash
ff beat analyze <audio_path>
ff beat drops <audio_path>
ff video info <video_path>
ff catalog add <clip_path>
```

### Web dashboard (Next.js)
```bash
cd ~/Projects/fandomforge-engine
pnpm install
cd web
pnpm dev   # runs on port 4321
```

## How the engine is structured

The config-driven design means ANY character/fandom/song works from a single `project-config.yaml`. That generalization work finished in Stage 1-4 (see memory). The engine reads the config, runs the pipeline:

1. **Beat analysis** — audio → beat-map.json (timestamps, drops, energy)
2. **Shot scoring** — video sources → scored candidates (character presence, motion, sentiment)
3. **Assembly** — match shots to beats, build the edit
4. **QA gate** — mandatory — validates the edit before declaring success
5. **Render plan** — outputs timeline data for NLE import

## Important rules carried over from the parent project

- QA gate is MANDATORY, not advisory. Every pipeline change must keep QA gating the success flag.
- Always proof renders by sampling frames + audio. Never claim success off plan JSON alone.
- VQ single-frame sampling misses mid-clip issues — use 3-point luma + sandwich propagation.
- Narrative edits need literal VO-to-scene matching. Action-only edits get a pass.
- Dialogue shots (tagged via SRT overlap) need deprioritization and a 1.2s duration cap under unrelated VO.
- Hot-mastered songs need `song_gain_db` of -6 to -8. Older masters fine at -4.

## What to work on next

Pick up from the last known state. The engine is generalized and working. Likely next moves:
- More source-character combos (test generalization further)
- Better QA heuristics (fewer false positives/negatives)
- Timeline export refinements for specific NLEs
- Web dashboard feature parity with CLI

## Relationship to `~/Video Project/` (historical)

This project was originally extracted from `~/Video Project/` on 2026-04-18. As of 2026-04-19 this repo is the source of truth — the parent location is no longer being maintained. Edits go here; the `~/Video Project/` copy is kept only for the user's historical projects folder.

## One-liner context

FandomForge = multifandom video plan/research/assembly engine. Python + Next.js. Plans the edit, user renders it.
