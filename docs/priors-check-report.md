# Per-bucket priors check — 2026-04-20

Verification that the Phase 0.5.3 per-bucket priors layering actually produces
distinct editorial profiles per edit_type and that the pipeline loads them at
runtime.

## Final corpus state (post-dedup)

After removing `action-pl7` (duplicate of existing `action-pl1`):

| edit_type        | videos | cpm  | median shot | tempo (bpm) |
|------------------|--------|------|-------------|-------------|
| sad_emotional    | 17     | 17.4 | 2.35s       | 129         |
| dance_movement   | 5      | 34.4 | 1.55s       | 117         |
| tribute          | 126    | 35.4 | 1.38s       | 123         |
| action           | 163    | 39.9 | 1.23s       | 123         |
| hype_trailer     | 15     | 52.6 | 0.92s       | 129         |

Ordered by cpm — the pacing profile tracks editorial grammar. Sad edits
linger (2.35s median, half the cut rate of action). Hype trailers burn
through cuts at 52/minute vs sad's 17/minute — the 3× difference is the
priors signal sync_planner picks up when routing per-bucket defaults.

## What was verified

**1. Priors load at runtime for all 5 buckets** — `load_per_bucket_priors(edit_type=X)`
returns a valid priors dict with 14 keys including cpm, median_shot, tempo_bpm.

**2. Phase 3.3 color_grade_confidence stamping works end-to-end** — a fresh
autopilot run on an action clone stamped confidence on all 15 shots, flagged
1 D-tier source below the 0.6 floor, avg 0.67. The QA rule
`qa.color_grade_confidence` reports actual numbers instead of the legacy
"skipped: Phase 3.3 not yet wired into render" message.

**3. Phase 4.10 applies_to routing works** — `qa.dialogue_safe_window` skips
cleanly for non-dialogue edits, `qa.aspect_consistency` elevates to `block`
on hype_trailer. Unit tests in `tools/tests/test_qa_gate.py` cover both
paths.

**4. Baseline preserved** — action-legends `graded.mp4` reviews at B/85.7
both before and after priors rebuild + action-pl7 dedup. The 178→163 video
drop in action priors shifted cpm from 40.4 → 39.9 (expected minor move).

## What was attempted but didn't pan out

**Full render-based regression via cloned project.** Cloned action-legends
to `action-legends.priors-check/` with stripped downstream artifacts; fresh
autopilot run produced a 15-shot/16s sparse shot-list that fails qa.duration
(shot-list total 16s vs song 229s).

Root cause: the current `propose_shots` step is slot-based — it emits one
shot per sync_point (drops + downbeats). action-legends has 13 drops + 3
downbeats = 16 sync_points → 15 shots. The QA gate expects shot-list total
duration to match song duration, which only works when a downstream
"dense-fill" pass adds establishing/reaction/transition shots between the
sparse slot-sync shots. That pass isn't wired into the autopilot pipeline
as shipped.

Nick's original 217-shot `action-legends/shot-list.json` came from an
earlier/alternate render path (commit 0ed8882 "full-song edit with narrative
dialogue preamble"), not the current `propose_shots` path. A fresh autopilot
run on ANY project hits this ceiling — not a priors issue.

Clone was removed (`rm -rf projects/action-legends.priors-check`). Nick's
original action-legends untouched.

## Recommendations for next session

- **Separate issue:** wire a dense-fill shot-proposal pass between slot
  selection and qa_gate. Probably belongs as a new autopilot step
  `step_fill_between_sync_points` that reads slot-list + scene inventory
  and inserts 1-2 second shots to reach song duration.
- **Or:** add a QA override policy where qa.duration is warn-only when the
  shot-list is explicitly a "slot-only" draft — the render step can still
  build a full roughcut by extending shot durations to fill gaps.
- **Priors layering is verified and shipping.** Render-path work is separate
  from the priors-check done here.
