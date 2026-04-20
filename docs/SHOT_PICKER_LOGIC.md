# Shot picker — what the densifier actually does

Written 2026-04-21 after the six-letter fix plan landed
(commits fff5c28 → 31e0d17). The goal of this doc is that nobody can
claim "we have logic here" again without being accountable for the
actual code.

## Where it lives

`tools/fandomforge/intelligence/shot_proposer.py`

Two public entry points:

- `propose_shot_list(inputs)` — generates SLOT shots at every sync
  point (drops + downbeats). Sparse.
- `densify_shot_list(shot_list, ...)` — fills the gaps with
  "insert"-role fillers to hit the target duration.

The old story was that `propose_shot_list` had logic and densify was
dumb. Both are now load-bearing.

## The inputs

`densify_shot_list` takes:

- `shot_list` — the sparse slot shots from propose_shot_list
- `edit_plan` — drives pacing bands + target_cpm
- `song_duration_sec` — what the beat-map says
- `target_duration_sec` — what project-config says (≤ song; may be less)
- `scenes_by_source` — dict of source_id → list of scene dicts carrying
  `avg_luma`, `motion_dir`, optional `intensity_tier`
- `source_catalog` — present but unused in the picker today

## What happens, in order

### 1. Duration clamping

```
fill_sec = min(song_duration_sec, target_duration_sec)
```

Slot shots whose start_sec ≥ fill_sec are dropped. The last kept slot
shot's duration is clamped so it doesn't spill past fill_sec. This is
why a 90s project-config on a 229s song no longer renders 229s.

### 2. Shot-count budget

```
target_cpm = edit_plan.target_cpm
           or _TARGET_CPM_BY_EDIT_TYPE[edit_type]
           or 35.0
```

Defaults: action=45, sad=18, tribute=25, dance=30, hype_trailer=55.

```
natural_filler_count = sum over all gaps of (gap_sec / band_median)
budget_total = target_cpm * fill_sec / 60
budget_fillers = budget_total - slot_count
stretch_factor = natural / budget_fillers
stretch_factor = clamp(stretch_factor, 0.5, 5.0)
```

Every filler's duration is then `band_median * stretch_factor`, clamped
to `[band_lo, band_hi * 1.5]`. Relative pacing between acts survives
(slow acts still emit longer fillers than frantic acts) but overall cpm
lands near budget. Centuries-action: 78 cpm → 42 cpm.

### 3. For each gap, place fillers

`_fill_gap` loops over the gap, requesting one filler at a time from
`_make_filler`. After each filler, it reads the filler's stored
`duration_frames` (which may have been clamped inside `_make_filler` —
see step 4d) and advances the cursor by that amount.

### 4. Scene picking — `_pick_scene`

Given target_intensity, prev_luma, prev_motion_dir:

**4a. Source rotation.** Sources are ranked ascending by prior use count
so under-used sources pick first.

**4b. Per-source candidate collection.** For each source, iterate scenes
and apply:
- Skip if scene already used (used_scenes set)
- Skip if scene duration < 0.4s (too short to be meaningful)
- Skip if intensity mismatch (strict pass only)
- Skip if `avg_luma < MIN_SCENE_LUMA` (0.22). Instead of dropping, track
  in `dark_fallbacks` list for last-resort use.

**4c. Within a source, rank candidates by continuity cost:**

```
cost(cand) = luma_part + 0.3 * opposite_motion_dir
luma_part  = |cand.avg_luma - prev_luma| or 0 if prev_luma unknown
```

Opposite_motion_dir is 1 only for same-axis opposites (left↔right, up↔down).
static/mixed/unknown take no penalty.

First source with any passing candidate wins; within it the lowest-cost
candidate wins.

**4d. Last-resort.** If every scene in every source was dark-rejected,
pick the brightest dark one rather than returning None. This prevents
total picker failure when a source is pure fade-heavy material.

**4e. Scene duration cap.** Once `_make_filler` has picked a scene, it
clamps the filler's duration_frames to the picked scene's actual length.
Without this, a 2.6s filler would extract scene+overflow and land in the
next (possibly dark) scene.

## What we explicitly do NOT do

- **Eyeline match.** Scene data doesn't carry eyeline info. Would need
  face detection + head-pose inference. Out of scope.
- **Per-frame luma probe at pick time.** Costs seconds per filler.
  Instead we rely on pre-computed `avg_luma` (3 samples per scene) from
  the enricher. Acceptable if MIN_SCENE_LUMA has enough buffer above the
  reviewer's 0.1 blackdetect threshold.
- **Sharpness / composition scoring.** Would require per-frame feature
  extraction at enrich time. Next phase.
- **Aspect-ratio correction at pick time.** There's a separate
  `letterbox-plan.json` stage that handles this.
- **Emotion-arc alignment.** Picker is oblivious to `emotion-arc.json`.
  Next phase.

## The single scariest footgun

If a scene in `scenes_by_source` is missing `avg_luma`, the picker
treats it as "unknown luma" and does NOT reject. This is by design
(legacy scene files without enrichment should still work) but it means
that if the enricher fails silently on a source, you'll see dark picks
again.

Always run `ff scenes enrich <project>` after ingest on any project
whose scenes came from raw ingest rather than
`reference_analyzer_deep`. Autopilot already wires this as
`step_enrich_scenes` after `profile_sources`.

## Current measured behavior (centuries-action, 2026-04-21)

| before any fix | after six-letter plan | after cleanup |
|---|---|---|
| 297 shots / 229s / 78 cpm | 53 shots / 90.0s / 35 cpm | 63 shots / 90.0s / 42 cpm |
| visual dim 0 (blackdetect flagged 15+ segments) | visual 57 (3 segments, 1.50s) | visual 57 (3 segments, 0.67s) |
| overall D+/68 | C+/77.9 | B-/80.3 |

## What still warns

- **visual = 57.** Three short dark segments remain (~0.67s total). The
  first is a structural fade-in at 0.00s. The other two are edge cases
  where a scene's avg_luma sits near the threshold. Not worth chasing
  below 0.5% dark runtime.
- **engagement = 63.** "One source dominates." The rotation is
  use-count-based but doesn't enforce a hard round-robin. Next phase.
- **arc_shape = 75.** Pre-existing Phase 2 issue, unrelated to picker.
- **shot_list = 69.** The proposer stamps a "densified from N → M"
  warning that reviewers penalize. Cosmetic — could move to a metadata
  field instead of warnings[].
