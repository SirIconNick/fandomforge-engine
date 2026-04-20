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
**Fix (this project):** NOT FIXED — the D+ is an honest reflection of picking dark source content without luma-aware filtering.
**Permanent guard:** extend `_pick_scene` to deprioritize scenes where `avg_luma < 0.15` (below visible-threshold). Derived scenes don't carry luma; either (a) compute luma at scene-detect time in `ff ingest`, or (b) add a luma-sample probe in densify for scenes without the field. Target: dark pockets become last-choice, not default.

### target_duration_sec=90 but engine renders 229s (full song)

**Symptom:** project-config says 90s target, engine renders 229s full song duration.
**Root cause:** densify fills to `song_duration_sec` (from beat-map.json) without respecting `target_duration_sec`. The truncation isn't wired into the pipeline.
**Fix (this project):** **NOT YET FIXED — tracked separately.** For this render we accepted the 229s output.
**Permanent guard:** add `target_duration_sec` awareness to densify. Options: (a) truncate song + beat-map to target; (b) subset sync_points to fit target; (c) explicit warning when target < song_duration. Decide per-project and ship one.

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
