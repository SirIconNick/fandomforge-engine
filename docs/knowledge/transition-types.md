# Transition Types — Full Library

Every transition in a multifandom edit either helps the story or gets in the way. Here's the full catalog with when to use each, common pitfalls, and NLE-specific notes.

## Hard cut

**What:** Straight cut from one clip to the next. No effect. No overlap.

**When:** Default. 85-90% of cuts in a good multifandom edit are hard cuts. A hard cut on a downbeat is the most powerful transition tool there is.

**Use cases:**
- Beat-synced cuts between visually similar shots
- Action-to-action continuation
- Rhythmic assembly during verses and build sections

**Pitfalls:**
- A hard cut between two shots with no visual relationship feels jarring. Either find a common visual element or pick a different transition.

## Match cut

**What:** A hard cut where one element continues across the boundary.

**Subtypes:**

### Motion match
Motion direction continues. Example: Spider-Man jumps right to left, cut, Luffy jumps right to left in a different film.

### Shape match
A shape in the frame matches the next shot. Example: circular helmet dissolves into a circular planet.

### Eyeline match
Two characters appear to be looking at each other across the cut. Powerful for implying connection between characters from different fandoms.

### Action match
An action completes across the cut. Example: someone throws a punch in fandom A, cut on the punch, in fandom B someone else gets hit.

### Graphic match
Compositional elements line up. A character in the left third cuts to another character in the left third at the same scale.

**Use cases:**
- Act handoffs
- Signaling "these fandoms are telling the same story"
- Thematic rhymes

**Pitfalls:**
- Overused match cuts become gimmicky. One or two per edit, max.
- The match has to be strong. A weak motion match looks like you tried and failed.

## Whip pan

**What:** Fast camera motion blurs out of one shot and into the next. The blur covers the cut.

**How to do it:**
1. Take the last 4-8 frames of clip A.
2. Apply a directional motion blur increasing toward the cut.
3. Speed-ramp those frames so motion accelerates.
4. First 4-8 frames of clip B: same treatment, blur decelerating.
5. Peak blur lines up with the cut.

**Use cases:**
- Covering a hard mismatch between clips
- Fast-paced buildups
- Transitioning between locations / worlds

**Pitfalls:**
- Whip direction must match across the cut. Left-to-right out = left-to-right in.
- Over-used in amateur edits. Reserve for real moments.

## Flash cut (flash-frame)

**What:** One or two frames of white (or other color) inserted between clips.

**Use cases:**
- Impact on a drop
- Mimicking a camera flash
- Hard energy shift

**Pitfalls:**
- More than a few frames and it becomes a flashbulb effect — rarely what you want.
- Pure white is standard. Colored flashes (red, cyan) should be theme-motivated.

## Flash-stack

**What:** Multiple flash cuts in rapid succession, each on a different shot, landing on the target shot.

**Example pattern:**
- Frame 1: white flash
- Frame 2: shot A (8 frames)
- Frame 3: white flash
- Frame 4: shot B (8 frames)
- Frame 5: white flash
- Frame 6: target shot (held for the drop)

**Use cases:**
- THE drop of the edit. Once per edit, max.
- Signaling a reality break

**Pitfalls:**
- Used more than once, it loses impact.
- Used in wrong context, it feels random. Flash-stack = "something huge is happening."

## Cross-dissolve

**What:** Two clips overlap with fading opacity. A gradual blend.

**Use cases:**
- Memory sequences
- Emotional transitions
- Intentionally slow moments (quiet sections, outros)

**Pitfalls:**
- NEVER cross-dissolve in an action section. Kills momentum.
- Default 15-30 frame dissolves look amateur. Use 8-12 frame for subtle, 40+ for intentional memory reads.

## Dip to black

**What:** Both clips fade through black briefly.

**Variants:**
- Quick dip (4-8 frames) — subtle pause, breath
- Medium dip (12-20 frames) — act break
- Long dip (30+ frames) — major chapter change

