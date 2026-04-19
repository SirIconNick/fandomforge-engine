# Dialogue Audio Ripping — The Core Multifandom Technique

The difference between a clip compilation and a real multifandom edit is usually ONE thing: **character dialogue threaded through the edit as narrative audio, played over visuals from different scenes.**

If your edit feels like "here are some cool moments set to a song," you're missing this.
If your edit feels like "these characters are telling us something," you've got it.

## The technique in one sentence

Rip dialogue from one scene as audio-only. Play it over visuals from a different scene. The juxtaposition creates meaning neither source had alone.

## Why it works

- **It creates a narrator.** The character(s) are SPEAKING the edit's theme in their own words.
- **It breaks the 1:1 mapping.** When audio and video are from different scenes, the brain works to link them — that linking IS the meaning.
- **It weaves the fandom.** You're no longer just USING the fandom; the characters are in conversation across their own timeline.
- **It earns the song.** The song is the emotional carrier. The dialogue is the thesis. Together they land.

## Workflow

### Step 1: Identify the theme in one sentence
Before ripping anything, know what you're SAYING. The dialogue is in service of the theme.

### Step 2: Find 5-15 canonical lines that articulate the theme
Browse the character's key scenes. Look for lines that ARGUE the theme. Not just famous lines — THEMATIC lines.

Example: Theme is "moral clarity in corrupt systems"
- Keeper line: "You should know better than anyone not to trust politicians." (Leon, RE6)
- Keeper line: "They'll sacrifice anyone to keep the truth buried." (Leon, Infinite Darkness)
- Keeper line: "Someone has to fight back against this evil." (Chris)
- Skip: iconic but off-theme lines like "I'll give you S.T.A.R.S." (Jill — great line, wrong theme for this edit)

### Step 3: Extract each line as audio-only
Download the source, scrub to the line, extract JUST the audio:

```bash
ff sources extract --project <slug> \
  --source <source-id> \
  --start HH:MM:SS --duration 3.0 \
  --name leon_top-down --audio-only
```

Result: a `.wav` file in `projects/<slug>/dialogue/` with just the line.

### Step 4: Script the audio flow
Before touching your NLE, write the sequence. A markdown table with song time, which line plays, which visual runs UNDER it. Example rows:

| Time | Voice | Line | Visual (different!) |
|------|-------|------|---------------------|
| 0:22 | Leon | "I've seen too much." (Damnation) | Leon in RE2R morgue |
| 0:48 | Leon | "Not to trust politicians." (RE6) | Leon glare at Salazar (RE4R) |
| 1:23 | Leon | "Top down." (RE6) | 4 rapid cuts of Leon's face across eras |

The audio is the THROUGH-LINE. The visuals are illustrations.

### Step 5: Place in the NLE
Drop each WAV on its own audio track. Time it to land on song beats or lyric pauses. Duck the song under it. Choose visuals that HAVE something to do with the line without being FROM the same scene as the line.

## Visual-audio pairing rules

### Pair 1: Same character, different era
Leon's voice from RE6 plays over Leon's visual from RE2R.
Effect: the SAME person at two points in their life. The voice (older, hardened) comments on the past (younger, unprepared).

### Pair 2: Character A's voice, Character B's visual
Chris saying "Nothing ever really dies" plays over Jill fighting Wesker.
Effect: Chris is COMMENTING on Jill's situation. He's the commentator, she's the subject.

### Pair 3: Character A's voice, environmental/thematic visual
Leon saying "They'll sacrifice anyone to keep the truth buried" over a shot of the infected presidential cabinet.
Effect: Leon is NARRATING the visual. The line describes what we're seeing.

### Pair 4: Character A's voice, their own visual FROM a different moment (callback)
Leon's "This fight ain't over" (ripped from Saddler fight) plays over Leon in the RE2R station years earlier.
Effect: the older Leon is ENCOURAGING his younger self.

## When to on-screen sync vs. audio-overlay

### On-screen sync
The line plays with its original visual. The character is ACTUALLY saying it on-screen.

