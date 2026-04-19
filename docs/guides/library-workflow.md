# Library workflow — link once, edit many

The library lets you register folders of movies / shows / clips once, have
them all ingested (scene detection, transcripts, CLIP embeddings), and then
pull shots from the whole corpus into any number of fandom edits without
re-ingesting.

## 1. Link a folder

```bash
ff library link /Volumes/Movies --name home
```

Optional — pick a fandom-inference rule at link time:

```bash
# First directory under the root is the fandom (default)
ff library link /Volumes/Movies --auto-fandom dir1

# Use two levels deep ("Marvel / MCU Phase 4")
ff library link /Volumes/Movies --auto-fandom dir2

# Pull fandom from everything before the year in the filename
ff library link /Volumes/Movies --auto-fandom filename-before-year

# No inference — everything lands as "Unknown" until you tag it
ff library link /Volumes/Movies --auto-fandom manual
```

## 2. Scan (walk + ingest)

```bash
ff library scan
```

This walks every linked root recursively, indexes every `.mp4 .mov .mkv .webm
.m4v .avi` file, and runs `ff ingest` on anything new. Skipped:

- already-indexed files (idempotent)
- hidden directories (anything starting with `.`)
- non-media extensions

Ingestion artifacts — scenes, transcripts, CLIP embeddings — are written to
`$FANDOMFORGE_CACHE_DIR/library/derived/<blake2>/` and shared across every
project. A file that also appears in a per-project `raw/` folder is recognized
by content hash and not re-ingested.

For a 500-movie corpus expect ~10 GB of derived artifacts and hours of CLIP
time on the first scan. Subsequent scans are near-instant.

## 3. Inspect

```bash
ff library list                     # roots + fandom counts
ff library list --sources           # every indexed file
ff library list --fandom "John Wick"
ff library list --status failed     # which ingests broke
ff library show "wick"              # free-text search
```

## 4. Fix fandom labels where auto-inference was wrong

```bash
ff library tag /Volumes/Movies/mismatched/JohnWick2014.mp4 --fandom "John Wick"
```

## 5. Spin up an edit from the library

```bash
ff project new my-action-edit
ff grab song --project my-action-edit --search "Fall Out Boy Centuries"
ff autopilot --project my-action-edit \
    --from-library \
    --fandom-mix "John Wick:0.4,Mad Max:0.6" \
    --song projects/my-action-edit/assets/song.mp3 \
    --prompt "heroes falling into neon rain, bullet ballet, 60 seconds"
```

Autopilot:

- Skips `ingest_sources` (library already has everything)
- Symlinks matching source files into `projects/my-action-edit/raw/`
- Runs the usual beat-analyze → edit-plan → shot-propose → QA → roughcut → color → export chain
- The shot proposer's no-reuse constraint ensures the same 2-second clip isn't picked twice (unless an `intent: callback` is declared in the plan)

## 6. Unlink (retire a folder)

```bash
ff library unlink home                   # forget the root, keep files indexed
ff library unlink home --delete-sources  # also drop the source rows
```

Ingested artifacts under `derived/<blake2>/` stay on disk either way — if you
re-link the same folder later the cache is still hot.

## Gotchas

- **Move the folder → re-link.** The library stores canonical paths. If
  `/Volumes/Movies/` becomes `/Volumes/Archive/Movies/`, run `ff library
  unlink` + `link` + `scan` to repoint. The blake2 hashes match, so derived
  artifacts reuse.
- **Symlink hygiene.** When `--from-library` runs, autopilot symlinks from
  `projects/<slug>/raw/` to the real library paths. The FCPXML exporter
  resolves symlinks back to their real locations before writing so the NLE
  finds the media even if the project folder moves.
- **Song quality.** `ff grab song --search` prefers Official Audio uploads
  over Music Videos. The library itself doesn't host music — the song still
  comes from `assets/` per project.
- **Per-file fandom granularity.** Shots inherit fandom from their parent
  source. If you need per-shot fandom for a cross-over scene, you currently
  have to ingest that clip twice under two fandom labels. On the list.
