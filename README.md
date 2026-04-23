# FandomForge

AI-powered multifandom video creation suite. Turn a song and a theme into a plan, a beat map, a shot list, a color strategy, a transition scheme — and an actual rendered MP4 + NLE-importable XML.

## What this actually is

A workspace that pairs **14 specialized AI experts**, a **Python analysis toolkit**, a **Next.js dashboard**, a **one-click auto-pilot**, and a **knowledge base** so you can go from prompt + song + source clips to a finished rough cut in minutes instead of a week.

Built for:
- Multifandom edits (Marvel + Harry Potter + Stranger Things all in one video)
- AMVs (anime music videos)
- Fan trailers and re-trailers
- Hype/action, emotional, beat-drop, and character-study edits

## The one command you probably want

```bash
ff autopilot --project my-edit --prompt "mentor-loss across Marvel, LOTR, Star Wars"
```

Assuming you've dropped a song into `projects/my-edit/assets/` and source clips into `projects/my-edit/raw/`, this runs the full 11-step DAG:

1. scaffold project dirs
2. copy song into `assets/`
3. ingest every video in `raw/` (scenes + transcript + catalog)
4. `ff beat analyze` → `data/beat-map.json`
5. draft `data/edit-plan.json` via the real edit-strategist LLM (Anthropic tool-use, schema-constrained; falls back to heuristic if no API key or credits)
6. propose `data/shot-list.json` + `shot-list.md`
7. infer `data/emotion-arc.json`
8. `ff qa gate` → pass/fail
9. `ff roughcut` → `exports/roughcut.mp4`
10. `ff color` → `exports/graded.mp4`
11. `ff export-nle` → `exports/<slug>.fcpxml` (Resolve / Premiere / FCP)

Every step is idempotent — rerunning picks up where it stopped. Progress streams to `projects/<slug>/.history/autopilot.jsonl` and the live web UI at `/projects/<slug>/autopilot`.

## The 14 experts

Every expert lives in `agents/` as a Claude Code subagent. The 10 core experts cover the creative layers:

| Expert | What they handle |
|---|---|
| **edit-strategist** | Master planner. Orchestrates the others. Start here. |
| **beat-mapper** | Audio analysis, BPM, drops, buildups, sync points |
| **story-weaver** | Narrative arc across multiple fandoms, theme coherence |
| **shot-curator** | Iconic moments, shot selection, visual matching |
| **color-grader** | LUTs, mood consistency, grading plan across sources |
| **transition-architect** | Whip pans, match cuts, flash cuts |
| **fandom-researcher** | Scene databases, character arcs, lore-accurate beats |
| **editor-guide** | Resolve / Premiere / CapCut / Vegas software playbooks |
| **audio-producer** | Song pick, mixing, SFX layering, buildup design |
| **title-designer** | Text overlays, kinetic titles, typography |

Plus four utility experts:

| Expert | What they handle |
|---|---|
| **pipeline-tuner** | ffmpeg params, speed-vs-quality tradeoffs before render |
| **qa-reviewer** | Post-pipeline quality gate, frame/audio/duration checks |
| **shot-proposer** | Blank-page fix — drafts a first-pass shot list from your edit-plan + beat-map |
| **autopilot-orchestrator** | Drives `ff autopilot` and reports progress honestly |

All 14 are also invocable inside the web dashboard's **expert chat** (text-based, propose structured JSON patches you review before applying) and **expert council** (ask 2-4 experts in parallel, see disagreements).

## Paste-link forensic UI (the training loop)

```bash
tools/.venv/bin/ff serve                          # http://127.0.0.1:4321
tools/.venv/bin/ff serve --host 0.0.0.0 --port 4321  # LAN-exposed (phone + laptop)
```

Paste any YouTube URL. FandomForge downloads it, runs the forensic pipeline (PySceneDetect + librosa/madmom + OpenCLIP + whisper), auto-tags the bucket + strengths/risks + craft techniques, and lets you correct anything it got wrong. Every correction writes to `.cache/ff/training/corrections.jsonl` and pulls that bucket's craft-weight profile 40% toward your values for every future render. Four-layer bias stack: table → forensic corpus (20%) → training journal (30%) → human corrections (40%). Full details in `docs/WEB_UI.md`.

**Autonomous mode:**

```bash
ff install-agent --interval 3600    # hourly launchd agent runs `ff auto`
ff uninstall-agent                  # remove
```

## Web dashboard

Runs on http://localhost:4321 (`scripts/dev.sh`).