Use when:
- The delivery is iconic (you want the viewer to recognize both voice AND face)
- The scene context matters (seeing Benford for Leon's "you mean everything to me")
- First time you introduce that character as a speaker

### Audio-overlay
The line plays over a DIFFERENT visual.

Use when:
- The line's words matter more than the original scene
- You want to create new meaning through juxtaposition
- The original visual is distracting, overused, or off-tone
- The character isn't physically in the current scene

Mix both types throughout. A good edit uses audio-overlay more than on-screen sync, but not exclusively.

## Callback lines

One of the most powerful techniques: play the same line twice in an edit.

- First time: audio-overlay, with new/reframed visual. Viewer hears the line but might not process it fully.
- Second time: on-screen sync, at a later moment. Viewer sees the character say it. The earlier hearing lands retroactively.

Example: Leon's "This fight ain't over" plays at 1:14 over a Salazar confrontation. At 2:28, it plays again — this time synced to Leon's on-screen face from the Saddler fight. The viewer registers "I've heard this line before" and it becomes a thematic through-line.

## The single best source of dialogue

Every fandom has one or two characters whose dialogue is GOLDEN for editing. They:
- Have distinctive voices (instantly recognizable)
- Speak thematic truths rather than action-movie one-liners
- Have been in enough material to source from

In Resident Evil: Leon (most thematic dialogue), Chris (institutional voice), Jill (surprising tenderness).
In Star Wars: Obi-Wan, Yoda, Palpatine (for villain voice).
In Breaking Bad: Walter (for self-justifying monologues), Mike (for hard truths).
In LOTR: Sam, Galadriel, Gandalf.

Identify the thematic voices in YOUR fandom and mine them first.

## Common mistakes

### Over-stuffing dialogue
If you have dialogue on top of the song for 70% of the edit, neither the dialogue nor the song lands. Aim for 30-50% dialogue coverage.

### Ignoring the song's drops
NO dialogue during the song's peak moments (drops, choruses). Let the music own its highest moments. Dialogue lives in verses, buildups, and valleys.

### Literal lyric-to-dialogue matching
If the song says "I'm broken" and you cut to a character saying "I'm hurting" — too on-the-nose. Let dialogue and lyrics have distinct but compatible meanings. The song is emotion, the dialogue is argument.

### Sourcing bad audio
Don't use a line from a source with music already playing underneath — you'll be stacking song on song. Rip lines from moments with clean dialogue (quiet scene, minimal underscore).

### Reverb mismatch
The character's original recording has a reverb "place" (indoor, outdoor, radio, close mic). If you drop their voice next to another character's voice with a wildly different reverb, it'll feel disjointed. Add a light unifying reverb (~15% wet) to the whole dialogue track so they live in the same space.

## Tools for this workflow

### Extracting audio-only from a source
```bash
ff sources extract --project <slug> --source <source-id> \
  --start HH:MM:SS --duration 3.0 \
  --name descriptive-name --audio-only
```
Saves WAV to `projects/<slug>/dialogue/`.

### Finding lines faster
Fetch auto-captions where available:
```bash
ff sources transcripts --project <slug> --priority primary
```
Then grep the transcripts for key words from the lines you want.

### Clean-up
If the ripped audio has game SFX underneath, use Audacity or your NLE's noise-reduction to attenuate. Full surgical removal isn't usually possible, but reducing background music-stings to -15 dB under the voice helps.

### Ducking in the NLE
Set up a sidechain from your dialogue track (A3) to your music track (A1). Threshold -20 dB, ratio 3:1, attack 50ms, release 300ms. Song automatically ducks whenever dialogue plays.

## The test

After placing all dialogue: scrub through the edit with EYES CLOSED for 30 seconds. Listen to just the audio.

Does the dialogue + song tell a story on its own? Can someone who's never seen the source material understand what the edit is about?

If yes, you nailed it.
If not, your dialogue isn't doing enough narrative work. Add more lines OR replace the ones that don't advance the theme.
