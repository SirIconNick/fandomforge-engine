# Starting a new FandomForge project

Step-by-step for adding a new character or franchise. Every project lives in `projects/<slug>/` and runs through the same `ff make-edit` pipeline as every other project. No code changes — everything character-specific is config and raw media.

This guide covers the "zero to first QA-passing edit" path. It takes 20–60 minutes of wall time + about $0.50 in API spend for a 60–90 second edit, depending on library size.

---

## 0. Pick a character and a song

You need a character with at least one compilation video you can use (or legally obtain) for source footage. You need a song you can use legally — public domain, creative-commons, or something you've licensed. If you're publishing, know that fair use is case by case.

Pick a song whose energy fits the character's arc. Moody + dying-hero needs a slow build. Action character needs a hook that drops hard. Don't pick a song you want to like — pick one that serves the character.

---

## 1. Create the project folder

```
projects/<slug>/
├── raw/              # source mp4s + song here
├── dialogue/         # extracted VO wavs + transcript-map.json
├── plans/            # pipeline outputs
├── exports/          # final.mp4 lands here
└── project-config.yaml
```

Create it:

```bash
mkdir -p projects/dean-winchester-renegades/{raw,dialogue,plans,exports}
```

---

## 2. Drop source media

```
projects/dean-winchester-renegades/raw/
├── dean-winchester-best-scenes.mp4
├── dean-later-seasons.mp4
└── renegades-x-ambassadors.mp3
```

Pull closed-caption VTTs alongside the mp4s if you can — `yt-dlp --write-auto-subs --sub-langs en` during download. These feed the VO extractor.

---

## 3. Write project-config.yaml

Start with the minimal template from `docs/guides/project-config-schema.md`. The four fields you must set:

```yaml
character: dean
character_aliases: [dean winchester]

era_source_map:
  BestScenes: dean-winchester-best-scenes
  LaterSeasons: dean-later-seasons

song: renegades-x-ambassadors.mp3
vision_context: "Supernatural TV series scene featuring Dean Winchester"
template: HauntedVeteran

narrative_priorities:
  - family business
  - saving people hunting things
  - son of a bitch
  - my brother
  - hell
  - demons

target_duration_sec: 60
```

---

## 4. Detect scenes

```bash
ff scene detect --project dean-winchester-renegades
```

Writes `.scene-cache.json` — the boundary list for every source.

---

## 5. Caption scenes via GPT-4o-mini vision

Quick script:

```python
from fandomforge.intelligence.scene_library import build_library
build_library(
    raw_dir=Path("projects/dean-winchester-renegades/raw"),
    scene_cache=Path("projects/dean-winchester-renegades/.scene-cache.json"),
    output_path=Path("projects/dean-winchester-renegades/.scene-library.json"),
    api_key=os.environ["OPENAI_API_KEY"],
    vision_context="Supernatural TV series scene featuring Dean Winchester",
    character_list="dean, sam, castiel, crowley, bobby, demon, angel, monster, other",
)
```

Budget: ~$0.003 per scene. A typical compilation yields 100–300 captionable scenes.

---

## 6. Ingest into the shot library

```python
from fandomforge.intelligence.shot_library import ingest_and_verify
from fandomforge.config import load_project_config, build_era_patterns

cfg = load_project_config(Path("projects/dean-winchester-renegades"))
era_patterns = build_era_patterns(cfg)
character_vocab = {cfg.character.lower(), *{a.lower() for a in cfg.character_aliases}}
# Add supporting cast so they don't land in "unknown"
character_vocab |= {"sam", "castiel", "crowley", "bobby"}

ingest_and_verify(
    Path("projects/dean-winchester-renegades/.shot-library.db"),
    Path("projects/dean-winchester-renegades/.scene-cache.json"),
    Path("projects/dean-winchester-renegades/.scene-library.json"),
    era_patterns=era_patterns,
    character_vocab=character_vocab,
)
```

After ingest, check that at least 70% of shots have the primary character attributed. If it's lower, your `vision_context` is probably too generic — tighten it.

---

## 7. Extract + verify VO

The VTT-to-wav path works for any YouTube-sourced compilation. See `tools/build_dean_vo.py` for a reference implementation. The flow:

1. Parse the `.en.vtt` into sentence-level cues (collapse the karaoke-style rolling captions).
2. Score each sentence against `narrative_priorities`. Drop short filler lines.
3. Pick 10–12 candidates per source.
4. ffmpeg-cut mono 48 kHz wav with 300 ms pre-roll, loudnorm to -14 LUFS, 15 ms fade-in.
5. Round-trip through Whisper to verify. Drop any wav where the transcription has less than 40% token overlap with the expected line.
6. Write `dialogue/transcript-map.json` (wav → Whisper-verified text) and `dialogue/source-map.json` (wav → `{source_mp4, source_start_sec, source_end_sec}`).

Filename convention: `<character>_<Era>_<slug>.wav`. The layered planner parses this format to match each VO back to its originating source for sync anchor shots.

---

## 8. Bootstrap a style template

If you haven't run reference analysis (`reference_analyzer.py`) for this project, copy Leon's style template as a starting point:

```bash
cp projects/leon-badass-monologue/.style-template-single_character_arc.json \
   projects/<slug>/.style-template-single-character-arc.json
cp projects/leon-badass-monologue/.style-template.json \
   projects/<slug>/.style-template.json
```

The templates were built from 146 generic tribute-video references, not Leon-specific, so they transfer.

---

## 9. Run the pipeline

```bash
ff make-edit --project projects/dean-winchester-renegades --duration 60
```

First run will walk every stage. Total time 3–6 minutes including captions, render, QA, thumbnails, YouTube metadata, copyright audit, and export presets.

Success criteria: `PIPELINE SUCCESS` at the end, `QA passed: True`, and `projects/<slug>/exports/final.mp4` is watchable.

---

## 10. Tune if QA fails

The two most common failures on a new project:

**Loudness too hot** — QA reports integrated LUFS outside `[-17, -12]`. Drop `song_gain_db` by 2–4 dB in the config.

**VO not clear enough** — QA reports voice-band lift under +2.5 dB at some cue. Drop `default_duck_db` to `-24` or `-26`. If the problem persists at a specific timestamp, the song is peaking there; consider shifting `song_offset_sec` or picking a different VO placement.

Re-run. Should take under 30 seconds of re-tune per iteration.

---

## 11. Save a baseline

Once QA passes and the edit feels right, copy into `baselines/<slug>-v1/`:

```
baselines/<slug>-v1/
├── final.mp4
├── project-config.yaml      # frozen knob set
├── layered-plan.json        # frozen plan
└── baseline-stats.json      # LUFS, shot count, QA results
```

That's your regression reference. Any future pipeline change should reproduce this (or improve it). If a pipeline change makes a baseline worse, diagnose before shipping.

---

## What NOT to do

- Don't edit the engine to "help" a specific project. If something's Leon-only, generalize it and push the knob into `project-config.yaml`.
- Don't skip QA. The gate exists because every time we shipped past a failing QA, the output was bad.
- Don't rip copyrighted content you don't have rights to use. The copyright audit flags it but won't stop you.
- Don't caption more than 500 scenes in one pass — batch it and review intermediate results first.