- **Projects** — list, create, open. Each project page links to every artifact editor, rough preview, autopilot, and expert chat.
- **Artifact editors** — edit-plan, color-plan, transition-plan, audio-plan, title-plan, fandoms.json. Form view (schema-driven) or JSON view. Live validation. Atomic save with undo via the artifact journal.
- **Auto-pilot** (`/projects/<slug>/autopilot`) — one-click full pipeline, live progress, cost/time estimate.
- **Rough preview** (`/projects/<slug>/preview`) — plays shot list against song audio without running the render pipeline.
- **Expert chat** — talk to any of the 14 agents; their proposed patches render as reviewable diff cards.
- **Expert council** (`/experts/council`) — 2-4 experts in parallel, conflicts highlighted.
- **Grab from URL** — paste a YouTube / Vimeo / Archive.org / direct URL, pick video-only / audio-only / both, yt-dlp pulls it into the right folder with a `.grab.json` sidecar.
- **Usage dashboard** (`/usage`) — cache hit rate, per-expert token spend, recent turns.
- **Timeline editor** — shot-list visualization with keyboard shortcuts (arrows/J/L step, Home/End jump, Esc clear, ⌘Z rollback).

## Grabbing video / audio from any URL

```bash
# Video + audio into projects/<slug>/raw/ (default) + auto-ingest
ff grab video --project my-edit --url "https://www.youtube.com/watch?v=..."

# Video only, silent mp4
ff grab video --project my-edit --url "https://..." --no-audio

# Audio only (mp3), into projects/<slug>/assets/
ff grab video --project my-edit --url "https://..." --audio-only
# …or the shortcut, which does the same thing with --filename=song:
ff grab song --project my-edit --url "https://..."
```

Any yt-dlp-supported URL works — YouTube, Vimeo, Archive.org, direct media links, etc. No license gating at the download step. Every file gets a `.grab.json` sidecar with the URL, sha256, mode, and an optional `--note` for attribution. Credits still get generated at publish time via `ff credit generate`.

## Quick start

```bash
# One-shot bootstrap — deps, agents, .env.local, fixtures, smoke tests
scripts/setup.sh

# Add ANTHROPIC_API_KEY to web/.env.local
scripts/verify-anthropic.sh   # confirm it works

# Start the dashboard
scripts/dev.sh                # http://localhost:4321

# Or skip the UI and run autopilot directly
scripts/autopilot-demo.sh     # uses the cached Incompetech song from fixtures
```

## Folder map

```
.
├── agents/              Expert subagent definitions (14 agents)
├── .claude/agents/      Synced copies for Claude Code auto-discovery
├── docs/
│   ├── knowledge/       Deep knowledge base — techniques, theory, reference
│   ├── guides/          Step-by-step walkthroughs
│   └── meta/            Smoke-test runbooks, subagent launch log
├── tools/
│   ├── fandomforge/     Python package — CLI, pipeline, schemas, intelligence
│   └── tests/           pytest suite (170+ tests, real-media integration)
├── templates/
│   ├── edit-plans/      6 prebuilt structures (4-act-hype, mentor-loss, etc.)
│   └── fandoms.json     Example user-extensible fandom data
├── web/                 Next.js 16 dashboard
│   ├── src/app/         App Router pages + /api routes
│   └── src/components/  React components (ArtifactEditor, SchemaForm, etc.)
├── scripts/             One-shot wrappers (setup, dev, smoke-test, autopilot-demo)
├── integrations/        External consumers (Discord webhook example)
└── projects/            Your actual edit projects live here
```

## Typical workflow

```
song + sources in → ff autopilot → rough MP4 + graded MP4 + FCPXML out
                              ↓
    (or any intermediate step manually — the DAG is just orchestration)
```

For finer control: run each `ff` stage by hand, tweak artifacts in the web editors, ask experts for targeted advice via chat or council.

## Scripts

| Script | What it does |
|---|---|
| `scripts/setup.sh` | Bootstrap deps, copy agents, create .env.local, fetch fixtures, smoke |
| `scripts/verify-anthropic.sh` | Live API check — confirms your key has credits |
| `scripts/dev.sh` | Start web dashboard |
| `scripts/smoke-test.sh` | Full verification — pytest + vitest + typecheck + build |
| `scripts/autopilot-demo.sh [slug]` | Scaffold demo project, run full autopilot |
| `scripts/clean.sh` | Clear caches (preserves projects, fixtures, .env.local) |

See `scripts/README.md` for details.

## Documentation

- [Knowledge base](docs/knowledge/README.md) — deep reference on every technique
- [Guides](docs/guides/README.md) — step-by-step walkthroughs
- [Agents](agents/README.md) — full expert roster with tool allowlists
- [Scripts](scripts/README.md) — what each runnable wrapper does
- [Copyright & fair use](docs/knowledge/copyright-and-fair-use.md) — read before publishing
- [Meta: smoke test + subagent launch log](docs/meta/) — verification records

## Requirements

- Python 3.13+
- Node 24+ and pnpm
- ffmpeg and yt-dlp (`brew install ffmpeg yt-dlp`)
- An NLE: DaVinci Resolve (free), Premiere Pro, CapCut, or Vegas
- Claude Code CLI (for the subagents)
- An Anthropic API key with credits (for expert chat + LLM edit-strategist in autopilot)
