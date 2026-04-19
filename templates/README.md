# Templates

Copy these when starting a new project. Don't edit the templates themselves — copy them into `projects/<slug>/`.

## Files

- [`edit-plan/edit-plan.template.md`](edit-plan/edit-plan.template.md) — the master plan
- [`shot-list/shot-list.template.md`](shot-list/shot-list.template.md) — shot-by-shot breakdown
- [`beat-map/beat-map.template.md`](beat-map/beat-map.template.md) — song analysis summary

## Quick usage

```bash
# Create a new project folder
./scripts/new-project.sh my-edit-name

# Or manually
mkdir -p projects/my-edit
cp templates/edit-plan/edit-plan.template.md projects/my-edit/edit-plan.md
cp templates/shot-list/shot-list.template.md projects/my-edit/shot-list.md
cp templates/beat-map/beat-map.template.md projects/my-edit/beat-map.md
```

## Placeholder syntax

Placeholders use `{{DOUBLE_BRACE}}` syntax. Search-and-replace them as you fill in the plan. The new-project script can pre-fill common ones from your input.

Common placeholders:
- `{{PROJECT_NAME}}` — human-readable project name
- `{{THEME}}` — one-sentence theme
- `{{SONG}}`, `{{ARTIST}}`, `{{SONG_TITLE}}` — music info
- `{{FANDOMS}}`, `{{F1}}`, `{{F2}}`, etc. — fandoms involved
- `{{DURATION}}` — target runtime
- `{{PLATFORM}}` — YouTube / TikTok / Reels / multi
- `{{VIBE}}`, `{{MODE}}`, `{{ARCHETYPE}}` — project character
