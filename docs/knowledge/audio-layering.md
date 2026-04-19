# Audio Layering

The song isn't the full audio of a great edit. Great edits have 4-6 layers stacked carefully. The song is the spine, but SFX, ducking, ambience, and vocal selects are the skin.

## The 6-layer stack

### Layer 1: The song (main music)
The spine. Sits at -6 to -4 dB peak. Everything else is either subservient to it or carved around it.

### Layer 2: Dialogue / VO selects
Spoken lines from the sources (characters, narrators, trailer voice). Used sparingly — 2-4 moments in a 2-minute edit max. When present, the song ducks -9 to -12 dB under them.

### Layer 3: Impact SFX
Boom hits, cinematic braam hits, bass drops. On major cuts and drops. Sitting -3 to -6 dB below the song, so they punch but don't overpower.

### Layer 4: Whoosh / transition SFX
Between shots, especially on whip pans and match cuts. Usually -12 to -18 dB — felt more than heard.

### Layer 5: Risers / downlifters
Building into drops — white noise sweeps, synth risers, reverse cymbals. Crescendoing from -24 dB to -6 dB over the buildup length.

### Layer 6: Ambience beds
Under quiet sections — pads, hums, textures. Very low (-20 to -30 dB). Adds spatial depth without being consciously heard.

## Why layered audio matters

Compare:
- Song alone + hard cuts on beats: the edit exists. It's fine. Nothing's wrong.
- Song + impacts on cuts + whooshes on transitions + risers into drop: the edit feels 10x more produced. Cuts land harder. Transitions feel intentional. The drop actually drops.

Layered audio is the difference between an edit that looks good and an edit that SOUNDS like a professional production.

## The impact SFX spec

Not all impacts are equal. Pick the right one for the moment.

### Boom / sub impact
Low-frequency hit. Felt in the chest.
- Use: big cuts, drops, bass-heavy moments
- Frequency: 30-120 Hz
- Duration: 200-400ms with reverb tail
- Volume: -6 dB below song peak

### Cinematic braam
Horn stab / orchestral hit. Cuts through the mix.
- Use: dramatic reveals, act transitions, tension peaks
- Frequency: full spectrum, emphasis 200-800 Hz
- Duration: 500ms-1.5s
- Volume: -3 to -6 dB below song

### Riser-impact combo
Riser builds, impact lands. Packaged as one effect in many SFX libraries.
- Use: pre-drop to drop
- Duration: 2-4 seconds total
- Volume: riser crescendoes -24 dB → -3 dB, impact at -3 to 0 dB

### Metal impact
Sword clang, metallic hit. Sharp, short.
- Use: action hits, weapon-focused moments
- Frequency: emphasis 800-4000 Hz
- Duration: 100-200ms

### Glass break / shatter
Breaking moment — character or reality breaking.
- Use: character snapping, reveal moments
- Frequency: emphasis 2000-8000 Hz
- Duration: 300-600ms

## The whoosh spec

Whooshes cover transitions. Pick by speed of the transition.

### Fast whoosh
- Duration: 200-400ms
- Use: whip pans, fast cuts
- Volume: -12 to -18 dB

### Medium whoosh
- Duration: 500-800ms
- Use: slower transitions, match cuts
- Volume: -12 to -18 dB

### Slow whoosh / swell
- Duration: 1-2 seconds
- Use: dissolves, dramatic reveals
- Volume: rising from -24 dB to -12 dB

## Ducking (sidechain compression)

When two audio elements compete, the less-important one should step back.

### Song-under-dialogue
- Song ducks -9 to -12 dB
- Attack: 50-100ms (fairly quick)
- Release: 300-500ms (gradual back up)
- Trigger: the dialogue track

### Song-under-impact
- Song ducks -3 to -6 dB for 200-400ms around the impact
- Prevents the impact from being muddied by song content

### Almost never
- Don't duck the song under ambience or whooshes. Too subtle to notice.

## The loudness target

Your final mixed output needs to hit a platform-specific loudness level. Louder than target = platform turns you down and quality degrades.

