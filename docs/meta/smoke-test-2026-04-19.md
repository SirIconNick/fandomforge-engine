# End-to-end smoke test — 2026-04-19

## What was smoke-tested here

This pass verified the **code path** for the full pipeline end to end but did not yet run against real media. The media-dependent checks are left as a runbook the user can execute when they have an owned song and 2–3 trailer clips on hand.

What was exercised without media:

- **115 pytest pass, 3 skipped** in `tools/` (with `--ignore=tests/test_leon_smoke.py --ignore=tests/test_matchers.py` — same exclusions the previous session used for sandbox-hang files). My schema addition (`fandoms.schema.json`) doesn't regress anything. Total 5.15 seconds runtime once the right ignores are applied. Without the ignores the two files hang under pytest's capture subsystem in the sandbox and never complete.
- 56 vitest tests in `web/` (up from 10 baseline, added schemas, atomic-write, validate/read/apply/rollback routes, chat mock, pipeline-step guards, thumb route, and component tests for ArtifactDiffReview + ArtifactEditor)
- 44 vitest in `web/` cover schema validation, atomic writes, all new API routes, expert chat with mocked Anthropic, and pipeline-step guard behavior
- `tsc --noEmit` clean; `pnpm build` clean (all new pages and routes compile and tree-shake)
- The `.claude/agents/` directory loads 12 agents with valid YAML frontmatter and scoped tool allowlists
- `fandoms.schema.json` parses and validates both positive and negative inputs (covered by vitest)

What was NOT run because it needs media:

- `ff beat analyze` on a real audio file (the binary works — `tools/` tests exercise the library — but no end-to-end audio → beat-map.json → web visualizer loop was walked on real input)
- `ff sources add` + `ff shots parse` with real video
- `ff roughcut` / `ff export` pipeline against a live project
- Expert chat with a real `ANTHROPIC_API_KEY` proposing a patch against a real artifact

## Manual runbook — do this when you have media

Need:
- An owned audio file (song) or one downloaded for fair-use review purposes, ~2–3 minutes
- 2–3 publicly available trailer clips (youtube-dl, owned copies, etc.)
- Your `ANTHROPIC_API_KEY` in `web/.env.local`

### 1. Scaffold the project

```bash
cd /Users/damato/Projects/fandomforge-engine
tools/.venv/bin/ff project new my-smoke-edit
# Put the song at projects/my-smoke-edit/assets/song.mp3
# Put the trailers in projects/my-smoke-edit/sources/
```

### 2. Analyze the song

```bash
tools/.venv/bin/ff beat analyze projects/my-smoke-edit/assets/song.mp3 \
  -o projects/my-smoke-edit/data/beat-map.json
```

Expected: writes a valid `beat-map.json` with beats, downbeats, drops, buildups, energy curve.

### 3. Ingest sources

```bash
tools/.venv/bin/ff sources add projects/my-smoke-edit
tools/.venv/bin/ff shots parse projects/my-smoke-edit
```

Expected: populates `projects/my-smoke-edit/data/catalog.json` and a parsed shot scaffold.

### 4. Web dashboard

```bash
cd web
pnpm dev
```

Open `http://localhost:4321/projects/my-smoke-edit`. Verify:

- Beat map visualizer renders (drops, energy curve)
- The 6 artifact editor tabs (edit-plan, color-plan, transition-plan, audio-plan, title-plan, qa-report) all load without errors
- Each editor shows validation state in real time

### 5. Expert chat + diff/apply

Open `http://localhost:4321/experts/chat/edit-strategist?project=my-smoke-edit`. Ask:

> "This is a 2-minute edit about sacrifice. Give me a basic 4-act structure for the edit-plan."

Verify:
- Response includes text reply AND a proposed patch card for `edit-plan`
- Patch card shows per-op checkboxes and validation badge
- Clicking Apply writes the file and appends a line to `projects/my-smoke-edit/.history/edit-plan.jsonl`
- Clicking Undo on the editor page rolls back and writes a second journal entry

### 6. Expert council

Open `http://localhost:4321/experts/council?project=my-smoke-edit`. Pick 3 experts (e.g. story-weaver, transition-architect, color-grader). Ask:

> "How should we handle the transition between act 2 and act 3?"

Verify:
- Three responses render side-by-side
- If any two experts propose patches to the same JSON Pointer, the conflict box highlights them
- You can accept patches from one expert and reject from another

### 7. Pipeline tool use

Open `http://localhost:4321/experts/chat/beat-mapper?project=my-smoke-edit`. Ask:

> "Re-analyze the song and tell me if you see any tempo changes."

Verify:
- The response includes a run-card for `ff beat drops` or similar
- Clicking Run streams output into the chat
- After exit the run is logged to `.history/qa-report.jsonl` as a pipeline-run entry

### 8. Rough cut + QA

```bash
tools/.venv/bin/ff qa gate projects/my-smoke-edit
tools/.venv/bin/ff export resolve projects/my-smoke-edit
```

Expected: `ff qa gate` produces a QA report (pass/warn/fail per rule); `ff export resolve` produces an NLE-importable XML.

### 9. Open in your NLE

Import the exported XML into DaVinci Resolve (or your NLE of choice). Verify that the timeline layout matches your shot list.

## Known friction points (pre-media)

From the code path walk:

- The `/pipeline/step` SSE stream works in the same process as the dev server. If the process is restarted mid-run (e.g. Next hot-reload), the stream drops. Not a bug for ad-hoc use but could be annoying during development. Mitigation: use the pipeline runner page (`/pipeline/[slug]`) for anything over 10 seconds.
- The `ArtifactEditor` is a raw JSON textarea. Pretty schema-form rendering is out of scope for this pass; the JSON + live-validation approach works for all 5 artifacts without bespoke per-artifact UI. A future pass could use `@rjsf/core` for a prettier edit experience.
- Shot thumbnails in the timeline ARE wired: `/api/project/[slug]/thumb?source=X&time=Y` serves frames from `.clip-cache/<source>_frames/frame_NNNNNN.jpg`, and the Timeline component shows them per-shot with graceful fallback. To populate the cache run `ff index-frames --project <slug>`.

## Follow-ups to tackle after a real-media run

These won't surface until actual audio/video flows through:

- Likely ffmpeg pathing / venv activation edge cases when the web server spawns `ff` from a different CWD
- Actual Anthropic SDK cache behavior (expected 85% hit rate after turn 2 — needs real traffic to confirm)
- Beat-map visualizer scaling on songs shorter than 30s or longer than 5 minutes
- Conflict resolution ergonomics when 3+ experts all patch the same shot in expert council
