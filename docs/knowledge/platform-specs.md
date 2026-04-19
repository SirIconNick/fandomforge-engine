# Platform Specs

Exact export settings and best practices for each major video platform as of 2026.

## YouTube

### Horizontal (traditional)
- **Resolution:** 1920x1080 (standard), 3840x2160 (4K)
- **Frame rate:** 24 fps, 25 fps, 30 fps, 48 fps, 50 fps, 60 fps
- **Codec:** H.264 (standard), H.265/HEVC, AV1 (large files)
- **Bitrate:**
  - 1080p 30fps: 8-12 Mbps
  - 1080p 60fps: 12-20 Mbps
  - 4K 30fps: 35-45 Mbps
  - 4K 60fps: 53-68 Mbps
- **Audio:** AAC-LC, 48 kHz, 384 kbps stereo
- **Container:** MP4 (preferred), MOV

### Vertical (YouTube Shorts)
- **Resolution:** 1080x1920
- **Duration:** 60 seconds max (3 minutes if opted in)
- **Frame rate:** 30, 60 fps recommended
- **Audio:** AAC-LC, 48 kHz, 192 kbps
- **Other:** Same codec as horizontal

### Loudness
- **Target:** -14 LUFS integrated
- **True peak:** -1 dBTP max
- **Louder than -14:** YouTube normalizes down, quality degrades

### Upload considerations
- Files up to 256 GB / 12 hours (for verified)
- Thumbnail: 1280x720, JPG/PNG, under 2 MB
- Title: up to 100 characters
- Description: up to 5,000 characters — CREDIT YOUR SONG AND SOURCES

## TikTok

### Standard
- **Resolution:** 1080x1920 vertical (preferred), 1920x1080 horizontal also supported
- **Duration:** 3 seconds to 10 minutes (typical sweet spot: 15-60 seconds)
- **Frame rate:** 30 fps recommended, 60 fps supported
- **Codec:** H.264
- **Bitrate:** 6-12 Mbps for 1080p
- **Audio:** AAC, 48 kHz, 128-192 kbps
- **Container:** MP4

### Safe zones
TikTok UI obscures parts of the screen:
- **Top safe:** 150px from top (for user icon)
- **Right safe:** 150px from right (for action buttons)
- **Bottom safe:** 350px from bottom (for caption + UI)
- **Center content area:** 1080x1420 roughly

Never put critical text or faces in the unsafe zones.

### Loudness
- **Target:** -14 to -16 LUFS
- **True peak:** -1 dBTP

### Music considerations
- TikTok's built-in sound library is pre-cleared
- Your own song may be muted or blocked — add from TikTok library if possible
- "CapCut Original Sound" vs using actual song: library version preferred

## Instagram Reels

### Standard
- **Resolution:** 1080x1920 vertical
- **Duration:** up to 90 seconds (standard), 15 minutes for some accounts
- **Frame rate:** 30 fps (60 fps supported but optional)
- **Codec:** H.264
- **Bitrate:** 5-10 Mbps
- **Audio:** AAC, 48 kHz, 128-256 kbps
- **Container:** MP4

### Safe zones
- **Top:** 220px safe margin
- **Bottom:** 310px safe margin (caption, profile bar)
- **Right:** 190px safe margin

### Loudness
- **Target:** -14 LUFS
- **True peak:** -1 dBTP

## Twitter / X

### Standard
- **Resolution:** up to 1920x1080
- **Aspect ratio:** 1:1 (square), 16:9, 9:16
- **Duration:** up to 2:20 (standard), up to 10 min for Premium
- **Frame rate:** 30 fps or 60 fps
- **Codec:** H.264 High profile, H.265 supported
- **Bitrate:** 5-25 Mbps
- **Audio:** AAC-LC, 128 kbps
- **Container:** MP4, MOV

### Loudness
- **Target:** -14 LUFS
- **True peak:** -1 dBTP

### Considerations
- Twitter compresses aggressively — export at higher bitrate than target to preserve quality post-compression
- 2:20 is a tough runtime limit for longer edits

## Cross-platform considerations

### Vertical vs horizontal
Many edits benefit from parallel exports:
- Horizontal master for YouTube main channel
- Vertical cutdown for Shorts / TikTok / Reels

Vertical cutdowns require:
- Reframing every shot (characters in frame center or tracked)
- Safe-zone awareness
- Often re-cutting — 2min edit becomes 40s vertical

### Frame rate choice
- **24/23.976 fps** — cinematic feel, matches movie source
- **30 fps** — standard digital, platform-friendly
- **60 fps** — smooth, good for action/hype edits, larger files

Match your edit feel to the choice. An emotional edit at 60 fps can feel jarring. An action edit at 24 fps can feel laggy.

### Aspect ratios at a glance

| Aspect | Name | Use |
|---|---|---|
| 16:9 | Widescreen | YouTube landscape, Twitter, horizontal web |
| 9:16 | Vertical / portrait | Shorts, TikTok, Reels |
| 1:1 | Square | Instagram feed, some Twitter |
| 4:5 | Portrait (tall feed) | Instagram feed (preferred tall) |
| 2.39:1 | Cinemascope | Cinematic edits, letterboxed |

### Cinemascope letterbox
For cinematic look on 16:9 platforms, add 2.39:1 letterbox bars. Pros: cinematic feel. Cons: loses vertical real estate, not loved by all viewers.

## Upload metadata hygiene

Always include:
- **Title** — descriptive but hooky
- **Description**:
  - First line: what the edit is about
  - Middle: song credit (artist — title)
  - Middle: source credits (films/shows used)
  - End: your handles, subscribe CTA
- **Tags / hashtags** — relevant, not spammy
- **Thumbnail** — bespoke, not auto-generated

Example description:
```
Three mentors. Three falls. One lesson.

A multifandom edit about the teachers who taught us the thing that killed them.

Song: The Weeknd — Until I Bleed Out
Sources: Revenge of the Sith, Half-Blood Prince, Avengers: Endgame, Attack on Titan

Watch in HD. No copyright infringement intended. All rights belong to their respective owners.

Follow: @[handle]
```

Don't claim ownership. Don't pretend it's original. Credit everything. Good faith never hurts.

## Export checklist

Before you hit render on your final:

- [ ] Correct resolution for target platform
- [ ] Correct frame rate (no forced conversion artifacts)
- [ ] LUFS checked and within target
- [ ] True peak under -1 dBTP
- [ ] Aspect ratio correct, no accidental letterbox
- [ ] Opening 3 seconds are hook-worthy
- [ ] Final frame is intentional
- [ ] Bitrate high enough for quality but reasonable for upload
- [ ] Audio synced (no drift from FPS mismatch)
- [ ] Thumbnail frame exists (if you need one)
- [ ] Description written
