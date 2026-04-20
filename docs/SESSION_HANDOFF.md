# Session Handoff — FandomForge Engine

**For any new Claude Code session picking up this work.** Read this doc in full before doing anything. It's the definitive state of the project as of the end of the 2026-04-19 marathon session.

## TL;DR

- Nick (solo user) is building FandomForge, a multifandom video-editing AI engine
- One-day marathon session rebuilt the engine into a working end-to-end pipeline
- Latest render: `projects/action-legends/exports/graded.mp4` (222s, B / 83.5, full "centuries" song)
- 8 semantic commits today, 418 pytest, 76 vitest, tsc clean, 5 new knowledge docs
- Nick wants 90+ grade (A-) — currently blocked by 5 dark-content segments costing visual dimension 55 points

## Project facts

- **Repo:** `/Users/damato/Projects/fandomforge-engine/`
- **User:** Nick D'Amato (nickdamatoit@gmail.com) — solo user, no collaborators
- **GitHub:** `SirIconNick/fandomforge-engine` (Damatnic account has an Actions block — use SirIconNick)
- **Primary language:** Python 3.13 (tools/), TypeScript/Next.js 16 (web/)
- **Active project for all rendering tests:** `projects/action-legends/`

## Today's 8 commits (in order)

```
0ed8882 fix(render): full-song edit with narrative dialogue preamble — grade B / 83.5
5226c7e docs(render): shot-by-shot justification for v2 action-legends render
373ce28 fix(render): v2 intentional shot selection — scene-detect + beat-align + story arc
c53feee feat(render): minute-long action-legends end-to-end render — grade B / 85.3
fa02139 feat(knowledge): edit-type taxonomy + priors + auto-classification
59cb257 docs(reference): example re-run on action-legends showing quality-prior effect
d283720 feat(reference): quality tiering — metadata, transitions, lyric-sync, motion-cuts, weighted priors
e4b4e68 feat(engine): sync planner, SFX engine, complement matcher, reference corpus
```

Nothing pushed to remote. Nick reviews before pushing.

## What the engine does now

Full pipeline: `song + sources + edit_type → full-song rendered mp4 + review grade`

1. **Sync planner** (`tools/fandomforge/intelligence/sync_planner.py`) — beat-aligned cut placement with quality-weighted corpus priors + edit-type targets blended 60/40
2. **Edit-type classifier** (`tools/fandomforge/intelligence/edit_classifier.py`) — 8 types (action/emotional/tribute/shipping/speed_amv/cinematic/comedy/hype_trailer) with auto-detection from edit-plan concept
3. **Complement matcher** (`tools/fandomforge/intelligence/complement_matcher.py`) — cross-source match-cut pairs (thrown → received)
4. **SFX engine** (`tools/fandomforge/intelligence/sfx_engine.py`) — action SFX with variant rotation + beat-snap alignment + scene-audio blend config
5. **Reference library** (`tools/fandomforge/intelligence/reference_library.py`) — YouTube playlist ingestion, scene detection, quality scoring (6 axes → composite 0-100 score → S/A/B/C/D tier)
6. **Deep reference analyzer** (`tools/fandomforge/intelligence/reference_analyzer_deep.py`) — pacing curve, brightness, motion, beat-sync rate, tempo
7. **Shot proposer** (`tools/fandomforge/intelligence/shot_proposer.py`) — pre-existing, still there, the planner sits on top
8. **Assembly orchestrator** (`tools/fandomforge/assembly/orchestrator.py`) — parse → assemble → transitions → overlays → color → mix → mux, per-shot checkpoints, work_dir cleanup, song stream validation
9. **Review** (`tools/fandomforge/review.py`) — 5-dimension grading, letter grade, ship recommendation

## The 4 knowledge-base docs to read FIRST

