# Edit-Type Knowledge Base

Different multifandom edits follow different rulebooks. This directory documents the eight types FandomForge recognizes, the craft conventions for each, and how the planner applies them. Each type has a matching entry in `tools/fandomforge/data/edit-types.json` that turns the guidance into numbers the scorer can use.

## The eight types

| Type | When to use | Pace |
|---|---|---|
| [Action / Hype](action.md) | Combat, chase, multifandom action compilations | Fast (~1s cuts, 50 cpm) |
| [Emotional / Tender](emotional.md) | Grief, loss, quiet triumph, character vulnerability | Slow (~4s cuts, 15 cpm) |
| [Character Tribute](tribute.md) | Celebrating one character's arc across appearances | Medium (~2.2s cuts, 27 cpm) |
| [Shipping / Romance](shipping.md) | Romantic pairing edits, relationship arcs | Medium-slow (~3.2s cuts, 19 cpm) |
| [Speed AMV](speed-amv.md) | Anime speed-edits, machine-gun pacing | Very fast (~0.5s cuts, 85 cpm) |
| [Cinematic / Slow-Burn](cinematic.md) | Film-style storytelling, motivated cuts | Very slow (~5s cuts, 12 cpm) |
| [Comedy / Meme](comedy.md) | Humor edits, timing-driven punchlines | Variable, punchline-driven |
| [Hype / Trailer](hype-trailer.md) | Rising-tension three-act builds | Escalating (3s → 1s) |

## How the system uses this

1. **Project config** — each project can declare `edit_type` in `project-config.yaml`. The planner loads the matching priors.
2. **Auto-detection** — when `edit_type` is absent, the planner classifies from the edit-plan prompt using keyword heuristics (see `fandomforge.intelligence.edit_classifier`).
3. **Scoring blend** — sync planner blends the declared type's priors with the reference corpus's priors (60% type target, 40% corpus signature) so matches are type-aware AND grounded in real fandom-edit data.
4. **Reasons surface** — when a shot's duration matches the type target, the planner emits `type-prior match: <type>` as a reason string on the recommendation.

## Where the numbers came from

All targets are distilled from three sources:

- **animemusicvideos.org** community craft guide — the canonical AMV editing reference. Sets base rules for action vs drama vs romance pacing.
- **studiobinder.com** and other film editing references — modern film baseline (4-6s) and emotional pacing principles (let shots breathe, cut shorter to intensify).
- **FandomForge's own 148-video reference corpus** — validates the targets against real measured edits.

See each per-type doc's "Sources" footer for specific citations.

## Editing the priors

If you find a type target that doesn't feel right, edit `tools/fandomforge/data/edit-types.json`. The test suite (`tools/tests/test_edit_classifier.py` and `test_type_priors.py`) enforces schema; values are free to tune.

## Not covered yet

- **Analysis edit / commentary** — voice-over heavy, slow pacing, dialogue-centric. Similar to cinematic; add when needed.
- **Music video proper** — when the song artist appears or the edit is branded as the artist's video. Tends toward shipping or cinematic depending on mood.
- **Fancam** — single-subject fan edit of performance footage. Rhythmic like action but typically shorter (~30s) with single-subject framing.

Add a new type by creating `docs/edit-types/<slug>.md` and a matching entry in `edit-types.json`. The classifier picks it up automatically.
