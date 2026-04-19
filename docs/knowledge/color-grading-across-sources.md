# Color Grading Across Sources

The single biggest tell of an amateur multifandom edit is unmatched color. Marvel at native saturation cut against HP at native darkness cut against anime at native over-saturation = five different movies, not one edit.

## The core problem

Every source has its own:
- **Color space** (Rec.709, P3, DCI-P3, sRGB — rarely aligned)
- **Exposure baseline** (bright blockbusters vs dark prestige TV)
- **Contrast curve** (aggressive vs flat)
- **Saturation level** (animated vs live-action variance)
- **Color bias** (HP's green, Nolan's desat, Marvel's teal)

Cut together untouched, they read as incoherent. Your job: force them into one visual language.

## Strategy 1: Unified grade (default)

One LUT or master grade applies to everything. Per-source tweaks bring outliers into alignment.

**Workflow:**
1. Pick a target look (reference a director, a specific film, or describe in 3 words)
2. Build the master grade on a "hero" clip (your strongest, most centerpiece shot)
3. Save that grade as the master
4. Apply to everything
5. For each source, add one correction node BEFORE the master to neutralize source-specific color bias
6. Verify on scopes

**Pros:** unified feel, professional read
**Cons:** can flatten the character of unique-looking sources

## Strategy 2: Source-proud

Preserve each source's identity. Only normalize contrast and black point for coherence.

**Workflow:**
1. For each source, bring black point to 0% (true black) and white point to 100% if content allows
2. Normalize exposure roughly
3. Leave color bias and saturation mostly alone
4. Apply light unifying LUT at 25-40% to tie it together

**Pros:** each fandom keeps its character
**Cons:** can still feel like a compilation; requires excellent shot selection

## Strategy 3: Section-graded

Different acts have different looks. Act 1 is warm, act 2 shifts cool, act 3 desaturates.

**Workflow:**
1. Plan color shifts as part of your act structure
2. Build 2-4 distinct grades, one per act
3. Design transitions between acts to signal color shift intentionally
4. Apply per-source correction WITHIN each act

**Pros:** color reinforces structure
**Cons:** harder to execute, requires more grading time

## The node tree (DaVinci Resolve)

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ Node 1: Primary │ →   │ Node 2: Sat     │ →   │ Node 3: Contrast│
│ Exposure / WB   │     │ Master sat      │     │ Curves / blacks │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        ↓                                              ↓
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ Node 4: LUT     │ →   │ Node 5: Source  │ →   │ Node 6: Finish  │
│ Master look     │     │ Per-source adj  │     │ Vignette / glow │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

## Per-source starting adjustments

These are the first moves for each source. They get you 80% of the way. Fine-tune from there.

### Marvel (MCU)
Signature issue: over-teal shadows, aggressive skin orange.
- Pull teal saturation -15%
- Recover skin tones: HSL key on skin, pull saturation back toward neutral
- Highlights: pull -100K (cooler) if going for unified cool look

### DCEU / Snyderverse
Signature issue: crushed contrast, over-desaturated.
- Lift midtones +0.2 stops
- Add +8% master saturation
- Loosen black point to +5% if it's crushed

### Harry Potter (Yates era)
Signature issue: green cast, very low light.
- Neutralize green: HSL key on green shadows, pull sat -20%
- Lift exposure +0.3 stops
- Warm highlights +200K

### Star Wars (Disney era)
Signature issue: warm highlights clipping, cool shadows.
- Pull highlights -0.3
- Reduce warmth in highlights -100K
- Preserve shadow blue as character

### Lord of the Rings
Signature issue: yellow-green cast, soft contrast.
- This is often a GOOD starting point — many emotional edits grade TO this look
- For action edits: pull yellow, add contrast

### Nolan films
Signature issue: desaturated, hard contrast.
- Often used AS the target. Match other sources to this.
- If using as one source among others, bump saturation +5% to blend

### Anime (modern)
Signature issue: over-saturated, hard blacks.
- Pull saturation -15-20%
- Soften contrast
- Lift blacks +3%

### Arcane / stylized animation
Signature issue: painterly, high saturation.
- Pull saturation -10% (resist going lower — it loses the look)
- Match exposure to other sources

### Breaking Bad / Better Call Saul
Signature issue: yellow-heavy, warm overall.
- For unified edit: pull yellow from midtones, push toward target
- For emotional edits using BB/BCS shots: the yellow often works as-is

### HBO-era Game of Thrones
Signature issue: muted, green-gray.
- Lift shadows if too dark
- Warm midtones slightly if acting as hero
- Preserve the muted feel if going for "prestige" read

## The LUT selection

LUTs are your unification tool. A well-chosen LUT applied at 60-80% smooths mismatches dramatically.

### Free LUTs worth the disk space
- **Juan Melara P20** — cinematic teal-orange, free
- **Lutify.me starter pack** — varied looks
- **Rocket Stock free LUT packs** — cinematic variants
- **Arri Alexa emulation LUTs** — get you to the "cinematic" baseline

### Paid worth it
- **Color Grading Central** — varied cinematic looks
- **FilmConvert Nitrate** — film stock emulation, legitimately different from free options
- **IWLTBAP LUTs** — cinematic sets

### LUT application rules
- Apply at 60-80% intensity, not 100% (dial with output gain or key mixer)
- Apply AFTER primary correction, not before
- Bake it last — you can still fine-tune per-clip after
- Never stack LUTs. One LUT, maybe two subtle tweaks.

## The scope-first approach

Eyes lie. Especially on uncalibrated monitors. Scopes don't lie.

### Waveform
Shows exposure distribution.
- Black point: should touch but not clip 0 (unless intentional crush)
- White point: should not clip 100 (unless intentional blow)
- Midtones: where most image info sits, target 40-60 for skin

### Vectorscope
Shows color distribution.
- Skin tones: should sit on the "flesh tone line" (between red and yellow, about 11 o'clock position)
- Overall color: balanced around center for neutral, skewed intentionally for style

### Parade
Separate R, G, B waveforms.
- Check for color balance — if green is significantly higher than R and B in shadows, you have a green cast

### Histogram
Frequency of brightness values.
- Gaps in the histogram = color posterization risk
- Smooth distribution = healthy image

Look at scopes for EVERY clip. Eyeball last.

## Matching sequential clips

When shot A cuts to shot B, their color should match or contrast intentionally.

### For seamless match
- Black points within 5%
- Saturation within 10%
- Color temperature within 300K
- Skin tones (if present) within the same flesh-tone region on vectorscope

### For intentional contrast (e.g., memory vs present)
- Make the contrast deliberate — viewer should FEEL the shift
- Usually differences of 10%+ in saturation, 500K+ in temperature

## Color meaning (across cultures, with caveats)

| Color | Emotional read |
|---|---|
| Warm orange / amber | Memory, warmth, home, nostalgia |
| Teal / cool blue | Distance, professionalism, melancholy |
| Desaturated | Grit, gravity, "real world" |
| High saturation | Energy, fantasy, emotion |
| Green | Nature, but in film often = sickness, isolation, envy (context) |
| Red | Danger, passion, violence |
| Purple / magenta | Otherworldly, romantic, surreal |
| Yellow | Warning, optimism, or disease (context) |
| Black-and-white | Timelessness, memory, dream |

Don't be slavish — use color bias as tool, not rule. But know what you're signaling.

## Memory / dream sections

When grading memory or dream sections differently:
- Lift blacks 5-15%
- Pull saturation 20-30%
- Soften contrast
- Warm or cool uniformly depending on mood
- Often add slight halation / glow

Makes the section read as "not present" without announcing it.

## Final sanity check

After grading:
1. Watch the full edit straight through
2. Pause anywhere that feels off
3. Pull scopes on that clip
4. Compare to the clip before and after
5. Match or intentionally contrast
6. Rewatch the section

If every section flows without you noticing individual clips, your grading is done.
