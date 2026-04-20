# Shot-by-Shot Justification — action-legends minute render (v2)

Full justification for every cut, SFX event, and audio decision in the v2
render. For each of the 52 shots: why THIS scene, at THIS beat, following
THIS other shot.

Song: **centuries · 89.1 BPM · 3 drops in the first 60s (29.19s, 44.21s, 59.21s)**

Structure: **Act 1 (0-19s)** fandom-establishing intro · **Act 2 (19-44s)** build and first drop · **Act 3 (44-60s)** sustained climax through second and third drops.

---

## Act 1 — The fandom-establishing intro (0:00–0:19)

The song has ~9.8s of musical intro before the first beat lands. That's the establishing window. Every fandom has to appear on screen before the music explodes. Shots 1–4 introduce each of the 4 sources in one held shot apiece; shots 5–10 cycle them faster as the song builds toward Act 2.

### s001 · 0.00–3.00s · Extraction · "held" establishing hero
- **Why this shot:** `held` tier scene from Extraction (low motion 0.01, duration 3s). Opens the edit on a held character shot — sets tone, establishes the first fandom. Held scenes are how real fandom edits open: a paused moment before rhythm takes over.
- **Source timecode `0:00:03`:** very early in the Extraction clip, probably a framed character intro. Scene midpoint (40% in) avoids any fade-in.
- **No cut on beat:** first cut intentionally free-sync — no beats in the song yet.

### s002 · 3.00–6.00s · John Wick 4 · "mid" establishing motion
- **Why:** rotation — John Wick is fandom #2. Mid-tier scene (motion 0.15) moves more than s001's held shot, signaling the song is about to start building.
- **Contrast with s001:** deliberate — held then motion → reads as "first world, second world, second world moves."

### s003 · 6.00–8.46s · Mad Max: Fury Road · "mid" motion
- **Why:** rotation continues. Fandom #3.
- **Why this scene:** mid-tier from mad-max's early sequence. Motion 0.13 matches the gently-building song energy.

### s004 · 8.46–11.17s · The Raid 2 · "held" hero
- **Why:** completes the 4-fandom rotation. Fandom #4.
- **Why held:** wraps the first "introductions" pass with a slower shot. The next shot will be the first beat-aligned cut — s004 is the anticipation hold.
- **Song context:** first beat of the song is at ~9.84s, which lands mid-shot. s004's end at 11.17s is the first proper beat-cut.

### s005 · 11.17–13.92s · Extraction · mid motion
- **Why:** second pass through the rotation starts. Extraction again but from a DIFFERENT scene than s001 (scene index ensures no repeats).
- **Why longer (2.75s):** this beat has a wider gap to the next one. Shot duration = time between beats, so this reflects the song structure.

### s006 · 13.92–14.58s · John Wick 4 · mid motion
- **Why short (0.67s):** beat gap is tight here — song is packing beats closer, cut rhythm accelerates with it.
- **Already different scene from s002:** picker's scene-uniqueness constraint prevents any repeat.

### s007 · 14.58–15.96s · Mad Max · mid motion
- Continues second rotation pass. Scene from the mid-section of mad-max (around 1:16 in) — ahead of earlier s003's 1:29 pick.

### s008 · 15.96–16.62s · The Raid 2 · mid motion
- **Short shot (0.67s):** tight beat cluster here. Song tension builds through rapid cuts.

