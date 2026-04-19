# Beat Mapping Workflow

From a raw song to NLE markers, step by step.

## 1. Get a clean audio file

Your song needs to be:
- A format librosa can read (MP3, WAV, FLAC, OGG, M4A — basically anything ffmpeg handles)
- Legally yours (owned, not streamed/DRM)
- The actual song you'll use (not a lower-quality preview)

If you only have a streaming URL, you have a problem: you cannot ethically rip from streaming services. Buy the song.

## 2. First-pass analysis

```bash
source .venv/bin/activate
ff beat analyze path/to/song.mp3 -o projects/<slug>/beat-map.json
```

Check the output:
- BPM — does it match what you'd clap along to?
- Drops — are they where the song's biggest moments are?
- Confidence — > 0.7 is usually good

## 3. Fix common issues

### BPM is half or double what you expect
Your song might be 70 BPM but detected as 140, or vice versa. Add a hint:

```bash
ff beat analyze song.mp3 --tempo-hint 70 -o beat-map.json
```

### Drops are in the wrong place
The detector missed them. Try:
- `--snare-bias` if the drop is snare-driven (no heavy bass)
- Reviewing the JSON and manually editing the drops array

### BPM drifts through the song
Some songs shift tempo. The current analyzer assumes one global BPM. For tempo-varying songs, note in your beat-map.md that beats after the change are approximate and verify manually.

### Confidence is low (< 0.5)
Song may be ambient, orchestral, or very percussion-light. Beat sync with this song will be difficult. Consider a different song, or accept that not all cuts will beat-lock.

## 4. Visualize in the dashboard

```bash
cd web && pnpm dev
```

Navigate to `http://localhost:4321/projects/<slug>`. The visualizer shows:
- Energy curve (white line)
- Buildups (orange blocks)
- Breakdowns (blue blocks)
- Downbeats (green ticks at the bottom)
- Drops (red vertical lines + dots)

Verify visually that the features land in the right places.

## 5. Export markers to your NLE

### For DaVinci Resolve (EDL)

```bash
python scripts/markers-to-resolve.py projects/<slug>/beat-map.json \
    -o projects/<slug>/markers.edl \
    --fps 24
```

In Resolve: Timeline → Import → Timeline from EDL. Choose your song sequence. Markers appear at every downbeat (green) and drop (red).

### For Premiere Pro (CSV)

```bash
python scripts/markers-to-resolve.py projects/<slug>/beat-map.json \
    -o projects/<slug>/markers.csv \
    --format csv --fps 24
```

In Premiere: open Markers panel → Import Markers. Import the CSV.

### For CapCut

CapCut doesn't support marker imports. Use your beat-map.md as a reference and manually tap to add markers on the CapCut timeline. The visualizer in the web dashboard helps verify you're adding at the right spots.

### For Vegas Pro

Vegas can import markers via EDL too:
```bash
python scripts/markers-to-resolve.py ... --fps 30  # or your project fps
```

## 6. Align cuts to markers

With markers in place, the editing flow becomes:
1. Scrub to a marker
2. Make your cut
3. The cut is on the beat

Key cut points for multifandom edits:
- **Every downbeat** is a valid cut point
- **Drops** are mandatory cut / visual peak points
- **Beat 3 of each bar** is a strong secondary cut point
- **1/8 beats during buildups** for accelerating cut rhythm

## 7. Verify sync

After your rough cut, watch the full edit. If any cut feels "off" by a frame or two:

- Most likely your cut is 15-30 ms late. Human perception prefers slight early.
- Try nudging the offending cut 1-2 frames earlier (depending on FPS).
- If many cuts feel late, apply a global offset: trim the start of your song by 15-30 ms.

## 8. Update the beat-map.md

As you work with the beat map, update `projects/<slug>/beat-map.md` with:
- Which drops you're using and for what
- Which buildup covers which shot sequence
- Any tempo notes you noticed
- Any vocal moments you want to lyric-sync

This becomes documentation for if you re-visit the project later.

## 9. When you re-cut / new version

If you change the song or re-analyze with different options, regenerate:

```bash
ff beat analyze song.mp3 -o beat-map.json --tempo-hint <X>
python scripts/markers-to-resolve.py beat-map.json -o markers.edl --fps 24
```

Re-import markers into your NLE. Your old cuts may or may not still align — re-verify.

## Technical notes

### Why librosa?
Industry-standard, open-source, Python-native. Its beat tracker uses dynamic programming on the onset envelope and is fast enough to run on a full song in seconds.

### Why not a dedicated beat-detection model like madmom?
madmom has a more accurate downbeat detector but is harder to install and has heavier dependencies. For multifandom editing purposes, librosa + our drop detector is sufficient. If you want madmom-level accuracy, you can swap the analyzer — the JSON format is stable.

### Why does the visualizer use SVG?
Client-side canvas would require heavier React and more bundle size. SVG scales cleanly, accepts CSS styling, and works at any viewport.

### Why 22050 Hz sample rate?
Beat detection operates on onset envelopes and low-frequency features. 22050 Hz preserves all information we need. Using 44100 Hz (CD quality) doubles memory and analysis time without improving accuracy meaningfully.

### Can I use ffmpeg to convert audio before analysis?
Yes. Example:
```bash
ffmpeg -i song.m4a -ar 22050 -ac 1 song.wav
ff beat analyze song.wav -o beat-map.json
```
Mono downmix at 22050 Hz matches librosa's internal processing and can save time on long files.