**Use cases:**
- Act transitions
- Resetting emotional state
- Opening / closing edits

**Pitfalls:**
- Long dips in short edits waste runtime. Keep dips short unless the song demands a long pause.

## Dip to white

**What:** Same as dip to black but fading to white.

**Use cases:**
- Afterlife / ethereal sections
- Memory / dream transitions
- Unusual mood

**Pitfalls:**
- Much more specific in meaning than dip-to-black. Don't use casually.

## Light leak / overexposure wipe

**What:** A burst of light (usually warm amber or white) washes over the frame, transitioning to the next shot.

**Use cases:**
- Nostalgic / memory sequences
- Softer energy shifts
- Opening shots

**Pitfalls:**
- Cheesy if overused. Reserve for earned emotional moments.

## Sound-motivated cut

**What:** The transition is defined by an audio event (impact, vocal hit, bass drop) rather than a visual effect.

**Use cases:**
- When the audio IS the transition
- Drops
- Vocal emphasis moments

**Pitfalls:**
- Works only if the audio is loud / distinct enough to cover a visual mismatch. Tiny audio events don't cover jarring visual cuts.

## L-cut / J-cut

**What:**
- L-cut: audio from clip A continues over the visual of clip B
- J-cut: audio from clip B starts under the visual of clip A

**Use cases:**
- Voice-over, dialogue bridges
- Narrative glue
- Any time you want audio-visual offset for intentional effect

**Pitfalls:**
- Can feel amateur if the audio bleed isn't motivated. Always have a narrative reason.

## Invisible zoom

**What:** Each clip has a subtle zoom-in or zoom-out applied. Viewer feels continuous momentum even through cuts.

**Implementation:**
- 2-5% zoom-in per shot over 4-8 consecutive shots during a buildup
- Same zoom rate / direction across the sequence

**Use cases:**
- Pre-drop buildups
- Escalating tension
- Sequences that need to feel like one continuous push

**Pitfalls:**
- If the clips already have their own zoom, your zoom fights it. Pick clips that are relatively static.

## Speed ramp

**What:** A clip slows down at the end, cuts, the next clip ramps up to speed. Or the reverse.

**Use cases:**
- Action beats
- Emphasizing a specific moment (the draw, the punch, the reveal)
- Pre-drop deceleration, post-drop acceleration

**Pitfalls:**
- Speed-ramping clips that are already highly stylized often looks wrong. Use on relatively straightforward action footage.
- Optical flow / frame interpolation is required for smooth speed ramps. Most NLEs have it (Resolve's Optical Flow, Premiere's Time Remapping with Frame Blending).

## Morph cut

**What:** AI/algorithm-based transition that blends two similar shots (usually talking-head type).

**Use cases:**
- Almost never in multifandom. This is a documentary/interview tool.
- Occasionally useful for a specific character continuity cut.

## Color wipe

**What:** A solid color (usually theme-relevant) fills the frame and is revealed as the new shot.

**Use cases:**
- Section transitions with color meaning
- Matching title card color

**Pitfalls:**
- Reads as design-forward / brand-aesthetic. Not right for most narrative multifandom.

## The default distribution

In a good 2-minute multifandom edit, your transition spread usually looks like:

| Transition | Approximate % | Count (in a 2min edit with ~60 cuts) |
|---|---|---|
| Hard cut | 85% | ~51 |
| Match cut | 7% | ~4 |
| Whip pan | 3% | ~2 |
| Flash cut | 2% | ~1 |
| Dip to black | 1% | ~1 (act transition) |
| Cross-dissolve | 1% | ~1 (memory section) |
| Everything else | <1% | 0-1 |
| Flash-stack | — | 1 (on the drop) |

Deviate intentionally. If you find yourself reaching for fancy transitions often, step back — usually the problem is shot selection, not transition tech.
