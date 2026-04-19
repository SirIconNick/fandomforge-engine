# NLE Integration

Getting your FandomForge plan into your editor of choice.

## DaVinci Resolve

### 1. Import markers
```bash
python scripts/markers-to-resolve.py projects/<slug>/beat-map.json \
    -o projects/<slug>/markers.edl --fps 24
```
In Resolve: **File → Import → Timeline → from EDL...** Choose the EDL. Markers appear on your timeline.

### 2. Set up bins
Use the structure from `editor-guide`:
```
01_Song/
02_Sources/
  Marvel/
  HarryPotter/
  ...
03_Selects/
04_Graphics/
05_SFX/
06_Exports/
```

Create subclips of your shot list entries inside `03_Selects/`.

### 3. Color setup
On the Color page, create your node tree per `color-grader`'s plan:
1. Right-click first clip → Add Node → Serial → Label "Primary"
2. Repeat for each layer: Saturation, Contrast, LUT, Per-Source, Finish
3. Right-click node → Copy grade → apply to all clips as base
4. Per-source: keyframe or track in the "Per-Source" node for each clip group

### 4. Audio tracks
In Fairlight:
- A1: Song
- A2: Dialogue selects (muted except where used)
- A3-A5: SFX layers

Add ducking via keyframes on A1 whenever you open A2 or layer a big SFX.

### 5. Export
**Deliver page** → **YouTube preset** (for YouTube) or custom H.264 at your target bitrate. Audio: AAC 320kbps.

## Premiere Pro

### 1. Import markers
```bash
python scripts/markers-to-resolve.py projects/<slug>/beat-map.json \
    -o projects/<slug>/markers.csv --format csv --fps 24
```
In Premiere: **Window → Markers**, then **Marker panel → menu → Import Markers...** Select the CSV.

### 2. Sequence settings
- New Sequence → Custom
- 1920x1080 (or 1080x1920 vertical)
- Frame rate matching song / source
- 48 kHz audio

### 3. Color via Lumetri
For each source group, create an adjustment layer:
- Basic Correction → WB, exposure, contrast
- Creative → LUT (at 30-80% intensity)
- Color Wheels → target look
- Curves → final polish

### 4. Audio routing
Use Essential Sound panel:
- Song: Music preset
- Dialogue selects: Dialogue preset → enable ducking (auto-ducks music when dialogue plays)
- SFX: SFX preset

Add Loudness Radar to your master output: Effects → Audio Effects → Loudness Radar. Target -14 LUFS.

### 5. Export
**File → Export → Media** (Cmd/Ctrl+M)
- Format: H.264
- Preset: Match Source - High bitrate (or custom 25 Mbps VBR 2-pass)
- Audio: AAC, 320kbps, 48 kHz

## CapCut (Desktop)

CapCut doesn't support marker import. Use the beat-map.md as reference.

### 1. Start project
- New Project → 1080p 30fps (or 9:16 1080x1920 for vertical)

### 2. Drop song
Add to audio track. Tap song → **Beats → Auto**. Review the auto-detected beats.

### 3. Override with FandomForge beats
Your `beat-map.json` has more reliable downbeats than CapCut's auto. Open `beat-map.md` beside CapCut and manually tap to add markers at the FandomForge downbeat times.

### 4. Match clips to markers
Drop clips → tap beat-match → clips snap to markers.

### 5. Color via built-in adjustments
CapCut's color tools are limited vs Resolve/Premiere. Use LUT filters + manual exposure/contrast for each clip.

### 6. Export
- Format: MP4
- Resolution: 1080p
- Frame rate: 30 fps (or 60 for hype)
- Bitrate: High (CapCut picks internally; often ~15 Mbps)

## CapCut (Mobile)

Same as desktop but:
- More limited for multi-track SFX
- Harder to manually align to FandomForge's beat map (no precise timeline zoom)
- Better for quick-turn shorts than full multifandom edits

## Vegas Pro

### 1. Import markers
```bash
python scripts/markers-to-resolve.py projects/<slug>/beat-map.json \
    -o projects/<slug>/markers.edl --fps 30  # match your project
```
In Vegas: **File → Import → EDL Text File**.

### 2. Color
Vegas's built-in color is limited. Consider:
- Using Vegas for assembly, XML-export to Resolve for color, re-import cut back to Vegas
- Or staying in Vegas with Secondary Color Corrector FX + Color Grading FX

### 3. Audio
Standard Vegas audio tracks. Use compression plugins for ducking (sidechain via ReaComp or similar VST).

### 4. Export
Render As → MAGIX AVC/AAC MP4 (or similar). Set to match YouTube specs.

## Common across all NLEs

### Frame rate mismatch warnings
If your source clips are 23.976 and your timeline is 30, you'll get drift. Either:
- Match timeline to source frame rate (if all sources agree)
- Transcode sources to timeline frame rate first (ffmpeg: `-r 23.976`)
- Accept the drift (visible on very long edits)

### Variable frame rate (VFR) issues
Most YouTube / streaming rips are VFR. Constant frame rate (CFR) is required for frame-accurate cuts.

Fix:
```bash
ffmpeg -i input.mp4 -vf fps=24 -c:v libx264 -crf 18 -c:a copy output-cfr.mp4
```

### Audio drift
If audio drifts from video over a long edit, usually a sample rate mismatch. Ensure all audio is 48 kHz. Convert with:
```bash
ffmpeg -i input.mp3 -ar 48000 -ac 2 output-48k.wav
```

### Proxy media for performance
All major NLEs support proxies. If 4K sources stutter:
- Resolve: right-click → Generate Optimized Media
- Premiere: create proxies via Media Encoder
- CapCut: handles this automatically
- Vegas: set Preview Quality to Draft

### Platform-specific export

| Platform | Resolution | FPS | Bitrate | Codec |
|---|---|---|---|---|
| YouTube Horizontal | 1920x1080 | 24/30/60 | 20-30 Mbps | H.264 |
| YouTube Shorts | 1080x1920 | 30/60 | 15-20 Mbps | H.264 |
| TikTok | 1080x1920 | 30 | 10-15 Mbps | H.264 |
| Instagram Reels | 1080x1920 | 30 | 10-15 Mbps | H.264 |
| Twitter/X | 1920x1080 or 1280x720 | 30 | 8-10 Mbps | H.264 |

### Loudness targets per platform

Aim for -14 LUFS integrated, -1 dBTP true peak for all major platforms in 2026. Check with:
- Resolve: Fairlight → Loudness Meter
- Premiere: Effects → Audio Effects → Loudness Radar
- CapCut: limited but reports approximate value
- Vegas: third-party VST (Youlean Loudness Meter is free)

### Round-trip color

If your NLE's color tools are limited, you can:
1. Edit to picture lock in your NLE
2. Export XML or AAF
3. Open in DaVinci Resolve, do color
4. Render color-final back as ProRes / DNxHR
5. Re-import into your NLE for final audio mix and export

This is the path many pro indie workflows take. Resolve's color is top-tier.