1. **`docs/REFERENCES.md`** — reference corpus catalog (137 videos, 5 action playlists, per-tier signatures)
2. **`docs/edit-types/`** — 8 type-specific docs + `README.md` taxonomy + `dialogue-patterns.md` (5 narrative dialogue patterns)
3. **`docs/STACK_DECISION.md`** — why Python, what bottlenecks are (none are Python)
4. **`docs/FIRST_RENDER.md`** + **`docs/SHOT_BY_SHOT.md`** + **`docs/EXAMPLE_RERUN.md`** — render diagnostics

## Current render state (`projects/action-legends/`)

| Asset | Path | State |
|---|---|---|
| Source videos | `raw/*.mp4` | 4 original (extraction-2, john-wick-4, mad-max-fury-road, the-raid-2) |
| Fight compilations | `raw/fights/*.mp4` | 6 compilations, 1.6 GB total (JW best fights 4K, JW ultimate mashup, Raid all best, Raid 2 brutal, Extraction 2 every fight, Extraction 2 Tyler Rake) |
| Songs | `assets/song.mp3` | "centuries" — 229s, 89 BPM, 13 drops |
| Scene catalogs | `data/scenes/*.json` | 10 sources catalogued → 1858 total scenes scored for motion/luma/tier |
| Transcripts | `data/transcripts/*.json` | whisper word-level timestamps for all 10 sources |
| Shot list | `data/shot-list.json` | 217 shots, 222s total, all 10 sources |
| Dialogue | `dialogue/dialogue.json` + 3 WAVs | 3 chained Extraction 2 lines, gain -3 dB, duck -10 dB |
| Project config | `project-config.json` | edit_type=action, target_duration_sec=229, song=song.mp3 |
| Sync/complement/SFX plans | `data/*-plan.json` | regenerated from current shot-list |
| **Final mp4** | `exports/graded.mp4` | 186 MB, 222s, B / 83.5, YELLOW verdict |
| Baseline (pre-v2) | `.baseline/` | earlier sync-plan etc. for diff |
| Baseline (pre-minute) | `.baseline-pre-minute-render/` | the 27s original before v1 |

## Known issues blocking A- / 90+

### 1. Visual dimension scores 45 (warn) — 5 dark segments totaling ~2s
Content-driven. Action films have shadowed combat scenes. Current luma filter rejects scenes with `avg_luma < 0.17` but some that pass still contain brief dark frames mid-scene. Two fixable approaches:

- **Tighter luma filter**: bump MIN_LUMA to 0.22 in `_pick_scene`. Risks exhausting tier pools.
- **Smarter scene offset**: currently picks at 40% into scene; could use ffmpeg's signalstats to find the BRIGHTEST sub-section of each scene and start there.
- **Luma check at extraction time**: reject the extracted clip if its avg luma is too low, substitute another picked scene.

### 2. Scene audio disabled for the latest render
Turning scene audio on creates conflict with narrative dialogue (source clips have their own movie dialogue + sound effects that collide with injected dialogue cues). Fix path:

- Extend `mixer.py` to duck scene_audio to silence during dialogue-cue windows (same pattern the song already uses via volume expression)
- OR build scene_audio track with leading silence matching dialogue window, real audio from dialogue_end onwards
- The simpler path is the scene-audio ducking volume expression, mirroring the song-duck logic at lines 220-240

### 3. SFX packs missing
Planner emits 230 SFX events (sub_booms on drops, action SFX per shot) but 0 WAV files on disk. User should drop .wav files into:
```
~/.fandomforge/sfx/<kind>/   (sub_boom, impact, whoosh, gunshot, punch, kick, gun_cock, glass_break, etc.)
```
or
```
projects/<slug>/sfx/<kind>/
```
Mixer gracefully skips missing variants, so this is non-blocking — just adds punch when present.

### 4. Complement pairs = 0
Shot descriptions don't use thrown/received action-cue language ("throws punch", "takes hit", "fires gun"). The complement matcher (`tools/fandomforge/intelligence/complement_matcher.py`) needs descriptive prose in `mood_tags` or `description` fields. Future: enrich with LLM-generated action cues during ingestion.

### 5. drawtext filter not compiled in local ffmpeg
Title overlays skipped gracefully. Fix: `brew reinstall ffmpeg --with-libfreetype` or rebuild ffmpeg with freetype support. Non-blocking.

