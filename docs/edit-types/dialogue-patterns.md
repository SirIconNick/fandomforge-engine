# Dialogue Patterns in Fandom Edits

Dialogue in a fandom edit is never decoration — it does narrative work the music alone can't do. This doc catalogs the four patterns pro fandom editors use and when each one applies. Every pattern has specific timing rules and visual expectations.

**Core principle:** the line STATES the theme, the visuals PROVE it. If the dialogue says "I'm back," the shots after it must show characters making their return. Mismatch kills the edit.

---

## Pattern 1 — Thematic Preamble

The most common opening in serious fandom edits. A character monologue states the emotional thesis; the montage after delivers on it.

**Structure:**
- **0s → [beat 1]** (typically 5-10 seconds): dialogue over held or slow establishing shots; song is low-volume bed or silent
- **[beat 1] onwards**: dialogue ends, song swells, montage delivers on the dialogue's claim

**Example — Nick's own reference:**
> John Wick: "People keep asking if I'm back. And yeah — I'm thinking I'm back."
>
> → montage of heroes from every fandom walking into frame, suiting up, drawing weapons

The line declares "I'm back." The visuals prove it by showing every featured character making their return to action.

**Other example forms:**
- "Tyler, you were clinically dead nine months ago. You fought your way back." → heroes rising from defeat in each fandom
- "They thought I was finished." → characters re-entering battle
- "Under the old laws, only one can survive." → cut between opponents squaring off

**Production rules:**
- **Dialogue duration: 3-10 seconds**. Shorter than 3s is punctuation, not a thesis. Longer than 10s loses momentum.
- **Song ducks by 8-12 dB** under the dialogue, then releases on the first beat.
- **Visual pacing**: slow during dialogue (2-3s holds), tightens aggressively after.
- **Key first shot after dialogue ends**: the single most iconic hero shot of the edit. The whole preamble built to this moment.

---

## Pattern 2 — Interrogative Hook

A character asks a question; the rest of the edit answers it visually.

**Structure:**
- Line 1 (0-3s): the question, over a held shot of the speaker
- Beat drops, edit begins, entire edit is the visual answer

**Examples:**
- "What would you do to save your family?" → edit of characters making brutal choices
- "How far are you willing to go?" → escalating violence across fandoms
- "Who are you?" → identity-affirming hero shots

**Production rules:**
- The question must be simple enough the viewer holds it in their head for 45+ seconds.
- Don't answer it with a second line — the visuals ARE the answer.
- Consider a callback: a single beat of silence late in the edit, then the character answers their own question in voiceover.

---

## Pattern 3 — Declaration + Montage

Character says something defining about themselves; montage illustrates it.

**Structure:**
- **Declaration (1-3s)**: short, emphatic. "I am Iron Man." "I'm Batman." "I do not fear death."
- **Montage (40-60s)**: every shot proves the declaration through action or emotion

**Examples:**
- "I am the one who knocks." → shots of the character dominating every room they enter
- "I am inevitable." → relentless advance shots
- "I choose the impossible." → characters attempting the impossible

**Production rules:**
- Declaration is SHORT. Every extra word weakens it.
- Consider placing it at the FIRST DROP instead of the intro — "I am Iron Man" hits 45 seconds in, over the drop, with a cumulative payoff.
- Cannot contradict the visuals. "I am alone" over a montage of ensemble fights breaks the spell.

---

## Pattern 4 — Philosophical Frame

A character states a worldview; the edit illustrates the consequences.

**Structure:**
- Preamble (5-8s): "Death is not the end" / "There are no heroes, only consequences" / "We are not things."
- Edit (50-55s): characters living/dying by that worldview

**Examples:**
- Mad Max: "We are not things." → every shot refutes dehumanization through action
- Dune: "Fear is the mind-killer." → heroes facing fear in different forms
- The Raid: "A true friend stays with you forever." → shots of alliance and betrayal

**Production rules:**
- Worldview must be **concrete** enough to illustrate visually. "Hope is fragile" works; "life is complicated" doesn't.
- Edit should end on a shot that either CONFIRMS the worldview or dramatically challenges it (the cliffhanger version).

---

## Pattern 5 — Chained Lines

Two-three short character lines stacked, each from a different character, building a shared narrative.

