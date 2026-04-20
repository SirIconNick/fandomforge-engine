# Render postmortem — gotchas + permanent fixes

Running catalogue of every problem I hit trying to render a fresh project,
the root cause, the fix, and the forward-looking guard so it doesn't
recur. Add entries here after every render. If the same class of problem
shows up twice, the second entry flags a "still-broken" and escalates.

Format per entry:
```
### YYYY-MM-DD — <short name>
**Project:** <slug>
**Symptom:** what I saw
**Root cause:** why
**Fix (this project):** what I changed now
**Permanent guard:** code change / rule / checklist item that prevents recurrence
```

---

## 2026-04-20 — centuries-action (1:30 action cut)

### edit-plan validation failure: `target_duration_sec`

**Symptom:** densify step failed with `ValidationError: Unexpected key 'target_duration_sec'` on edit-plan.json.
**Root cause:** `project-config.json` uses `target_duration_sec`; `edit-plan.schema.json` uses `length_seconds`. My project-config-override code copied the field name literally. Schemas are strict-additional-properties.
**Fix (this project):** explicit rename in `step_edit_plan` — reads `cfg.target_duration_sec`, writes `plan.length_seconds`. Commit 6ea9f98 (upcoming).
**Permanent guard:** any new override/reconcile logic between config files should go through a small `config_to_edit_plan_fields(cfg)` adapter. Never assume same name across schemas — check the destination schema's `required` + `properties` keys.

### Auto-stamping fair_use_statement violated CLAUDE.md rule

**Symptom:** qa.copyright blocked on >60s YouTube render. My first fix auto-stamped a blanket fair-use statement into every edit-plan.
**Root cause:** I mistook a project policy ("fan edits don't need copyright gating") for a legal defense posture. CLAUDE.md explicitly says *"Don't claim fair use blankets in generated credits — fair use is case-by-case, flag uncertainty when it matters."*
**Fix (this project):** reverted the auto-stamp. Demoted `qa.copyright` from `level="block"` to `level="info"` so the rule still surfaces concerns but doesn't gate fan edits.
**Permanent guard:** QA rules that encode **policy/legal/ethical** decisions (copyright, takedown risk, licensing) default to `level="info"`. Only **technical correctness** rules (fps, resolution, refs, duration, safe-area) get `level="block"`. Before making a rule blocking, ask: "is this a measurable technical fact, or a judgment call?"

### Hardcoded `level` in RuleResult ignores decorator default

**Symptom:** I changed `@rule(...level="info")` but the rule still reported `level=block` in qa-report.
**Root cause:** Each RuleResult the rule function returns hardcodes `level=`. The gate decorator's `level=` is only used when the returned RuleResult has empty `level=""`. Confusing API.
**Fix (this project):** also updated every hardcoded `level="block"` inside `qa/rules/copyright.py` RuleResults.
**Permanent guard:** new rules should use `level=""` in their RuleResult returns and let the decorator inject. Avoid hardcoding level in two places — one source of truth.

### Render output was 15.9s instead of 229s (shot-list.md stale)

**Symptom:** post-render-review: "rendered 15.92s vs expected 229.21s (93.1% off)".
**Root cause:** `step_propose_shots` writes `shot-list.md` from the 15-shot draft. Densify mutates `shot-list.json` to 297 shots but doesn't regenerate the `.md`. The ffmpeg assembly reads `.md` (not `.json`), so it only renders the 15 slot shots.
**Fix (this project):** `step_densify_shot_list` now refreshes `shot-list.md` from the densified set.
**Permanent guard:** whenever ANY step mutates `shot-list.json`, it MUST re-derive every downstream projection (`shot-list.md` today, `shot-list.fcpxml` tomorrow). Centralize this in a helper: `_rewrite_all_shot_list_projections(shot_list_dict, project_dir)` called from every mutating step.

### Scene-match silently fell back — scenes file at unexpected path

**Symptom:** Only 15 distinct source_timecodes in the 297-shot list despite scene-match supposedly being active.
**Root cause:** `step_densify` looked at `data/scenes/<path-stem>.json` (legacy layout). Fresh `ff ingest` writes `derived/<blake3-id>/scenes.json`. Paths never matched → empty `scenes_by_source` → fallback to flanking-shot inheritance.
**Fix (this project):** densify step now tries BOTH paths. Preferred: derived layout. Fallback: legacy.
**Permanent guard:** derived-artifact path discovery should be centralized — a `locate_scenes(source_entry, project_dir) -> Path | None` helper that knows both layouts (and any future ones). Don't hardcode paths in consuming code.

