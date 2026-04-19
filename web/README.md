# FandomForge Web Dashboard

Next.js 16 App Router dashboard for managing your multifandom edit projects.

## Dev

```bash
cd web
pnpm install
pnpm dev
```

Runs on http://localhost:4321.

## What it does

- **Home** — overview of projects, experts, and knowledge base
- **Projects** — your current edits, with edit plans / shot lists / beat maps visualized
- **Experts** — read agent definitions without opening files
- **Knowledge** — browse the knowledge base
- **Beat Map** — visualize any project's beat-map.json with drops, buildups, energy curve

## Stack

- Next.js 16 App Router
- React 19
- TypeScript strict mode
- Tailwind CSS 4
- `marked` + `isomorphic-dompurify` for safe markdown rendering
- `gray-matter` for agent frontmatter parsing

## Reading data

The dashboard reads directly from the filesystem:

- `../agents/*.md` — expert agents
- `../docs/knowledge/*.md` — knowledge docs
- `../projects/<slug>/` — per-project data

No database. No backend. The filesystem is the source of truth. This matches the workflow: edit files in your editor, view them in the dashboard.

## Build

```bash
pnpm build
pnpm start
```