**Structure:**
- Line A from character 1 (1-2s): setup
- Line B from character 2 (1-2s): contrast or confirmation
- Line C from character 3 (1-2s): resolution
- Beat drops, edit begins

**Example:**
- "They thought we were done." (character A from fandom 1)
- "They were wrong." (character B from fandom 2)
- "Let's go." (character C from fandom 3)
- → all-out multifandom action

**Production rules:**
- Each line under 2 seconds.
- Voices must sound distinct (different actors).
- Ducking: song is SILENT under this, not just ducked. The chain carries the whole intro.
- Third line is the ignition point — the next cut is on the first musical beat.

---

## When to use which

| Edit type | Pattern |
|---|---|
| Action / Hype | Thematic Preamble or Declaration+Montage |
| Emotional / Tender | Philosophical Frame or Interrogative Hook |
| Character Tribute | Declaration+Montage (character's own defining line) |
| Shipping / Romance | Philosophical Frame, from one partner about the other |
| Hype / Trailer | Chained Lines (stakes-building from multiple characters) |
| Speed AMV | Usually NO dialogue — pure kinetic rhythm |

---

## Anti-patterns (what to avoid)

### Dialogue as decoration
A random punchy line dropped in a quiet passage just because it sounds cool, with no thematic connection to the shots around it. Reads as filler. The viewer subconsciously registers that the line and the visuals don't match.

### Dialogue during a drop
Song is peaking, viewer's adrenaline is peaking, AND the editor drops in a quiet line of dialogue? The ducking breaks the energy and the line is drowned anyway. Dialogue goes in the quiet passages before or after drops, never on top of them.

### Too many dialogue cues
Multiple thematic preambles in one edit = telling the story three times. Pick ONE pattern, execute it well. Second dialogue moment undermines the first.

### Captions instead of ducking
Some editors caption the dialogue on screen while letting the song play loud underneath. Unless it's a comedic edit, this reads as underpowered. Duck the song properly.

### Line contradicts visuals
"I'm back" over a shot of a character who's obviously dying. "Alone" over an ensemble shot. These create cognitive dissonance that the viewer registers as "bad edit" even when they can't articulate why.

---

## Finding the right line — the mental checklist

For any candidate dialogue line:

1. **Is it a complete thought?** Fragments lack gravity.
2. **Can a viewer repeat the line after hearing it once?** If not, too complex.
3. **Does the line state a THEME, not a FACT?** "I'm going to the store" is a fact. "I'm not going back" is a theme.
4. **Do I have 3+ shots that deliver on the theme?** If the visuals can't support it, pick another line.
5. **Is the speaker visible on screen during or right after the line?** Helps the viewer attach the dialogue to a character.
6. **Would a stranger understand the line without watching the source?** Fandom-edit viewers include people who haven't seen every source.

---

## Implementation notes for the planner

The narrative dialogue picker (`fandomforge.intelligence.dialogue_picker`) should score candidate whisper segments on:

- **Thematic keyword match**: boost for keywords like back/return/survive/fight/alone/done/family/friends/promise/revenge/kill/choose/dead/alive/final/first/last/never/always
- **Sentence completeness**: tokens ending with period or question mark score higher than fragments
- **Speaker continuity**: a single-speaker line scores higher than overlapping dialogue
- **Duration fit**: 3-10s ideal, penalties outside that range
- **Whisper confidence**: above 0.85 required (below = noisy transcript, speaker unclear)

The picked line's START TIME in the edit is 0s (song intro). The song_gain_db reduces by 10 during the line's window; returns to normal on the first musical beat.

The picker also outputs **visual hints** for the shot picker: keywords from the line that should bias shot selection during the line's window. "I'm back" → prefer shots tagged with `return`, `entrance`, `rise`. "We fight" → prefer combat scenes. This is how the line and the visuals stay thematically matched.

---

## Gap in the reference corpus

The current reference corpus (`references/action-pl1–pl5`) analyzed the 148 fandom edits for rhythm, motion, color, beat-sync, and tempo — but **not for dialogue placement patterns**. A future analysis pass should measure:

- Does the edit open with dialogue? (how long before the first musical beat)
- Which of the 5 patterns above is most common in the corpus?
- Average dialogue duration in the opening
- Song duck depth during dialogue
- Thematic keyword frequency in opening dialogue

Building that into `reference_analyzer_deep.py` would let the engine learn what the best editors actually do with dialogue, not just heuristically guess.
