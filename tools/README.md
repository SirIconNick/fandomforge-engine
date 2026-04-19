# FandomForge Tools

Python CLI for audio analysis, video metadata, and clip cataloging.

## Install

```bash
cd "/Users/damato/Video Project"
python3 -m venv .venv
source .venv/bin/activate
pip install -e tools/
```

This installs the `ff` command pointing at `tools/fandomforge`.

## Commands

### Audio / beat

```bash
# Full beat analysis (BPM, beats, drops, buildups, energy curve)
ff beat analyze song.mp3 -o projects/myedit/beat-map.json

# Just BPM check
ff beat bpm song.mp3

# Just drop detection
ff beat drops song.mp3 --snare-bias
```

Options for `beat analyze`:

| Option | Purpose |
|---|---|
| `--tempo-hint <BPM>` | Constrain the search around a known BPM |
| `--tightness <int>` | Stricter beat tracker (default 100) |
| `--beats-per-bar <int>` | For non-4/4 signatures |
| `--snare-bias` | Weight high-frequency flux higher (drops without heavy bass) |
| `--song`, `--artist` | Metadata labels |
| `-o`, `--output` | Write JSON to this path |

### Video

```bash
ff video info clip.mp4
```

Shows duration, resolution, fps, codec, bitrate, audio presence.

### Catalog

```bash
# Add a clip to the catalog
ff catalog add \
  --project myedit \
  --source "Revenge of the Sith" \
  --fandom "Star Wars" \
  --timestamp 01:52:30 \
  --duration 2.5 \
  --description "Obi-Wan on Mustafar, wide shot, lava flare" \
  --mood grief --mood resolve \
  --framing wide \
  --motion static \
  --color "orange/blue high contrast"

# List all clips in a project
ff catalog list --project myedit

# Filter
ff catalog list --project myedit --fandom "Star Wars"
ff catalog list --project myedit --mood grief
ff catalog list --project myedit --search "Mustafar"

# Remove
ff catalog remove c_abc12345 --project myedit
```

Omit `--project` to use the global catalog at `projects/_global/catalog.json`.

### Project

```bash
# Create a new project folder with templates pre-filled
ff project new my-edit-slug \
  --theme "Mentors who taught us the thing that killed them" \
  --song "Until I Bleed Out" \
  --artist "The Weeknd"
```

Drops a project folder into `projects/<slug>/` with `edit-plan.md`, `shot-list.md`, and `beat-map.md` pre-filled.

## Module layout

```
tools/
├── pyproject.toml
└── fandomforge/
    ├── __init__.py
    ├── cli.py              Main Click CLI (ff command)
    ├── audio/
    │   ├── __init__.py
    │   ├── beat.py         Beat tracking, BPM, downbeat estimation
    │   ├── drops.py        Drop, buildup, breakdown detection
    │   └── energy.py       Energy curve computation
    ├── video/
    │   ├── __init__.py
    │   └── info.py         ffprobe-based video metadata
    └── catalog/
        ├── __init__.py
        └── store.py        JSON-backed clip catalog
```

## Technical notes

### Beat detection accuracy

Librosa's `beat_track()` uses dynamic programming over the onset envelope. It's fast and accurate for steady-tempo pop/rock/hip-hop. It can struggle with:

- Variable tempo (try `--tempo-hint` to constrain)
- Songs that start with no percussion (first bar may be missed)
- Half-time / double-time ambiguity (a 70 BPM song might report as 140)

When half-time/double-time is suspected, check the output and use `--tempo-hint` to nudge into the right range.

### Drop detection approach

We use a weighted sum of:
1. Low-frequency energy (bass content — most drops are bass-heavy)
2. Spectral flux (rate of spectral change — drops are big changes)
3. High-frequency flux (only weighted if `--snare-bias` is on)

Peaks above 65% of the maximum, separated by at least 15 seconds, count as drops. The highest-intensity one is the "main_drop," the second is the "second_drop," and so on.

### Why 22050 Hz sample rate?

Beat and tempo detection doesn't need full CD quality. 22050 Hz is half the standard rate, which halves memory use without harming accuracy for this task.

### Known gotchas

- `ffmpeg` must be installed system-wide for audio loading to work on exotic formats. On macOS: `brew install ffmpeg`.
- Very short audio (< 5 seconds) won't produce useful analysis.
- DRM-protected / Apple Music downloads can't be analyzed. Use MP3/WAV/FLAC you legally own.