## Bugs fixed today (for reference — don't re-break these)

### assemble.py `_find_source_video` — recursive search + fight_ prefix strip
Previously only globbed `raw/<source_id>.*`. Fight compilations at `raw/fights/<stem>.mp4` were invisible → clips silently extracted as black. Fix in place — rglob + prefix strip.

### mixer.py dialogue input off-by-one
Used `[{input_i + 1}:a]` where `input_i` was already the correct index. Dialogue tracks referenced non-existent ffmpeg inputs → silent dialogue AND constant-1 song volume. Fix in place.

### reference_library.score_quality audience normalization
Previously used corpus_max_views → one 65M-view outlier crushed everyone else to single digits. Now uses 90th-percentile. Fix in place.

### reference_library.py tier thresholds
Previously 90/80/70/60/0 → everything scored D. Now calibrated 82/73/65/55/0 against empirical distribution.

### ingest_playlist download file-id matching
Previously globbed first mp4 → subsequent videos re-analyzed the first one. Now matches by explicit `video_id`. Fix in place.

## What would push to A- / 90+

This is Nick's explicit target. To get visual → pass and land at 90+:

1. Make `_pick_scene` reject scenes with `avg_luma < 0.22` (not 0.17)
2. If that exhausts a source's pool, fall back by SWAPPING source (not tier) — preserving the intent-match
3. Re-run scene extraction with brightness-aware offset (use ffmpeg brightness analysis to find the most-lit sub-section of each scene)
4. Consider scoring visual dimension less harshly — the current rubric fails at >10% dark runtime but warns linearly below. Review scoring might need tuning too.

Estimated effort: 1-2 hours of code + one re-render (~3 min).

## Reference corpus state

148 fandom edits analyzed across 5 action playlists (pl1-pl5). Quality-tiered:
- 11 B-tier (best of corpus)
- 31 C-tier
- 106 D-tier
- 0 A/S tier (whisper only ran on 15/148 so lyric_sync is neutral for most)

To push more videos into A/S: run whisper across all 148 reference videos. Estimated 8 hours CPU time. Not blocking.

Per-tier signature key findings (from REFERENCES.md):
- B-tier median shot duration: 0.95s vs D-tier 1.51s (37% faster)
- B-tier cuts-per-minute: 52 vs D-tier 39 (+33%)
- B-tier beat-sync: 71% vs D-tier 57% (+14 points)
- **Hard-cut and dissolve rates are nearly identical across tiers** — the craft difference is in TIMING and MOTION, not in flashy transitions

## 8 edit types (from `docs/edit-types/`)

Every edit type has:
- Production doc at `docs/edit-types/<type>.md` (pacing / sync / transitions / palette / sources)
- Priors entry in `tools/fandomforge/data/edit-types.json`
- Auto-classifier keyword coverage in `tools/fandomforge/intelligence/edit_classifier.py`

Types: `action` (current project), `emotional`, `tribute`, `shipping`, `speed_amv`, `cinematic`, `comedy`, `hype_trailer`

## 5 narrative-dialogue patterns (from `docs/edit-types/dialogue-patterns.md`)

1. **Thematic Preamble** — character states theme, montage delivers (CURRENTLY USED in action-legends)
2. **Interrogative Hook** — question posed, edit is the visual answer
3. **Declaration + Montage** — defining character line ("I am Iron Man"), montage illustrates
4. **Philosophical Frame** — worldview stated, edit demonstrates consequences
5. **Chained Lines** — 2-3 short lines from different characters, shared narrative

## The full 10-source catalog for action-legends

