---
created: 2026-04-19
updated: 2026-04-19
type: analysis
app: FandomForge
status: reference
tags: [fandomforge, gap-analysis, roadmap-critique, workflow]
summary: "Where the engine will fall over when it tries to make what message.txt describes. Gaps not covered by Phases 1-5, and the workflow reordering needed before any of it gets built."
---

# FandomForge — Gap Analysis vs. message.txt Scope

> [!info] Read order
> Companion to [[FandomForge Engine]]. Written after reading the 11 research prompts in `message.txt` that define the target scope (5 edit types, dialogue-narrative stitching, mixed-media visual consistency, psych-aware scoring).

---

## TL;DR — The Three Structural Gaps

1. **The reference corpus is monocultural.** 148 videos across 5 action playlists = every prior, every weight, every S-tier shape is action-edit-flavored. The moment you render a tribute, sad edit, dance edit, or dialogue-narrative edit, the engine is scoring it with the wrong ruler. This is the biggest single gap and nothing in Phases 1–5 fixes it directly.
2. **Dialogue-narrative has no pipeline.** "Pattern 1 Thematic Preamble" is one preamble. What message.txt wants — a character appearing to "say" something by stitching snippets across sources — needs four stages that don't exist: `dialogue_script` → `dialogue_search` → `dialogue_lipsync` → `dialogue_place`. None of this is in the planned phases.
3. **There's no prompt interpretation layer.** The engine assumes you picked the right template from the 6 available. But message.txt's core promise ("prompt + song + sources → engine figures out the rest") requires an edit-type classifier, tone inferencer, and auto-template picker as the *first* pipeline stage. Everything downstream — arc, weights, QA rubric — is wrong without it.

Everything else is secondary to these three.

---

## What Will Fail Today If You Try To Render…

Concrete failure scenarios against the current engine, with the specific cause.

### …a character tribute to a slow emotional song
- Ref priors are action-corpus-only → shot cadence will be too fast for the song
- `color_matcher` forces a single LUT across clips; won't preserve the soft/desaturated look emotional tributes need
- No 8-dim emotional register on clips (planned Phase 1, not built) → "quiet reaction shot" vs "longing close-up" can't be distinguished
- `3-act-emotional` and `mentor-loss` templates exist, but their structural math was calibrated on action ref data

### …a dialogue-narrative edit ("this character says X by stitching snippets")
- No word- or phoneme-level index of dialogue across ingested sources. Whisper transcripts aren't enough — need searchable per-utterance segments with timing.
- No lip-sync plausibility scoring. Face rec exists; mouth ROI tracking + viseme-alignment does not.
- No "dialogue script from prompt" stage. Prompt goes straight to `edit_plan_stub` → `propose_shots`, skipping the fact that for this edit type the *script comes first, then the clips*.
- Current `DIALOGUE_SAFE/RISKY/BLOCKED` flagging doesn't exist — `mixer.py` ducks reactively when dialogue is already placed; it doesn't *choose* placement from the beat map.

### …a dance/movement edit cutting across fandoms
- `transition_matcher` uses optical-flow direction at cut boundaries, but there's no motion-vector continuity score *across* cuts ("leftward motion in clip A cuts to leftward motion in clip B")
- No rhythm-of-motion detection (step-frequency ≈ BPM matching)
- No cross-source movement-similarity embedding

### …a 30-second tight edit or a 6-minute long-form edit
- Templates encode fixed act structure (4-act, 3-act). Short edits need compression math — more like "single-beat statement" with tight intro/payoff. Long edits need tension-curve pacing with deliberate rest beats.
- `emotion_arc` stage exists but is one-size-fits-all
- Phase 2's arc-architect fixes this, but it's not built yet

### …any mixed-media edit (anime + live-action + western animation)
- `color_matcher` histogram-to-LUT is too blunt for anime↔live-action. 90s anime vs modern anime is a distinction the engine can't even represent.
- No aspect-ratio arbiter → a 4:3 anime clip cutting into 2.39:1 live-action will just… happen. Black bars everywhere or forced scaling that crops faces.
- No per-source visual profile (luma range, color cast, grain, quality tier) → no predictive risk flagging
- Phase 3 plans this but calls out that "Visual signature database bootstrapped from the 148-video reference corpus" — that corpus is action, so the signatures are biased