| Platform | Integrated LUFS | True Peak |
|---|---|---|
| YouTube | -14 LUFS | -1 dBTP |
| TikTok | -14 to -16 LUFS | -1 dBTP |
| Instagram Reels | -14 LUFS | -1 dBTP |
| Spotify | -14 LUFS | -1 dBTP |
| Broadcast (rarely relevant) | -23 LUFS | -2 dBTP |

Measure with an integrated LUFS meter. DaVinci Resolve's Fairlight has one, Premiere has Loudness Radar, or use free LUFS meters (Youlean Loudness Meter is free and excellent).

## EQ zones for each layer

Different layers need different frequency real estate. If everything sits in the same zone, your mix is muddy.

| Layer | Sits in | Carve out |
|---|---|---|
| Song (full-range) | Everything, but especially 100-4000 Hz | |
| VO selects | 200-4000 Hz (voice fundamental + clarity) | Song: dip -3 dB at 1-3 kHz |
| Sub impacts | 30-120 Hz | Song: dip -6 dB at 30-100 Hz during impact |
| Whooshes | 300-6000 Hz | — |
| Cinematic braam | Full range, emphasis 200-800 Hz | Song: duck during braam |
| Ambience beds | Below 200 Hz or above 4000 Hz | — |

## Free SFX libraries

All have usable free tiers:

- **Pixabay Audio** — CC0, no attribution needed
- **Mixkit** — royalty-free SFX, commercial use OK
- **Freesound.org** — wide variety, check per-file license (CC0 or CC-BY)
- **Zapsplat** — free with account, attribution required on free tier
- **BBC Sound Effects** — 16,000 sounds, non-commercial use free
- **YouTube Audio Library** — mostly music but some SFX, all free for YouTube use

## Paid libraries worth it

If you edit regularly:
- **Epidemic Sound** ($15/mo) — huge SFX library + music
- **Artlist SFX** ($$) — very high quality SFX
- **Musicbed** — expensive but cinematic-grade

## The pre-drop audio pattern

The 4-8 seconds before your main drop is your audio tour-de-force. Stack:

1. **Riser** — white noise high-pass filter sweep, building from quiet to loud
2. **Reverse cymbal** — under the riser, gives shimmer
3. **Vocal ad-libs** (if your song has them) — can re-use a lyric clipped and reversed
4. **Sub pre-drop swell** — sub-frequency growth, felt under the floor
5. **1-2 "gap" frames** right before the drop — everything drops to silence for 2-4 frames, then:
6. **Main impact** — lands on the drop, full stack

This is the signature of a great edit audio-wise. Everyone uses it because it works.

## The drop silence trick

Counterintuitive: for maximum impact, cut to silence for 2-4 frames RIGHT before the drop. Then hit the drop at full force.

The silence gives the drop somewhere to fall FROM. Without it, the drop has to compete with the rising buildup, and it doesn't feel as big.

## Common audio mistakes

### "It's too loud"
If your edit is louder than -14 LUFS, the platform will turn you down non-uniformly. Transient peaks survive, sustained peaks compress. Result: everything sounds flat.

### "It's too quiet"
Under -18 LUFS and viewers on mobile with earbuds won't hear it. They'll swipe.

### "Impacts don't feel big"
You didn't duck the song. The impact is fighting the song's existing content at the same moment. Duck the song -3 to -6 dB for 200-400ms around the impact.

### "Whooshes sound cheesy"
You used preset whoosh #1 from your NLE. Find a better library. Every free NLE transition SFX is trash.

### "The edit feels thin"
You're only using the song. Add impacts and whooshes. Your mix probably has ONE layer where it should have four.

### "The drop doesn't drop"
Not enough buildup audio, or no silence gap before the drop. Stack the riser + cymbal + swell, and put 2-4 silent frames before the drop hits.

## Final mastering chain

For your final output:

1. All layers balanced to target levels
2. Master bus compressor: light glue compression (1.5:1 ratio, -10 dB threshold, slow attack, auto-release)
3. Master bus EQ: gentle shelf boost at 10 kHz for air, slight dip at 300 Hz to clean mud
4. Master bus limiter: -1 dBTP ceiling, -14 LUFS target
5. Export

The goal is not to squash your mix louder. It's to ensure it hits platform targets cleanly without distortion.
