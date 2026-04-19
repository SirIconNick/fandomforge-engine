# Example Re-run — action-legends with Reference Priors Loaded

Demonstrates what the quality-weighted reference priors actually change when
the sync planner runs on a real project. Run date: 2026-04-19.

## Setup

- **Project**: `projects/action-legends/` (Nick's real multifandom project)
- **Song**: "Centuries"
- **Shot list**: 15 shots across multiple fandoms, durations clustered around 2.0s
- **Reference corpus**: 148 videos across 5 fandom-edit playlists, scored and tiered
- **Baseline**: sync plan generated without `reference_priors` loaded
- **New**: same sync plan with `load_priors()` feeding the scorer

## Counts — before and after

| Artifact | Baseline | With priors | Delta |
|---|---|---|---|
| sync-plan.song_points | 15 | 15 | — |
| sync-plan.lyrics | 0 | 0 | — (no song-lyrics.json present) |
| complement-plan.pairs | 0 | 0 | — (no action cues in current shot descriptions) |
| complement-plan.unpaired_received | 1 | 1 | — |
| sfx-plan.events | 15 | 15 | — |
| sfx-plan.beat_aligned | 15/15 | 15/15 | — (100% — the drops are the events) |

Counts are identical — that's the point. The priors don't add or remove
recommendations; they **rescore** them, and the rescoring reveals a style gap.

## Score shifts — the real signal

Top-pick recommendations stayed the same for all 15 song points (same
shot ordering, because local scoring stays internally consistent). But
**every confidence score dropped by 0.04–0.05 points** when priors loaded:

| Song point | Baseline top pick | New top pick | Score shift |
|---|---|---|---|
| break_000 | s004 @ 0.68 | s004 @ 0.64 | **−0.04** |
| drop_000 | s006 @ 0.83 | s006 @ 0.79 | **−0.04** |
| drop_001 | s010 @ 0.81 | s010 @ 0.77 | **−0.04** |
| drop_002 | s003 @ 0.67 | s003 @ 0.62 | **−0.05** |
| drop_003 | s007 @ 0.69 | s007 @ 0.64 | **−0.05** |
| drop_004 | s001 @ 0.71 | s001 @ 0.67 | **−0.04** |
| drop_005–drop_012 | all — | all — | **−0.04 each** |
| break_001 | s002 @ 0.52 | s002 @ 0.47 | **−0.05** |

All 15 top-pick scores dropped. **That's the planner telling you something.**

## Why scores dropped — the duration mismatch

```
action-legends shot-list:  median duration 2.00s, avg 1.80s
Corpus B-tier (excellent): median duration 0.95s
Project shots are 2.1x longer than the "excellent" signature.
```

The scorer's `duration_prior` component (weight 0.15) penalizes shots whose
duration is far from the learned median. Real fandom edits at B-tier cut
at ~1s intervals; action-legends holds each shot for ~2s. The planner is
flagging this via the 4-5 point confidence drop — not a warning, not a
blocker, just lower confidence that these picks will read as a "real"
fandom edit at the corpus style level.

No "duration matches reference priors" reason string appears in the new
plan because *no shot gets close enough to the corpus median to earn
that badge.* The `_duration_prior_score` threshold for that reason is
≥0.85 (shot within ~25% of median). At 2.00s vs 0.95s median, the ratio
score is ~0.48 — well below.

## What would push scores up

If the shot list were redrafted with tighter cuts — 0.8–1.2s shots instead
of 2s — the `duration_prior` score would jump to 0.9+ and add ~8 points
to every composite score. The planner would then emit "duration matches
reference priors" reason tags for most picks.

Same story for the other quality axes: if the project's source material
was richer in action cues (thrown → received pairs), the complement plan
would produce match-cut pairs instead of 0. If a `song-lyrics.json` were
generated for "Centuries" (via `ff sync extract-lyrics`), the sync plan
would include lyric-typed song points with meaning-sync targets.

## New reason tags that appeared

Same reason-tag distribution in both runs:

```
  35x mood=defiant pairs with intense
   4x mood=intense matches intense
   4x intensity-match
   3x already-used-earlier
   2x mood=tender pairs with somber
   1x act 3 lands in song-act 3
```

No new tags in the with-priors run. Expected — the duration-match tag
only fires when shots actually align with the corpus median, which these
don't. The signal the priors added is in the **scores**, not in new
categorical reasons.

## SFX plan + complement plan observations

- **SFX plan**: 15 events, all at drop timestamps, all beat-aligned. 13
  sub_booms + 2 impacts. None of the shots had punch/kick/gunshot cues
  in their descriptions so no per-shot action SFX were generated — only
  the drop-aligned sub-booms. The project's shot descriptions need more
  action-cue language (punch, fire, hit, tackle) to activate the SFX
  engine fully. Scene-audio blend is enabled at −20 dB.

- **Complement plan**: 0 pairs. Same reason — shot descriptions don't
  tag thrown/received actions, so the cross-source match-cut pairing has
  nothing to pair. 1 unpaired received found. To exercise this pipeline,
  shot descriptions would need phrases like "throws punch" / "takes hit"
  / "fires pistol" / "bleeds out."

## Plain-English summary

With the quality-weighted priors loaded, the planner doesn't change
**which** shots it recommends — it changes **how confident** it is in
those recommendations, based on how closely the shot durations match what
real fandom editors do. For `action-legends`, every confidence score
dropped 4–5 points because the project's 2s shots are twice as long as the
corpus's B-tier 0.95s median. That's a diagnostic: if the goal is to hit
the "excellent fandom edit" signature, the shot list needs faster cuts.
The planner isn't rewriting the project for you — it's telling you where
your project drifts from the style envelope of 148 analyzed reference
videos.

## Next moves (not part of this pass)

- Redraft the `action-legends` shot list with 0.8–1.2s shots and re-run
  to see scores climb into the 0.80+ range
- Run `ff sync extract-lyrics --project action-legends` to add lyric
  song-points to the sync plan
- Enrich shot descriptions with action-cue language so the complement
  matcher + SFX engine can produce real pairs and per-shot SFX
- Run whisper across the remaining 133 videos in the corpus to lift more
  reference edits into A/S tier — currently our ceiling is B because
  lyric_sync defaults to a neutral 50 for unscored videos
