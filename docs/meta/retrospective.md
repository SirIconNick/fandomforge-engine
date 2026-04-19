# Retrospective — What Went Wrong

This document is the honest post-mortem after the user watched the demo output and rated it "dogshit." It's right. Here's why, and what we're changing.

## What the user watched

A 30-second demo video produced by the pipeline with these inputs:

- **"Song":** a 220 Hz sine wave tone generated with ffmpeg (literally a test tone, not music)
- **"Dialogue":** three 2-second WAV files extracted from random timestamps (1:00, 3:30, 5:00) of the Leon compilation. Those weren't actual dialogue lines — they were random audio (probably background music from the compilation).
- **Shot list:** 15 shots pulled from random timestamps in the 15-minute Leon compilation
- **Color:** `crushed_noir` preset applied as a heavy ffmpeg filter chain
- **Length:** 30 seconds (intentionally short for a pipeline proof)

The user's reaction: "color grading, audio, dialoge, it was only 30 seconds... dogshit."

All of those complaints are valid. Let me break them down honestly.

## What went wrong

### 1. The demo had fake inputs, not real inputs

The demo was a **pipeline proof**, not a real edit. The goal was "does the code work end-to-end." It did. But I demoed it using synthetic inputs (a test tone, random audio snippets) and called the output "a rough cut" when it was really just "the pipeline's output on garbage inputs."

**The lesson:** stop confusing "the pipeline runs" with "the pipeline produces a good edit." A pipeline that runs with bad inputs produces bad output. That's not a pipeline bug — it's a framing bug. I should have been clearer that the demo wasn't a real edit.

### 2. Color grading was heavy-handed

My `crushed_noir` preset applies:
- A steep curve that crushes blacks hard
- A blue/cyan shadow tilt
- -15% saturation
- +25% contrast

Applied to ALREADY-graded source material (Leon compilation from fan editors who already color-graded it), it stacked on top of existing grading and produced an over-processed look.

**The lesson:** colored fan-compilation sources have their own grading already. Applying another heavy preset on top is double-grading. The preset defaults were designed for NEUTRAL sources, not pre-graded ones.

### 3. Audio mixing was crude

- The "song" was a pure tone, not music. No dynamics, no emotional content.
- The sidechain compressor was set to 3:1 ratio with 50ms attack — that's aggressive. For a real song it'd duck noticeably.
- The dialogue WAVs were 2-second random clips, not actual lines. So what the user heard was random background music from the Leon video, ducking the test tone.
- No EQ, no reverb matching, no level normalization per-clip.

**The lesson:** the audio mixer's defaults are fine for the "layer dialogue over a song" case but need softening for quieter songs. And the demo's audio was inherently broken by the input choice.

### 4. 30 seconds isn't an edit

The user's complaint about length is fair. 30 seconds of rough cut is not a multifandom edit. It's a test fixture. Real multifandom edits are 2-5 minutes. A demo that produces 30 seconds doesn't show what the pipeline can do.

**The lesson:** demos should use realistic lengths.

### 5. The pipeline has no quality gate

The pipeline runs all the way to completion regardless of whether the output is good. There's no QA step that says "this shot is black, this audio clip is silent, this color looks wrong." So broken outputs get produced and called successful.

**The lesson:** need a quality review step.

## What went right

To be fair to the pipeline:

- **Architecture is sound.** The pipeline (parse → assemble → color → mix → mux) works end-to-end in one command.
- **Parallelism works.** 15 clips extracted in seconds with ThreadPoolExecutor once we fixed the stderr deadlock.
- **The modules are isolated.** Each stage (extract, mix, grade, concat) can be tested independently.
- **The knowledge base is solid.** Theory documents (beat sync, transitions, color, dialogue ripping) are genuinely useful reference.
- **The experts (10 agents) are well-scoped.** They each own one domain with clear delegation.
- **The sources catalog is real.** 27 real YouTube URLs, confirmed, downloadable, with character mapping.

The problem isn't the foundation. The problem is the defaults, the demo inputs, and the lack of a quality gate.

## Changes for future runs

### Quality-focused pipeline defaults

1. **Color preset defaults** — change from heavy `crushed_noir` default to neutral `none`. Let the user opt-in to color grading.
2. **Audio ducking defaults** — change sidechain ratio from 3:1 to 2:1, slower attack (100ms vs 50ms), longer release (500ms vs 300ms). Subtler.
3. **CRF default** — from 22 to 20 (slightly higher quality).
4. **ffmpeg preset** — from `ultrafast` to `fast` (slightly slower but better quality; ultrafast is for testing only).
5. **Validation step** — before pipeline starts, check: song duration > 30s? dialogue WAVs exist? source videos exist? sources have decent duration?
6. **Length gate** — warn if shot list total runtime < 60s (probably a test shot list, not a real edit).

### New QA agent

`qa-reviewer` — runs after the pipeline completes. Analyzes the output MP4:
- Are there black shots longer than expected?
- Is the audio clipping or whisper-quiet?
- Is the bit rate reasonable for the resolution?
- Does the shot count match the plan?
- Does the runtime match the plan?
Produces a red/yellow/green quality report.

### Preview-before-commit

Add `--preview` mode that generates thumbnails for every shot and a low-res quick-render BEFORE the full render. User can approve or reject before investing in the full pipeline.

### Per-clip fine-tuning

Add `ff clip fine-tune` command that lets the user adjust a single shot's timing, color, or in/out points without re-running the whole pipeline.

### A/B color comparison

Let the user generate the SAME shot with 4-8 different color presets side-by-side. Pick the best one before applying to the whole edit.

### Honest demo inputs

Stop using sine waves as "songs" and random timestamps as "dialogue." Either:
- Use the user's real inputs (they own the Madonna song — use THAT)
- Use public-domain test content that actually sounds like music
- Clearly label synthetic demos as "technical proof — not representative of output quality"

## The web app plan

The user asked for a web app with visuals, editor, and player. Given the quality issues, the app's job is to make problems VISIBLE before they become bad output:

1. **Pipeline runner** — see progress, see the output video when done
2. **Timeline editor** — see the shot list visually, click to preview each clip
3. **Video player** — watch the output with keyboard shortcuts, shot markers
4. **Color A/B** — see 4+ presets side-by-side, pick one
5. **Dialogue timing** — see dialogue cues on a timeline, drag to adjust, hear in context
6. **Quality dashboard** — red/yellow/green on each stage of the pipeline

The app is the answer to "I watched the output and it's bad." It becomes the place where you CATCH that it's bad before rendering, not after.

## The honest one-liner

> The pipeline runs. The pipeline is not yet a good editor. Humans + the web app close that gap.

Rough cuts are called "rough" for a reason. The pipeline gets you to "15 clips assembled on the beat with a base audio mix and one color pass." The web app, the QA agent, and your NLE finish the job.