### …an edit in a fandom not already in `reference-priors.json`
- Priors fall back to defaults calibrated on action corpus
- No fandom-specific editing norms (K-pop fancam cadence ≠ Marvel tribute cadence ≠ anime AMV cadence)

### …any edit where dialogue needs to land in a specific music moment
- 1-second energy-curve resolution is too coarse. Dialogue window detection needs ≤250ms RMS windowing + frequency-band separation.
- No `DIALOGUE_SAFE/RISKY/BLOCKED` timestamp labeling yet
- Beat-proximity exclusion zones ("no dialogue within 150ms of a hard hit") aren't formalized as rules

---

## Architectural Gaps Not Covered By Phases 1–5

Things message.txt implies the engine needs that aren't in the current roadmap at all.

### 1. Prompt interpretation layer (zero stages for this)
Need a pre-scaffold stage:
- Edit-type classifier (tribute / action / sad / dance / dialogue-narrative / other)
- Tone inferencer when `--tone` not supplied
- Character/speaker role inference from prompt
- Auto-template selector from the 6 templates (or flag "custom arc needed")
- Output: `intent.schema.json` artifact that drives every downstream weighting

Without this the arc-architect (Phase 2) has no idea which arc shape to build.

### 2. Dialogue-narrative sub-pipeline
Four new stages, not in any phase:
- `dialogue_script` — prompt → ordered list of utterances the final edit needs ("line 1: defiant rejection", "line 2: recognition", "line 3: turn")
- `dialogue_search` — semantic + phonetic search across all ingested transcripts for utterances that match each script line, scored by (a) semantic match, (b) voice-register match, (c) speaker-gender match, (d) audio clarity
- `dialogue_lipsync` — mouth-ROI extraction + viseme alignment score per candidate; rejects clips where mouth shape is implausible
- `dialogue_place` — assigns each chosen dialogue clip to a `DIALOGUE_SAFE` window on the beat map

New agents: `dialogue-scriptwriter`, `dialogue-searcher`, `lipsync-scorer`.

### 3. Source profiling during ingest
Currently `ff ingest` does scene detect + Whisper + face rec + metadata. Needs to also produce a `source_profile.schema.json`:
- Luma histogram, chroma histogram, color-temp estimate
- Grain/noise floor
- Sharpness
- Source-type classification (anime / live-action / western-animation / 3d-render / archival / music-video / sports / documentary)
- Era bucket (crude: pre-2000 / 2000-2010 / 2010-2020 / post-2020)
- Letterbox/pillarbox detection
- Quality tier (your S–D axis, applied to the *source* not just the clip)

This is the feeder for Phase 3's visual signature DB. Doing it at ingest time means you never re-scan.

### 4. Feedback loop from Resolve back to priors
Engine outputs `.drp` / FCP XML → Nick edits in Resolve → renders final. Right now, none of Nick's manual edits flow back. The engine doesn't learn from use.

Needed:
- Resolve project diff reader (what cuts did Nick change / remove / reorder?)
- Prior updater that nudges shot-role weights, arc shape, and cliche thresholds toward Nick's actual taste
- Monthly "prior retrain" command

Without this, the engine is frozen at its initial calibration forever.

### 5. End-to-end quality regression suite
418 pytest + 76 vitest is unit/integration. No automated answer to "did this commit make rendered output worse?"

Needed:
- N reference projects (one per edit type, same inputs every time)
- `ff regress` — re-run all of them, score via `ff review`, alert if any project drops >2 grade points vs. last green commit
- Stored baselines in `regression/baselines/`

This is cheap to build and will save entire weekends of "why does the render feel worse now."

