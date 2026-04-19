# FandomForge Web Dashboard

Next.js 16 App Router dashboard for managing your multifandom edit projects.

## Dev

```bash
cd web
pnpm install
cp .env.local.example .env.local  # edit with real values
pnpm dev
```

Runs on http://localhost:4321.

## Environment

See `.env.local.example` for the full list. Minimum required:

- `ANTHROPIC_API_KEY` — needed for expert chat. Without it every other page still works but the chat returns a clear error.

## What it does

- **Home** — overview of projects, experts, and knowledge base.
- **Projects** — your current edits, with edit plans / shot lists / beat maps visualized. Each project has per-artifact editors for edit-plan, color-plan, transition-plan, audio-plan, and title-plan, each with live schema validation, atomic save, and per-artifact undo.
- **Expert chat** — 12 specialized agents you can talk to. Experts can propose structured JSON patches to any artifact; patches land in a review card with per-op checkboxes, schema validation, and a rationale. Nothing is written without your approval. A handful of experts (beat-mapper, pipeline-tuner, qa-reviewer, shot-curator) can also propose allowlisted CLI runs that execute with confirmation and stream output into the chat.
- **Timeline editor** — read-only visual view of the shot list with keyboard shortcuts (arrow keys / J / L to step, Home/End to jump, ⌘Z to rollback the last shot-list change through the artifact journal).
- **Pipeline runner** — long-running pipeline jobs with log streaming.
- **Knowledge** — browse the knowledge base.
- **Beat Map** — visualize any project's beat-map.json with drops, buildups, energy curve.

## Stack

- Next.js 16 App Router
- React 19
- TypeScript strict mode
- Tailwind CSS 4
- `@anthropic-ai/sdk` — expert chat, tool use, prompt caching
- `ajv` + `ajv-formats` — JSON Schema 2020-12 validation shared with the Python CLI
- `fast-json-patch` — RFC 6902 patch operations on artifacts
- `marked` + `isomorphic-dompurify` — safe markdown rendering
- `gray-matter` — agent frontmatter parsing

## Schema source of truth

All artifacts validate against the JSON Schema files in `../tools/fandomforge/schemas/`. The Python CLI and the web dashboard both read the same files — no codegen, no zod mirror, no drift. See `src/lib/schemas.ts` for the Ajv side.

## The artifact journal

Every mutation (expert patch, manual edit, rollback) appends to `projects/<slug>/.history/<artifact>.jsonl`. Each line captures the timestamp, the expert that proposed it, the rationale, the before/after SHA-256, and the applied RFC 6902 ops. The `/api/artifacts/rollback` route walks this journal in reverse to build inverse patches — undo is just "apply the inverse patch as a new journal entry," so undo-of-undo works for free.

## Reading data

The dashboard reads directly from the filesystem:

- `../agents/*.md` — expert agents
- `../docs/knowledge/*.md` — knowledge docs
- `../projects/<slug>/` — per-project data
- `../tools/fandomforge/schemas/*.schema.json` — artifact schemas

No database. No backend. The filesystem is the source of truth. This matches the workflow: edit files in your editor, view them in the dashboard, let the experts propose patches that land atomically back on disk.

## Build

```bash
pnpm build
pnpm start
```

## Tests

```bash
pnpm test         # unit + integration (vitest)
pnpm test:e2e     # end-to-end (playwright)
pnpm typecheck    # tsc --noEmit
```
