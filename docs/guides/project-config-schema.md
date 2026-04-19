# project-config.yaml — schema reference

Every FandomForge project folder carries one `project-config.yaml` (or `.json`) at its root. That file is the single source of truth for character, sources, song, template choice, audio tuning, and engine toggles. The generalized pipeline reads nothing from the code — everything character- or franchise-specific lives here.

Location: `projects/<slug>/project-config.yaml`

Loaded by `fandomforge.config.load_project_config()` and consumed by `fandomforge.master_pipeline.from_project_config()` at the start of every `ff make-edit` run.

---

## Minimal config

```yaml
character: dean
song: renegades-x-ambassadors.mp3
era_source_map:
  BestScenes: dean-winchester-best-scenes
  LaterSeasons: dean-later-seasons
vision_context: "Supernatural TV series scene featuring Dean Winchester"
template: HauntedVeteran
target_duration_sec: 60
```

Everything else falls back to defaults. Start here, then tune.

---

## Identity

| Field | Type | Default | Notes |
|---|---|---|---|
| `character` | str | `"unknown"` | Primary character name, lowercase. Used for VO-wav filename prefix, dialogue filter, and vision-caption character list. |
| `character_aliases` | list[str] | `[]` | Other names the character goes by. Used when scoring dialogue lines and matching captions. |

---

## Source mapping

| Field | Type | Default | Notes |
|---|---|---|---|
| `era_source_map` | dict[str, str] | `{}` | Maps era key (e.g. `RE2R`, `LaterSeasons`) to the stem of the raw mp4 file. The engine uses this to (a) label shots in the library with the correct era, and (b) parse VO wav filenames like `dean_LaterSeasons_*.wav` back to their source mp4. |

Keys become era labels on every shot in `.shot-library.db`. Values must match the filename stem in `projects/<slug>/raw/`.

---

## Narrative priorities

| Field | Type | Default | Notes |
|---|---|---|---|
| `narrative_priorities` | list[str] | `[]` | Keywords and phrases that boost a dialogue line's score when the layered planner is picking the 6 VO lines for the final spine. Pick punchy character-defining phrases — "family business" for Dean, "dso" for Leon. |

---

## Audio

| Field | Type | Default | Notes |
|---|---|---|---|
| `song` | str | `""` | Filename of the backing track in `projects/<slug>/raw/`. |
| `song_offset_sec` | float | `0.0` | Seconds to skip at the start of the song (useful when the intro is dead air or a talk-up). |
| `song_gain_db` | float | `-4.0` | Baseline song level before ducking. Modern hot masters (Renegades, 2015+ pop) need `-6` to `-8`. Older or quieter choir/orchestral masters sit at `-4`. If QA reports LUFS too loud, drop this 2–4 dB. |
| `default_duck_db` | float | `-20.0` | Depth the song is ducked while a VO cue is playing. Songs with busy mids may need `-24` to `-26` to keep voice-band lift above the +2.5 dB QA threshold. |

---

## Template

| Field | Type | Default | Notes |
|---|---|---|---|
| `template` | str | `"HauntedVeteran"` | Narrative-arc template. Current registry: `HauntedVeteran`. Add more by registering in `intelligence/narrative_templates.py`. |

---

## Vision captioning

| Field | Type | Default | Notes |
|---|---|---|---|
| `vision_context` | str | `"game cutscene"` | Short phrase injected into every GPT-4o vision prompt so the model knows what series you're captioning. Example: `"Supernatural TV series scene featuring Dean Winchester"`. |

---

## Engine toggles

All default to the most common choice. Set to `false` to skip a stage, `true` to force it on.

| Field | Type | Default | Notes |
|---|---|---|---|
| `add_titles` | bool | `false` | Needs ffmpeg built with libfreetype/drawtext. Off unless you've confirmed your ffmpeg supports it. |
| `apply_transitions` | bool | `true` | Runs the transition scorer between assembled clips. |
| `add_sfx` | bool | `true` | Adds impact/riser SFX on section boundaries. |
| `run_qa` | bool | `true` | Runs `qa_loop.run_qa` as a mandatory gate. Pipeline exits non-zero if any gate fails. |
| `extract_missing_vo` | bool | `false` | Runs the multi-era VO mining stage. Currently Leon-specific; disable for other projects and provide `dialogue/*.wav` + `transcript-map.json` yourself. |
| `run_director` | bool | `false` | GPT-4o director review. Expects EditPlan shape; LayeredPlan has shims but coverage is partial. |
| `build_storyboard` | bool | `false` | Produces a storyboard image grid from the plan. |
| `build_thumbnail` | bool | `true` | Picks a representative frame + renders alt thumbnails. |
| `generate_captions` | bool | `true` | Writes `.srt` and `.vtt` from the dialogue timeline. |
| `build_youtube_meta` | bool | `true` | Writes `final.youtube.json` with title, description, tags. |
| `copyright_audit` | bool | `true` | Runs the fair-use/copyright audit and writes `final.fair-use.md`. |
| `enrich_motion` | bool | `true` | Motion-flow analysis on the shot library (improves cut scoring). |
| `enrich_gaze` | bool | `true` | Face/gaze detection for close-up weighting. |
| `target_duration_sec` | float or null | `null` | Hard target for the final edit. Null means "match song length." |
| `export_presets` | list[str] | `["youtube"]` | Any of `youtube`, `shorts`, `tiktok`, `reels`, `twitter`, `master`. |

---

## Style

| Field | Type | Default | Notes |
|---|---|---|---|
| `cluster_archetype` | str | `"single-character arc"` | Picks which `.style-template-*.json` file to load. Valid: `single-character arc`, `multi-era montage`, `fast AMV`. |
| `lut_name` | str | `"cinematic-teal-orange"` | LUT preset from `templates/luts/`. |
| `lut_intensity` | float | `0.5` | 0–1 blend amount of the LUT. Lower is more natural. |

---

## Full-reference example (Leon)

```yaml
character: leon
character_aliases:
  - leon kennedy
  - kennedy

era_source_map:
  RE2R: leon-re2r-cutscenes
  RE4R: leon-re4r-cutscenes
  RE6: leon-re6-cutscenes
  Damnation: leon-damnation
  Vendetta: leon-vendetta
  ID: leon-infinite-darkness
  RE9: re9-leon-scenepack

narrative_priorities:
  - leon kennedy
  - dso
  - raccoon city
  - couldn't save
  - it's over victor
  - bastards

song: in-the-end-tommee.mp3
song_offset_sec: 0.0
song_gain_db: -4.0
default_duck_db: -20.0

template: HauntedVeteran
vision_context: "Resident Evil game cutscene featuring Leon Kennedy"

add_titles: false
apply_transitions: true
add_sfx: true
run_qa: true
enrich_motion: true
enrich_gaze: true
target_duration_sec: 90

cluster_archetype: "single-character arc"
lut_name: cinematic-teal-orange
lut_intensity: 0.5

export_presets:
  - youtube
```

---

## How the engine reads this

1. `fandomforge.config.load_project_config(project_dir)` loads the YAML and fills defaults for missing fields via dataclass construction.
2. `master_pipeline.from_project_config(project_dir, **cli_overrides)` turns it into a `PipelineConfig`. CLI flags like `--duration` override the config.
3. Each pipeline stage reads only what it needs from `PipelineConfig`. No stage reads raw YAML directly.
4. Unknown YAML keys are filtered (with a warning) so the config can grow without breaking older engine versions.

Config reload isn't hot — you must re-run `ff make-edit` for changes to take effect.
