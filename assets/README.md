# Assets

Where global assets live — LUTs, reference images, demo files.

## Structure

```
assets/
├── demo/        Demo files (test audio for beat-detection, reference images)
├── luts/        Color grading LUTs (not committed — too large)
└── fonts/       Font reference (not committed — license varies)
```

## Demo files

- `demo/test-120bpm-with-drop.wav` — a synthetic test file with a clear 120 BPM pulse and a bass drop at 7 seconds. Used to validate beat detection.

## LUTs

Place downloaded LUTs (`.cube` files) in `assets/luts/`. Recommended free starting points:
- **Juan Melara P20** — cinematic teal-orange
- **Arri Alexa emulation LUTs** — cinematic baseline
- **Lutify.me starter pack**

These are NOT committed because LUT files are often licensed and can be large.

## Fonts

For title design work, tell your editor to install fonts system-wide:
- **Bebas Neue** (free, Google Fonts)
- **Anton** (free, Google Fonts)
- **Playfair Display** (free, Google Fonts)
- **Cormorant** (free, Google Fonts)
- **EB Garamond** (free, Google Fonts)
- **Space Grotesk** (free, Google Fonts)

Paid alternatives mentioned in `agents/title-designer.md` require their own licensing.
