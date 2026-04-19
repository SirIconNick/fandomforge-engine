# Your First Edit — Full Walkthrough

An end-to-end walkthrough from idea to export-ready plan. Takes about an hour.

## Prerequisites

- FandomForge installed (`./scripts/setup.sh` ran clean)
- Python env active: `source .venv/bin/activate`
- A song you legally own (MP3, WAV, or FLAC)
- An NLE you know (Resolve / Premiere / CapCut / Vegas)
- Claude Code with agents symlinked

## Step 1 — Pick your theme

Before you touch any tool, answer one question:

**What is this edit about, in one sentence, 12 words or fewer?**

If you can't, go read [theme-patterns.md](../knowledge/theme-patterns.md) and pick a framework. Come back when you can finish "this edit is about ___."

## Step 2 — Create the project

```bash
ff project new my-first-edit --theme "Your one-sentence theme"
```

You'll get a folder at `projects/my-first-edit/` with three templates pre-filled.

## Step 3 — Run beat analysis on your song

```bash
ff beat analyze path/to/song.mp3 -o projects/my-first-edit/beat-map.json --song "Title" --artist "Artist"
```

You should see BPM, beats, drops, buildups, breakdowns, and an energy curve. If BPM looks wrong (e.g., detected 70 BPM but you know the song is 140), re-run with a hint:

```bash
ff beat analyze song.mp3 -o projects/my-first-edit/beat-map.json --tempo-hint 140
```

## Step 4 — Review the beat map in the dashboard

```bash
# In another terminal
cd web && pnpm dev
```

Visit `http://localhost:4321/projects/my-first-edit`. You'll see the beat-map visualized — downbeats, drops, buildups, energy curve.

Verify visually:
- Drops fall where you'd expect (usually the loudest moments)
- Downbeats feel right when you scrub through your song
- Energy curve shape matches the song's intensity

If something looks wrong, re-run beat analyze with different options.

## Step 5 — Kick off the edit-strategist

In Claude Code, inside this project folder:

```
@edit-strategist I want to start a new edit. Here's the info:
- Theme: [your theme]
- Song: [title + artist] ([duration])
- Fandoms: [list]
- Vibe: [action / emotional / hype / sad / mixed]
- Length: [target duration]
- Platform: [YouTube / TikTok / multi]

The beat map is at projects/my-first-edit/beat-map.json.
```

The strategist will propose:
- A structure archetype (Classic / Hype Burst / etc.)
- An act breakdown with timing
- A delegation plan to the other experts

Review and push back if anything's off. The strategist should name the archetype, tell you which acts each fandom owns, and what the final image is.

## Step 6 — Lock the theme with story-weaver

```
@story-weaver Here's my structure: [paste from strategist]. 
Build the theme arc across my fandoms in [Convergent/Parallel/Relay/Woven] mode.
```

You'll get a story map — which fandom does what per act, character parallels, what the final image is, what NOT to include.

Copy this into your `edit-plan.md` under the act breakdown.

## Step 7 — Research scenes

```
@fandom-researcher For theme "[theme]", find me the top scenes for:
- [fandom 1]: [specific emotion/beat needed]
- [fandom 2]: [specific emotion/beat needed]
- [fandom 3]: [specific emotion/beat needed]
```

You'll get timestamped scene proposals ranked by iconic-vs-deep-cut. Pay attention to which are flagged as overused.

## Step 8 — Build the shot list

```
@shot-curator Here's my act plan and the scenes from fandom-researcher.
Build the shot list with the 4-criterion filter (theme, framing, emotional, beat).
Target [X] shots per act.
```

You'll get a full shot list with source, timestamp, mood, beat target, and scores. Copy into `shot-list.md`.

Verify:
- No fandom exceeds 40% of shots
- Each shot shares at least one visual element with its neighbors
- Scores all pass 14/20

## Step 9 — Plan the visual language

Run these three in sequence (or parallel):

```
@color-grader [paste theme + shot list + a statement about what mood you want]
@transition-architect [paste shot list + theme — design the transition plan]
@title-designer [paste theme + platform — decide if text is needed]
```

You'll get:
- A color plan with target look, LUT recommendation, and per-source adjustments
- A transition plan with cut type for each shot pair
- A text decision (often "no text" — and that's fine)

## Step 10 — Plan the audio stack

```
@audio-producer Here's my song and structure. Plan the SFX layers and any ducking.
```

You'll get:
- Impact SFX for each drop
- Whoosh SFX for transitions
- Riser/downlifter for buildups
- Ducking recommendations
- Final mastering target (-14 LUFS)

## Step 11 — Execute in your NLE

```
@editor-guide I use [Resolve/Premiere/CapCut/Vegas]. Here's my plan. Give me the concrete steps for my NLE.
```

You'll get:
- Project settings (resolution, frame rate, color space)
- Bin structure
- Cutting workflow with shortcuts
- Color node tree (if Resolve) or Lumetri flow (if Premiere)
- Audio routing
- Export specs for your platform

## Step 12 — Import beat markers

```bash
python scripts/markers-to-resolve.py projects/my-first-edit/beat-map.json -o projects/my-first-edit/markers.edl --fps 24
```

Import the EDL into your NLE. You now have downbeat markers at every bar and drop markers for your peaks.

## Step 13 — Cut

Now actually edit. Use your shot list as the recipe. Use the beat markers to align cuts. Trust your plan.

Watch your first full assembly once. Note where it's boring or confused. That's your feedback loop.

## Step 14 — Publish

Follow the credit / fair use notes in the edit plan's legal section. Reminder: credit song, credit sources, don't monetize if risky.

## What went wrong is usually one of these

- **Theme drift** — you included a shot that doesn't serve the theme. Cut it.
- **Pacing flat** — your energy curve has no valleys. Find or create rest sections.
- **Fandom imbalance** — one source is dominating. Cut it back to <40%.
- **Color mismatch** — sources weren't unified. Re-check color plan.
- **Transitions stacking** — you piled effects on a single cut. Pick one.
- **Overused shots** — clichés are showing. Find alternatives.

For each, there's an expert. Loop them back in.

## Your second edit is 3x faster than your first

You now know the workflow. The tools are the same. The experts are the same. What changes is your taste. Keep making.
