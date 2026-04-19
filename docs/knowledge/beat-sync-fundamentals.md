# Beat Sync Fundamentals

A beat-synced edit cuts or changes energy in time with the music. When it's done right, the viewer doesn't consciously hear "oh the cut lined up with the beat" — they just feel the edit is tight. When it's wrong, nothing looks polished no matter how good your clips are.

## The math you actually need

### BPM to time per beat
```
seconds per beat = 60 / BPM
```

Common BPMs and their beat times:
- 60 BPM → 1.000s per beat (ballads, slow cinematic)
- 90 BPM → 0.667s per beat (mid-tempo emotional)
- 120 BPM → 0.500s per beat (dance, upbeat pop)
- 140 BPM → 0.429s per beat (trap, hype songs, phonk)
- 174 BPM → 0.345s per beat (DnB, extreme hype)

### Subdivisions
In 4/4 time (almost every pop/rock/hip-hop song):
- 1 whole note = 4 beats
- Half note = 2 beats
- Quarter note = 1 beat (the pulse most people feel)
- Eighth note = 0.5 beat
- Sixteenth note = 0.25 beat

## Cut strength hierarchy

Not all beats are equal. When planning cuts:

1. **Downbeat (beat 1 of each bar)** — strongest cut point
2. **Beat 3** — second strongest, the "mid-bar" pulse
3. **Beats 2 and 4 (backbeats)** — common in rock/pop, strong in genres that snare on these
4. **Eighth notes** — usable during buildups for fast cuts
5. **Sixteenths** — only during peak intensity / pre-drop moments
6. **Off-beats (between beats)** — usually wrong, occasionally brilliant

### Finding downbeats
A bar in 4/4 has 4 beats. The downbeat is the first beat of each bar. If a song is 120 BPM, you hit a downbeat every 2 seconds.

```
BPM 120:
0.0s → downbeat 1
0.5s → beat 2
1.0s → beat 3
1.5s → beat 4
2.0s → downbeat 2 (new bar)
2.5s → beat 2
...
```

## The drop

A "drop" is the song's highest-intensity moment. Usually:
- A bass hit appears or massively intensifies
- The energy ceiling jumps 20-40% above prior sections
- Often preceded by a buildup (tension rise) and a breakdown (silence or minimalism)

Drops happen at predictable places in most songs:
- **Main drop** — often at ~25% or ~35% into the song
- **Second drop** — often at ~65-75%
- **Outro drop** — for some songs, a final peak moment

Your edit's biggest visual moment must land on a drop. Miss the drop, waste the song.

## The 4-beat buildup rule

A buildup into a drop is typically 4-8 bars (16-32 beats). Your cuts inside the buildup should accelerate:

```
Bars 1-2 of buildup: cut every 2 beats (slow)
Bars 3-4 of buildup: cut every 1 beat (medium)
Bars 5-6 of buildup: cut every 0.5 beats (fast)
Bars 7-8 of buildup: cut every 0.25 beats (chaos)
DROP: hold a single shot for 1-2 full beats (release)
```

Acceleration on buildup → hold on drop. That contrast is what makes the drop hit.

## Lyric sync vs. beat sync

When a song has lyrics, your cut hierarchy shifts:

- **Lyric-meaningful moments** (a specific word or phrase that matches a visual concept): cut ON the word
- **Non-lyric moments** (instrumental passages): cut on beats
- **Mixed**: alternate — lyric sync for verse/hook, beat sync for instrumental

Lyric sync is stronger than beat sync when the lyric and visual genuinely match. "I am the danger" → cut to Walter White. "Fly me to the moon" → cut to a rocket launch. Literal-matching lyrics can become cheesy; thematic-matching lyrics land better.

## Common beat-sync mistakes

### 1. Cutting a quarter-beat early
Human perception puts the beat slightly before the spectral peak. If the editor cuts exactly on the detected beat, it can feel fractionally late. Most tools can apply a -10 to -20ms offset for this.

### 2. Treating every beat the same
If every cut is on every quarter note, the rhythm flattens. Good edits use a mix — hold for 2 beats, then 1, then half, then 2. Variation keeps the rhythm interesting.

### 3. Cutting on the wrong beat during buildups
In a rising buildup, the 1/8 and 1/16 subdivisions matter more than the downbeat. The downbeat is often masked by the rising sound. Cut on the sub-beats that drive the tension.

### 4. No rest in busy sections
A 3-minute edit that cuts every half-beat for the full duration is exhausting. You need rest sections. Not every beat needs a cut.

### 5. Late drop
Missing the drop by even 2-3 frames (83-125ms) is visible. Frame-perfect drop sync is worth the extra 5 minutes of fine-tuning.

## Working with non-4/4 songs

Some songs are 3/4 (waltz) or 6/8 or irregular. You need to:
- Identify the time signature (librosa can hint at it, but often you have to feel it)
- Count the bars by the time signature, not by assumed 4/4
- Downbeat every 3 (for 3/4) or 6 (for 6/8) beats

3/4 songs feel more emotional and classical. 6/8 feels rocking, swinging. Pick songs intentionally.

## Tempo changes within a song

Some songs shift tempo. When they do:
- Split the beat analysis at the transition
- Use `librosa.beat.plp` (predominant local pulse) instead of `beat_track`
- Re-establish your grid after each tempo change

Don't try to force one grid across a tempo change. It'll feel wrong from the moment the tempo shifts.

## Validation

After you place your cuts, scrub through the edit and ask:
- Do the cuts feel effortless? → good
- Do the cuts feel early or late? → check offsets, adjust -10 to +10ms
- Does the rhythm feel varied? → vary hold length
- Does the drop land? → verify frame-exact placement on drop

If every cut feels good on playback, the beat map is working.