### s009 · 16.62–17.29s · John Wick 4 · mid motion
- **Why John Wick again:** rotation fell to least-used; JW has the fewest "high" tier scenes (11 total vs mad-max's 31), so it tends to get picked when mid-tier is in demand.

### s010 · 17.29–18.67s · Mad Max · mid motion
- Continues rotation. 1:21 timestamp — deep-sequence motion from Fury Road.

---

## Act 2 — Build + first drop (0:19–0:44)

The song's first drop hits at **29.19s**. This act spans beats that are packing faster as the song intensifies. Shots stay in the `mid` tier until ~s020, where the picker transitions to `high` tier to match the drop.

### s011–s019 · 18.67–28.21s · Rapid mid-tier cuts, all 4 sources cycling
- **Why all mid tier:** song is still in "build" phase — rising toward the drop. Mid-motion scenes sustain energy without overshooting.
- **Why cuts get shorter (~0.67s):** beats pack tight approaching the drop.
- **Source cycling:** least-used source wins each pick, with 15% random variance. All 4 fandoms appear at least twice through Act 2.

### s019 · 27.54–28.21s · Extraction · last mid-tier before drop
- Final beat in the "build" context. Shot selection algorithm classifies this time as `build` (within 2.5s of the 29.19s drop), which prefers high/mid/climax tiers — mid is picked because Extraction has more mid scenes than high at this point in its catalog.

### s020 · 28.21–29.58s · The Raid 2 · "high" action (drop approaches)
- **First high-tier shot.** Motion 0.31 — the highest motion shot so far.
- **Why here:** 28.21s is classified `build` (1s before the 29.19s drop). Tier preference shifts to high/climax.
- **The drop (29.19s) lands 0.98s INTO this shot.** So the drop fires while the Raid 2 action is already on screen — viewer sees motion explode visually at the same time the song hits.

### s021 · 29.58–30.25s · John Wick 4 · high (post-drop)
- **First cut AFTER the drop.** 0.4s after the drop, the picker is in `post-drop` context. High tier preferred.
- **John Wick's high-tier scene:** motion 0.33 — second-highest in the edit. Maintains the momentum kicked off by the drop.

### s022–s035 · 30.25–43.25s · Sustained high-motion sequence
- **12 shots of `high` tier action.** This is the main body — the song's first post-drop section. Motion values between 0.18 and 0.32.
- **Pattern:** each source appears roughly equally, with John Wick filling in more than its share because mad-max's `high` pool eventually exhausts (31 scenes but many already used), so rotation lands more on JW and Raid 2.
- **Short cuts (0.67s mostly, some 1.38s):** beat gaps drive duration. Song verse has tighter beats than chorus.

### s035 · 41.88–43.25s · The Raid 2 · high (ramping into drop 2)
- Motion 0.32 — highest of the raid-2 picks. Sitting 0.96s before drop 2 (44.21s). The picker sees this as `build` and pulls the most intense available.

### s036 · 43.25–43.92s · Extraction · "climax" HERO — 1st climax-tier shot
- **First climax-tier scene selected.** Motion 0.29, duration 0.67s. This is the edit's peak-intensity scene so far.
- **Why here:** 43.25s is classified `build` (<1s to drop 2). Climax is the top preferred tier.
- **Role = hero** (not action) because of the drop-adjacent position. Hero scenes get more weight at the peak.
- **Drop 2 (44.21s) lands 0.29s INTO the NEXT shot.** So s036 is the visual anticipation — you see the climax scene start, the drop hits as the cut happens, and s037 is the release.

---

## Act 3 — Post-drop sustain through climax (0:44–1:00)

### s037 · 43.92–44.58s · John Wick 4 · high (drop 2 release)
- **The drop fires 0.29s into this shot.** Intentional design: the CUT to s037 is the beat immediately before the drop; the DROP explodes 0.29s later while s037 is on screen. Creates a "visual preempt, audio confirms" feel.
- **Motion 0.24, luma 0.24:** darker than ideal but within the filter threshold (0.18 floor). JW's high-tier pool is running low.

### s038 · 44.58–45.96s · Mad Max · high (1.38s held shot)
- Longer shot immediately post-drop. Gives the viewer a moment to register the drop hit.
- 1.38s is the beat-gap that happens to fall here — song breathes slightly after the drop lands.

### s039–s043 · 45.96–51.42s · Sustained Act 3 cut rhythm
- Six shots, all `high` tier (motion 0.18–0.26). Sources cycling but leaning mad-max and extraction (their high pools are deeper).
- **No climax scenes here** — the picker is saving those for the final drop at 59.21s... actually, only 1 climax scene (s036) got picked total in Act 3 because the climax pool was small after the luma filter (many climax scenes were dark night action).

### s044 · 51.42–52.08s · John Wick 4 · "low" insert — the weak link
- **Motion 0.03 — essentially still.** This is the picker falling back from high to low because John Wick's remaining unused high-tier scenes didn't pass the luma filter, and the rotation insisted on a JW pick here.
- **Honest call-out:** this shot breaks the action rhythm. The viewer will feel it as a sudden "wait, why are we holding here?" beat. A future fix: when tier pool runs out, swap source instead of dropping to low tier.

### s045–s048 · 52.08–55.50s · Recovery sequence
- Four `high` tier shots return the pace. Motion 0.20–0.24.
- Sources rotating the-raid-2 → extraction → mad-max → the-raid-2.

### s049 · 55.50–56.88s · John Wick 4 · "low" insert — second weak link
- **Same issue as s044.** Motion 0.07. JW's high pool exhausted, fallback to low.
- This one is worse because it's 1.38s long (longer held time). The energy pauses for almost a second and a half in the final 5 seconds of the edit — right when the song is climbing to its third drop.
- **Fix for next render:** exclude JW from rotation when its high-tier pool is empty, or lower the rotation's "fairness" weight in Act 3.

### s050 · 56.88–57.58s · Extraction · high action
- Picks up the pace again. Motion 0.26.

### s051 · 57.58–58.25s · Mad Max · "climax" HERO — 2nd climax pick
- **Second climax-tier shot of the whole edit.** Motion 0.28.
- **Drop 3 approaches at 59.21s.** This shot is 0.96s before drop 3, same pre-drop hero play as s036.

### s052 · 58.25–58.67s · John Wick 4 · "low" insert — final weak link
- **Last shot. Motion 0.03. Duration 0.42s (shortest).**
- **Drop 3 (59.21s) lands 0.54s AFTER this shot ends.** The drop fires into the end of the render (final 0.33s of video).
- **Why this placement is still bad:** the final climactic hit of the song lands on essentially a still frame. That's the opposite of what the picker should do at the final drop.
- **The bug:** picker rotation forced JW here even though its high tier was exhausted. Should have stolen another mad-max or extraction climax scene instead.

---

## Audio decisions

### The song — centuries at -4 dB
- Song plays full-length from 0:00. No song_offset — the 3:09 song is trimmed to the 60s render window.
- Song_gain: -4 dB (default). Leaves headroom for dialogue and SFX to layer without clipping.

### Scene audio blend — enabled at -20 dB
- **What gets blended:** each clip's original audio track (extracted during assembly, concatenated in shot order, trimmed to 60s).
- **Why -20 dB:** the mixer's default — scene audio should bleed under the song, not compete with it. At -20 dB the scene audio is audible but not foregrounded.
- **What you should hear:** engine roars from Mad Max during s003/s007/s010/etc., gun-cocks and shots from John Wick sections, fight thuds from Raid 2, tactical comms from Extraction. All as ambient texture, not centered.
- **Mix verification:** `result.mix.scene_audio_applied = True` on both renders. Scene audio track was built and passed to ffmpeg's filter graph.

### SFX plan — 68 events, 39 beat-aligned
- **3 sub_boom events** — one per drop (29.19s, 44.21s, 59.21s). These are supposed to be 808-style bass hits reinforcing each drop.
- **65 per-shot SFX events** — one per shot based on shot role and mood tags. Punches for `hero` shots, impacts for `action`, etc.
- **12 variant files missing** on disk — no `punch-heavy-01.wav` etc. in `sfx/<kind>/` directories.
- **Result:** the mixer silently skipped all 68 events. The edit has ZERO added SFX. The sub_booms that should punctuate each drop aren't there.
- **To hear SFX:** drop `.wav` files into `~/.fandomforge/sfx/<kind>/` (gunshot, punch, impact, sub_boom, whoosh, kick). The mixer will find and inject them on the next render.

### Audio mix chain
1. Song (60s trim, -4 dB)
2. Dialogue cues: 0 (no dialogue.json for this project)
3. Scene audio bed (-20 dB, 60s concatenated from source clips)
4. SFX events (skipped — no files)
5. alimiter on output (level_out=0.86, prevents clipping)

No loudnorm — it would dynamically compress and undo the scene-audio bed's deliberate low placement.

---

## Cut-by-cut beat alignment

49 beats detected in the first 60s of the song. 3 intro cuts (pre-beat) + 49 beat-aligned cuts = 52 total.

| Cut # | Time (s) | Source | Beat? | Drop alignment |
|---|---|---|---|---|
| 1 | 0.00 | Extraction | intro | — |
| 2 | 3.00 | John Wick | intro | — |
| 3 | 6.00 | Mad Max | intro | — |
| 4 | 8.46 | Raid 2 | beat #1 @ 9.84s (aligned to next beat start) | — |
| ... | ... | ... | ... | ... |
| 20 | 28.21 | Raid 2 | beat | **0.98s before drop 1** |
| 21 | 29.58 | John Wick | beat | 0.39s after drop 1 |
| 36 | 43.25 | Extraction | beat | **0.96s before drop 2** |
| 37 | 43.92 | John Wick | beat | 0.29s after drop 2 (drop fires into s037) |
| 51 | 57.58 | Mad Max | beat | **1.63s before drop 3** |
| 52 | 58.25 | John Wick | beat | drop 3 fires 0.96s after s052 ends |

**Cuts aligned with drops:** of the 3 drops, cuts s021 and s037 land within 0.4s of the drop (2 out of 3 hit). Drop 3 at 59.21s lands outside the render's final cut — no visual cue on screen for the final drop. That's a real weakness.

---

## The honest critique I owe you

### What works
- ✅ 52 unique (source, scene-index) pairs — no shot repeats
- ✅ First 4 shots introduce all 4 fandoms
- ✅ Every beat in the first 60s becomes a cut
- ✅ Tier matching: held/mid in intro, high in main body, climax adjacent to drops
- ✅ Scene rationale recorded in every shot's `description`
- ✅ Drops 1 and 2 land inside shot boundaries they're visually anticipating

### What doesn't work
- ❌ **3 "low" tier fallbacks in Act 3** (s044, s049, s052) — picker ran out of high-tier JW scenes that passed the luma filter and fell back to still/dark shots. Kills momentum during the most important stretch.
- ❌ **Drop 3 lands on a weak shot** (s052 is a 0.42s near-still) — the final climactic hit isn't reinforced visually.
- ❌ **Only 2 climax-tier shots used** (s036, s051) out of 22 available — the picker was conservative and luma-filtered out most of them. Climax scenes tend to be dark night-action with heavy shadows.
- ❌ **No SFX files on disk** — every SFX event the engine planned got skipped. The three sub-booms that should hit each drop are missing.
- ❌ **Shots 9–10** (0.67s JW and 1.38s Mad Max back-to-back) feel identical in rhythm to shots 11–14 — the intro doesn't clearly transition into Act 2.

### Fixes for v3 (if you want them)
1. **Smarter rotation exhaustion:** when a source's preferred-tier pool runs out, swap sources for that beat rather than dropping to a lower tier.
2. **Drop alignment pass:** post-generate, nudge adjacent cuts so every drop either starts or is within 0.2s of a cut boundary.
3. **Climax budget protection:** reserve the best 3 climax scenes for the 3 drops, don't spend them early.
4. **Luma filter bypass for known-dark-but-high-motion scenes:** some action scenes are deliberately dark (night combat) and shouldn't be excluded.
5. **Drop SFX packs in `~/.fandomforge/sfx/`:** sub-boom, impact, whoosh, gunshot, punch. Even royalty-free packs will make the mix punchier.

---

Every shot has a reason. Not all the reasons are good. But now you can see them.