### Derived scenes schema is minimal (no intensity_tier / motion / duration)

**Symptom:** Even after finding scenes, the picker skipped everything — scenes didn't have `intensity_tier` or `duration_sec` fields.
**Root cause:** `ff ingest`'s scene detector emits only {index, start_sec, end_sec, frames}. The legacy hand-curated scenes in action-legends had extra enrichment (motion, luma, tier).
**Fix (this project):** picker now computes `duration_sec` from `end_sec - start_sec` and infers `intensity_tier` from duration (<1s = high, 1-3s = medium, ≥3s = low). Scenes with explicit fields keep theirs.
**Permanent guard:** consumers of derived artifacts must be schema-resilient. If a field is missing, derive it lazily OR skip the check gracefully. Never silently NO-OP — log a warning so we catch drift.

### Idempotent skip-logic re-used stale artifacts across re-runs

**Symptom:** After shipping fixes, re-ran autopilot. Densify was "already done," roughcut was "already done," render was still bad.
**Root cause:** Every step has `check_done(ctx)` that skips when output artifact exists. Stale `shot-list.json` + `roughcut.mp4` from the previous broken run persisted; autopilot skipped the steps even though the intent was to re-run from scratch.
**Fix (this project):** manually `rm -f shot-list.json sync-plan.json emotion-arc.json complement-plan.json qa-report.json aspect-plan.json sfx-plan.json post-render-review.json shot-list.md && rm -rf exports derived` before relaunching.
**Permanent guard:** add `ff autopilot --reset-from=<step>` or `ff autopilot --redo` flags that invalidate downstream artifacts from a given step and proceed fresh. Documentation pattern: after ANY code change that affects a step's output, manually delete that step's output + all its downstream deps before re-running.

### visual dim=0 — scene-match picked dark segments

**Symptom:** After all other fixes, final render graded D+/68. `visual` dimension landed at 0 (15+ dark segments across 229s).
**Root cause:** My scene-picker ranks by intensity (derived from duration as a proxy when `intensity_tier` is missing) + source rotation, but NOT by `avg_luma`. Source material (John Wick 4, Raid 2) is famously dark; without a luma-based filter the picker happily grabs 4s black-frame sections.
**Fix:** **FIXED 2026-04-21** — six-letter plan (commits fff5c28 → 31e0d17). `MIN_SCENE_LUMA=0.22` hard filter in `_pick_scene` with brightest-dark last-resort fallback. Also added `scene_enricher` (new module) that backfills `avg_luma` and `motion_dir` on every scene at autopilot time (or via `ff scenes enrich <project>`). After fix: centuries-action dark runtime 2.24s → 0.67s. Grade visual 0 → 57. See `docs/SHOT_PICKER_LOGIC.md` for current picker behavior.

### target_duration_sec=90 but engine renders 229s (full song)

**Symptom:** project-config says 90s target, engine renders 229s full song duration.
**Root cause:** densify fills to `song_duration_sec` (from beat-map.json) without respecting `target_duration_sec`. The truncation isn't wired into the pipeline.
**Fix:** **FIXED 2026-04-21** (commit fff5c28). `densify_shot_list` now accepts `target_duration_sec`. Slot shots starting past target are dropped; the final kept slot shot has its duration clamped; tail fill terminates at min(song, target). `autopilot.step_densify_shot_list` reads `edit_plan.length_seconds` and passes it through. `qa.duration` now grades shot-list total against min(song, target) and reports which one in `evidence.graded_against`. Verified: centuries-action renders 90.00s exactly.

### machine-gun cutting — 297 shots in 229s (74 cpm, seizure pacing)

**Symptom:** Even after the above were conceptually scoped, the densifier emitted 0.425s fillers for every gap because every action act is classified 'frantic' (band 0.25-0.6s) and the picker used the band median with no upper bound.
**Root cause:** `_filler_dur_sec` returned `(lo + hi) / 2` unconditionally. A 90s edit filled to ~141 natural cpm. Tests measured "duration correct" but the viewer experiences it as chaos.
**Fix:** **FIXED 2026-04-21** (commit 8c11001). Shot-count budget governor in `densify_shot_list`. `_resolve_target_cpm` pulls from `edit_plan.target_cpm` → edit_type default (action=45, sad=18, tribute=25, dance=30, hype_trailer=55) → 35. Natural cpm is estimated across all gaps; stretch_factor multiplies every filler's band median to land near budget. Clamped to `[lo, hi*1.5]` so slow acts stay recognizably slower than frantic. Centuries-action cpm 78 → 42.