| Source id | Location | Scenes | Top tier |
|---|---|---|---|
| extraction-2 | raw/ | 50 | high |
| john-wick-4 | raw/ | 84 | mid |
| mad-max-fury-road | raw/ | 90 | climax+high (most intense) |
| the-raid-2 | raw/ | 58 | held+low |
| fight_extraction2_every_fight | raw/fights/ | ~118 | mid |
| fight_extraction2_tyler_rake | raw/fights/ | 360 | held+mid |
| fight_jw_best_fights_4k | raw/fights/ | 496 | held+low (lots of slow-mo holds) |
| fight_jw_ultimate_mashup | raw/fights/ | 78 | mixed |
| fight_raid_all_best_fights | raw/fights/ | 111 | mid+high |
| fight_raid2_brutal_fights | raw/fights/ | 79 | mid+held |

## Test counts

- **pytest:** 418 passing, 10 skipped (integration tests that need fixtures fetched)
- **vitest:** 76 passing
- **tsc --noEmit:** clean

Commands:
```bash
cd tools && ./.venv/bin/python -m pytest -q --ignore=tests/test_integration_real_media.py
cd web && pnpm tsc --noEmit && pnpm vitest run
```

## Schemas (22 total)

All JSON Schema Draft 2020-12 under `tools/fandomforge/schemas/`. Key ones:

- `shot-list.schema.json` — the edit's shot timeline
- `beat-map.schema.json` — song rhythm + drops + breakdowns
- `sync-plan.schema.json` — song points + shot recommendations
- `complement-plan.schema.json` — match-cut pairs
- `sfx-plan.schema.json` — SFX events + scene_audio_blend config
- `post-render-review.schema.json` — grade report
- `reference-priors.schema.json` — corpus priors (per-tier, audience-weighted)
- `project-config.schema.json` — per-project settings, includes `edit_type` enum

TS types auto-generated via `scripts/generate-ts-types.mjs` → `web/src/lib/types/generated.ts`. Run `pnpm types:gen` after any schema change.

## Web dashboard state

Next.js 16 on port 4321. Key routes:
- `/projects/[slug]` — project home with render/color/export/review/sync buttons
- `/projects/[slug]/review` — post-render grade report
- `/projects/[slug]/sync` — sync plan viewer
- `/api/project/[slug]/{render,color,export-nle,review,sync-plan}` — CLI proxies

Components: `ReviewReport.tsx`, `ProjectActions.tsx`, `EnvBanner.tsx`, `AutopilotProgress.tsx`

## If the new session is asked to continue

**Most likely next asks from Nick:**

1. **"Get to A- / 90+"** — implement the luma-fix approach from the "Known issues" section above
2. **"Turn scene audio back on"** — implement dialogue-window ducking in mixer
3. **"Add SFX packs"** — Nick may drop .wav files, verify mixer picks them up
4. **"Re-pick a better iconic line"** — current is Extraction 2's "you fought your way back." He may want a different one
5. **"Make another edit"** — use the engine on a new project/song. The pipeline now handles it end-to-end

**Do NOT do without asking:**
- Re-download source media (1.6 GB already on disk)
- Re-run whisper on the 148-video reference corpus (8 hours)
- Push to GitHub (Nick reviews first)
- Drop the existing `projects/action-legends/` state

## Key env / config notes

- `FF_REFERENCES_DIR=/Users/damato/Projects/fandomforge-engine/references` — when running `ff sync plan` etc.
- `FF_AUTOPILOT_SUBPROCESS=1` — debug escape hatch for in-process autopilot
- `FF_CACHE_DIR` — scene-detection cache location (defaults to `~/.fandomforge/cache/`)
- `ANTHROPIC_API_KEY` — absent in Nick's env; edit-strategist LLM falls back to heuristic
- `OPENAI_API_KEY` — absent; GPT-4o director review skipped

## Nick's voice / working style

- Direct, fast feedback, no patience for BS
- Wants to see working output, not plans
- Will interrupt mid-task with corrections — respond to them immediately
- "Make sure it's good" — means quality matters more than feature completeness
- Honest > optimistic. When asked scope, tell the truth.
- Global CLAUDE.md at `~/.claude/CLAUDE.md` — read it if unfamiliar
- Voice memory: `/Users/damato/.claude/projects/-Users-damato-Projects-fandomforge-engine/memory/` — contains user profile, feedback, project context