### 6. Variable output-length math
The engine handles 222s now. Message.txt explicitly requires variable length. Needs:
- Target-duration-aware arc scaler (compress 4-act into 30s? → single-beat version; stretch into 6min? → add rest beats)
- Per-length energy curve resampling
- Different QA thresholds for short vs long (missing a beat in a 30s edit is fatal; in a 6min edit it's forgivable)

### 7. SFX and diegetic audio handling
Current mixer: song + dialogue ducking. Not handled:
- Preserve-diegetic flag (keep the explosion sound on the punch)
- Score-strip flag (mute the source music in a clip when layering the new song)
- Voice-carry flag (keep a character's breathing/grunt even when dialogue is stripped)

These are editorial decisions the engine currently can't even represent.

### 8. Psych proxies as un-graded telemetry
Phase 5 says "schema stubs only." But several psych signals are measurable *now* and worth logging even without grading:
- Character screen-time balance (already partially in fandom-balance QA rule)
- Color-mood clustering per zone (low-hanging given existing color analysis)
- Beat-entrainment score (already implicit in beat-sync grade)
- Eyeline continuity (face rec + gaze estimate — a bit harder but not ML-only)

Store these. Don't grade yet. When you eventually calibrate against user data, you already have the history.

### 9. Clip category taxonomy (formal, shared)
Phases 1 and 2 both imply a clip taxonomy (for energy-zone mapping, for slot-fit scoring). But there's no `clip_category.schema.json` anywhere. Multiple modules will invent their own if this isn't nailed down first.

Proposed minimum taxonomy:
`establishing` · `action-high` · `action-mid` · `reaction-quiet` · `reaction-emotional` · `dialogue-primary` · `dialogue-reaction` · `transitional` · `climactic` · `resolution` · `texture` (b-roll / pattern)

Lock this before Phases 1 and 2 start.

### 10. Cross-edit-type QA rule weighting
The 10 QA rules run identically for every render. But:
- Fandom-balance matters in a crossover tribute, not in a character-specific one
- Beat-sync tolerance is tighter on action than on sad
- Cliche rules mean different things for dialogue-narrative (intentional catchphrases) vs. action (stock transitions)

Rules need an `applies_to: [edit_types]` plus per-type severity.

---

## The Corpus Problem (Actual Pre-Phase-1 Blocker)

You mentioned the YouTube reference playlists. Right now the engine only has **148 videos across 5 action playlists**, and open decision #6 flags that 133 of 148 still don't have Whisper transcripts. This is the actual top-priority blocker.

### Why it's a blocker
- Phase 1's clip metadata scoring has nothing to calibrate against for non-action types
- Phase 3's visual signature DB is "bootstrapped from the 148-video reference corpus" — so it will encode action-only signatures
- Phase 4's evaluation rubric weights are unlearnable without per-type reference examples
- Every "S-tier prior" in `reference-priors.json` is action-shaped

### Minimum corpus expansion before Phase 1 kickoff
Add playlists for the four missing edit types, ~30 videos per playlist, 2 playlists per type for variety:

| Edit type | Suggested playlist seeds | Target count |
| --- | --- | --- |
| Character tribute | High-view tribute edits, one anime-dominant + one live-action-dominant | 60 |
| Sad / emotional | Grief-focused edits, prioritize ones with distinct visual restraint | 60 |
| Dance / movement | K-pop fancam-style + movement-matching montages | 60 |
| Dialogue / narrative | Found-footage storytelling, "character says X" compilations | 60 |

That's ~240 new refs on top of 148 = ~390 total. Not a trivial ingestion but it's a weekend of `ff grab` + Whisper, and it transforms what the engine can actually learn.

### Corpus layering
Store priors per `(edit_type, fandom_family)` tuple, not global:
- `priors/action/anime.json`
- `priors/action/live-action.json`
- `priors/tribute/anime.json`
- `priors/tribute/western.json`
- etc.

This is the only way cross-media edits get meaningfully different priors than same-media.

### Do Whisper on the full corpus *first*
Open decision #6 asks "defer or parallelize." Answer: **do it first, before any Phase 1 code.** Without transcripts across the ref corpus you can't learn:
- Where dialogue lands relative to beats (the exact rule you're trying to build)
- Typical dialogue density per edit type
- Intelligibility norms
- The `~8 hrs CPU` cost is a one-time overnight job. Stop treating it as a Phase 1 tradeoff.

---

## Workflow Corrections

Specific reorderings against the current plan.

> [!warning] Current plan ordering problem
> The phases are organized by architectural layer (audio → sequencing → visual → eval → psych). That's logical but it front-loads all of Phase 1's audio polish before you've solved the corpus bias or the prompt interpretation layer, both of which are prerequisites for Phase 1 to actually produce better output.

### Proposed new Phase 0.5 — Groundwork
Before any Phase 1 code:
1. Whisper pass on all 148 refs (overnight)
2. Corpus expansion to ~390 refs across 5 edit types (1 weekend of grab + ingest)
3. Lock `clip_category.schema.json` and `intent.schema.json`
4. Build `intent` pre-scaffold stage (edit-type classifier + tone inferencer + template selector)
5. Build end-to-end regression suite (`ff regress` + 5 reference projects, one per edit type)

Only then start Phase 1. This is ~1.5 weeks of groundwork that prevents Phase 1 from shipping against stale priors.

### Ship vertical slices per edit type, not horizontal layers
Current plan is: finish all audio intelligence → then all sequencing → then all visual.

Alternative: pick one edit type (dialogue-narrative is the richest gap), build the full vertical slice end-to-end:
- dialogue_script + dialogue_search + lipsync stages
- dialogue-specific arc template
- dialogue-specific QA weights
- dialogue-specific review rubric

Ship. A/B-render. Then next vertical slice (sad/emotional). Each slice re-validates the foundational layers against a real target instead of "build everything horizontally, pray it composes."

### Defer ComfyUI and ML style transfer hooks to a real Phase 6
Open decision #5 ("ComfyUI / ML hooks — extend Phase 3, or a separate Phase 6?"). **Separate Phase 6.** Every ML hook is a rabbit hole of model management, GPU memory, version drift. Ship deterministic Phase 3 first, earn the right to add ML later.

### Cap vision-LLM spend with aggressive caching, not a monthly budget
Open decision #1. Cache by clip content hash + prompt hash. A given clip gets its metadata extracted exactly once, ever, per prompt version. Re-renders cost nothing. New clips are the only spend. This removes the decision from "budget guardrail" to "caching correctness."

### D-tier clip handling: default to refuse, with explicit opt-in
Open decision #2. Default `--allow-dtier=false`. If refused and the edit needs more material, engine suggests "upscale via ComfyUI (slow)" or "add more sources" as options. Don't silently denoise-and-hope.

### Tiering thresholds: calibrate from data, not from guessing
Open decision #4. After corpus expansion, the 6-axis composite scores across ~390 refs give you a natural distribution. Set Amateur/Competent/Exceptional at percentile breakpoints (e.g., 30th / 70th / 95th) and adjust later.

---

## Dialogue-Narrative Pipeline — Detailed Spec Sketch

Because this is the biggest missing vertical, here's what the added stages actually look like. Write this as a scratch design, not a commit-ready plan.

```
prompt + sources + song
      │
      ▼
  ┌──────────────┐    intent.json
  │   intent     │ ─────────────►  (edit_type=dialogue-narrative,
  │  classifier  │                  speakers=[...], tone=...)
  └──────────────┘
      │
      ▼
  ┌────────────────────┐  dialogue_script.json
  │ dialogue_script    │ ────────►  [{line: "...", intent: "defiant",
  │ (prompt → script)  │              speaker_role: "protagonist",
  └────────────────────┘              target_duration_ms: 1400}, ...]
      │
      ▼
  ┌────────────────────┐  beat_map + dialogue_windows.json
  │ beat_analyze       │ ────────►  [{start: 12.3, end: 14.1,
  │ + window detector  │              status: DIALOGUE_SAFE}, ...]
  └────────────────────┘
      │
      ▼
  ┌────────────────────┐  candidates per script line
  │ dialogue_search    │ ────────►  uses Whisper index + phonetic
  │ (semantic+phonetic)│              match + voice-register score
  └────────────────────┘
      │
      ▼
  ┌────────────────────┐  scored candidates
  │ dialogue_lipsync   │ ────────►  mouth-ROI viseme alignment,
  │ scorer             │              rejects implausible matches
  └────────────────────┘
      │
      ▼
  ┌────────────────────┐  final dialogue placements
  │ dialogue_place     │ ────────►  assigns each line to a SAFE window,
  │ (window assigner)  │              b-roll slots between/around
  └────────────────────┘
      │
      ▼
  ┌────────────────────┐
  │ propose_shots      │  now fills non-dialogue slots
  │ (b-roll chooser)   │  with thematic reinforcement clips
  └────────────────────┘
      │
      ▼
  ...existing qa_gate → roughcut → color → export
```

The key reorder: **beat zones + dialogue windows must be known before dialogue candidates are searched, because window count constrains line count.** If the song only has 4 DIALOGUE_SAFE windows, a 6-line script is infeasible and the engine should negotiate down to 4 lines before searching.

---

## Reference Playlist Strategy

Expanding on the corpus problem. You mentioned the playlists; here's how I'd structure their use.

### Playlist selection criteria
- Per playlist: 20–40 videos, curated (not auto-populated)
- Tagged at playlist level with `(edit_type, fandom_family, aesthetic_tier)`
- Videos within a playlist should have consistent *editorial* approach even if sources vary — e.g., "fast-cut Marvel tributes" is one playlist, "slow-build Marvel tributes" is another

### Per-video processing pass
On ingest:
1. `ff grab video` (already exists)
2. Full audio track → beat map + zone labels + dialogue window map
3. Visual profile (luma, chroma, grain, aspect, quality tier)
4. Scene detect + shot boundaries
5. Whisper full transcript with timing
6. Cut-boundary features: beat-distance-ms at each cut, motion-vector continuity, color delta across cut
7. Aggregate stats → `reference.schema.json` record

### Per-playlist stats → priors
For each `(edit_type, fandom_family)` bucket:
- Median shot duration + stdev
- Shot-duration distribution (histogram)
- Beat-sync precision (ms offset distribution)
- Dialogue density (dialogue seconds / total seconds)
- Dialogue-to-beat placement offsets (where in the bar does dialogue tend to land?)
- Color palette centroid + spread
- Aspect ratio mix
- Arc shape (energy envelope, normalized to 0–1 time axis)

These *are* the priors. Everything Phase 1, 2, 3, 4 does should compare a candidate render's stats against the matching `(edit_type, fandom_family)` bucket and flag outliers.

### Quality tier calibration
Your current S/A/B/C/D is a 6-axis composite. Problem: it was computed against the action corpus. Across 5 edit types the axes' *weights* should differ:
- Action: beat-sync weight high, variety weight high, narrative weight low
- Sad: beat-sync weight low, variety weight low, emotional-coherence weight high
- Dialogue-narrative: narrative weight very high, beat-sync moderate, dialogue-intelligibility critical

Ship per-edit-type weights as `tier_weights.json` and re-grade the corpus.

---

## The Six Open Decisions, Answered

> [!question] From the engine doc, with opinions
> 1. **Vision LLM budget** — cache by clip-content-hash. Effectively unlimited budget once caching is solid. Don't cap the raw call rate.
> 2. **D-tier clip handling** — default refuse, explicit `--allow-dtier` opt-in, AI upscale as a Phase 6 option.
> 3. **Psychology Phase 5** — ship proxy metrics as un-graded telemetry. Cheap and future-proof.
> 4. **Tiering thresholds** — calibrate from expanded corpus percentiles. Don't pick by hand.
> 5. **ComfyUI / ML hooks** — separate Phase 6, ruthlessly. Don't bloat Phase 3.
> 6. **Whisper expansion** — do it first, before Phase 1 code. Non-negotiable if you want non-action support.

---

## Test Additions That Actually Matter

Beyond "keep 418 pytest + 76 vitest green":

- `tests/regression/` — 5 reference projects (one per edit type), rendered + reviewed on every green commit
- `tests/corpus/` — sanity check that priors aren't drifting off expected distributions when new refs are added
- `tests/dialogue/` — synthetic "this character must say X" test with known-good sources, asserts placement lands in DIALOGUE_SAFE
- `tests/lipsync/` — visemes for ~20 hand-labeled clips, asserts scorer matches ground truth
- `tests/intent/` — prompt → edit-type classifier test set, ~50 labeled prompts

---

## Risk Ranking

> [!danger] If you only fix three things before Phase 1
> 1. Corpus expansion + Whisper everything (1 weekend + overnight)
> 2. Prompt interpretation layer / intent schema (2–3 days)
> 3. End-to-end regression suite (2 days)
>
> Everything else in Phases 1–5 is much higher ROI once those three are in place.

---

## Related

- [[FandomForge Engine]] — the source doc this analyzes against
- [[002 - Overview]] — projects overview
