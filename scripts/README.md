# FandomForge scripts

One-command shortcuts for the common dev workflows. Run from the repo root.

| Script | What it does |
|---|---|
| `scripts/setup.sh` | Full environment bootstrap — checks ffmpeg/node/pnpm, creates `.venv`, installs Python + web deps, copies agents into `.claude/agents/`, creates `web/.env.local` from the example, fetches legal test fixtures. |
| `scripts/verify-anthropic.sh` | Makes one ~40-token live call to the Anthropic API to confirm your key works and your account has credits. Prints model, tokens, and `reply`. |
| `scripts/dev.sh` | Starts the web dashboard on http://localhost:4321. Warns if `.env.local` is missing. |
| `scripts/smoke-test.sh` | Runs the full pytest suite (excluding known sandbox-hangs), vitest, typecheck, and production build. Exits non-zero on any failure. |
| `scripts/autopilot-demo.sh [slug]` | Scaffolds a demo project using a cached Incompetech fixture song, runs cost estimate, then runs the full autopilot DAG. Default slug is `demo-autopilot`. |
| `scripts/clean.sh` | Clears build artifacts and test caches. Does NOT touch your `projects/`, fixtures, or `.env.local`. |
| `scripts/new-project.sh` | (legacy) Interactive project-scaffolding wizard. |
| `scripts/markers-to-resolve.py` | (legacy) Helper for exporting markers to DaVinci Resolve. |

## Typical first-time flow

```bash
scripts/setup.sh                 # 2-3 minutes first time
# edit web/.env.local to add ANTHROPIC_API_KEY
scripts/verify-anthropic.sh      # should print "API LIVE."
scripts/autopilot-demo.sh        # end-to-end demo
scripts/dev.sh                   # open http://localhost:4321
```

## Typical verification flow (before a commit)

```bash
scripts/smoke-test.sh
```

## What the autopilot does

`ff autopilot --project <slug>` (which `scripts/autopilot-demo.sh` wraps) runs
an 11-step DAG:

1. scaffold project dirs
2. copy song into `assets/`
3. ingest every video in `raw/` (scenes + transcript + source-catalog)
4. `ff beat analyze` → `data/beat-map.json`
5. draft `data/edit-plan.json` — real `edit-strategist` LLM call via Anthropic when credits are available, heuristic fallback otherwise
6. propose shot list → `data/shot-list.json` + `shot-list.md` projection
7. infer emotion arc → `data/emotion-arc.json`
8. `ff qa gate` → pass/fail
9. `ff roughcut` → `exports/roughcut.mp4`
10. `ff color` → `exports/graded.mp4`
11. `ff export-nle` → `exports/<slug>.fcpxml`

Every step is idempotent — rerunning picks up from the last unfinished step.
Progress journals to `projects/<slug>/.history/autopilot.jsonl` and streams
live in the `/projects/<slug>/autopilot` web UI.

## Grabbing video or audio from a URL

Two CLI commands wrap yt-dlp, no domain gating, three modes:

```bash
# Video + audio, merged mp4 into projects/<slug>/raw/, then auto-ingest
ff grab video --project my-edit --url "https://www.youtube.com/watch?v=aqz-KE-bpKQ"

# Video only (silent mp4)
ff grab video --project my-edit --url "https://..." --no-audio

# Audio only (mp3 into assets/)
ff grab video --project my-edit --url "https://..." --audio-only
ff grab song  --project my-edit --url "https://incompetech.com/.../Sneaky%20Snitch.mp3"
```

Any yt-dlp-supported URL works. Each download writes a `.grab.json` sidecar
with the URL, mode, sha256, timestamp, and an optional `--note` for
attribution. Streaming services aren't blocked — if yt-dlp can reach it, the
tool will grab it.

The web dashboard has a "Grab from URL" panel on each project page — paste
the URL, pick mode (video+audio / video only / audio only), click Grab.