### flash cuts — dark/bright alternation between consecutive scenes

**Symptom:** After dark-scene reject, the picker still bounced between avg_luma=0.3 and avg_luma=0.8 scenes, creating visible flicker.
**Root cause:** No shot-to-shot luma continuity. Source rotation was the only cross-shot signal.
**Fix:** **FIXED 2026-04-21** (commit 4a928a9). `_pick_scene` accepts `prev_luma`; when multiple candidates pass the hard filters, it ranks them by `abs(cand.avg_luma - prev_luma)`. `last_picked_luma` tracked across fillers. Plus Fix F (commit b2a25a2) adds `motion_dir` continuity — opposite-axis directions (left↔right, up↔down) take a +0.3 cost in the same continuity sort. `coherence` dim went 0 → 100.

### filler spilled past picked scene boundary

**Symptom:** Even after dark-scene reject tightened to 0.22, some dark segments remained — picks of 2.6s duration on 1.5s scenes grabbed the scene PLUS 1.1s of whatever came after.
**Root cause:** `_make_filler` used the caller's requested duration verbatim without bounding to the picked scene's actual length. Extraction would slide past the scene into the next one.
**Fix:** **FIXED 2026-04-21** (commit 31e0d17). `_make_filler` caps `dur_frames` to the picked scene's duration. `_fill_gap` reads the returned filler's stored duration and advances `cursor` by that (not the requested one) so gaps stay covered by additional fillers. Centuries-action dark runtime 1.50s → 0.67s.

### source-missing — bare stem doesn't resolve to `fight_<stem>` raw file

**Symptom:** Roughcut emitted "filled with black (source-missing:extraction2_tyler_rake)" for 9 of 53 shots even though the file `fight_extraction2_tyler_rake.mp4` was present in `raw/`.
**Root cause:** `_find_source_video` handled `fight_X` → `X` (for scene data that carries fight_ prefix but files don't) but not the reverse — bare stem from scene data when files DO carry the prefix.
**Fix:** **FIXED 2026-04-21** (commit 31e0d17). Added inverse lookup: if bare stem misses, try `fight_<stem>`. All centuries-action source resolutions now succeed.

---

## Pre-flight checklist (run before declaring any fresh-render attempt)

1. ✅ `project-config.json` exists with `edit_type`, `target_duration_sec`, `platform_target`, `fandoms`
2. ✅ `assets/song.<ext>` exists (mp3/wav/m4a/flac)
3. ✅ `raw/*.mp4` has ≥3 source clips
4. ✅ `data/source-catalog.json` exists (autopilot regenerates)
5. ✅ `derived/<blake3-id>/scenes.json` exists for every source (autopilot ingest produces)
6. ✅ `tools/.venv/bin/ff --version` returns without error
7. ✅ Atlas cookies fresh at `/tmp/claude/ff-ingest/yt-cookies.txt` (only needed for yt-dlp-based tasks)
8. ✅ Load average < 4.0 (or thermal watchdog armed)

## Known friction points watch list

- **Long songs + short target_duration**: engine renders full song unless target truncation is wired.
- **Schemas evolving**: new fields (densified, warnings) get added; older projects may need `ff migrate` or a manual touch-up.
- **Heuristic edit-plan vs LLM**: LLM-drafted plans are structurally different — the override logic must work on both.
- **Thermal on laptops**: whisper + scene detection can spike load past 8.0. Use the orchestrator daemon's throttle.

## Forward-looking work

- [ ] Wire `target_duration_sec` truncation in densify
- [ ] Consolidate scene-file lookup into `locate_scenes()` helper used by everyone
- [ ] Centralize shot-list projection regeneration into one helper
- [ ] Enrich `ff ingest` scenes with motion + luma + intensity at detection time (remove the downstream-inference burden)
- [ ] Add a `ff doctor` subcommand that runs the pre-flight checklist before autopilot
