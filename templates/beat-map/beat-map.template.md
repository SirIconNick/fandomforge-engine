# {{SONG}} — Beat Map

**Artist:** {{ARTIST}}
**Duration:** {{DURATION}}
**BPM:** {{BPM}}
**Time signature:** {{TIME_SIG}}
**Key:** {{KEY}} (if known)
**BPM confidence:** {{CONFIDENCE}}

## Feel

{{ONE_SENTENCE_DESCRIPTION}}
<!-- e.g. "Slow-build cinematic ballad with a massive drop at 0:45 and a final release at 2:15" -->

## The drops (your edit's peak moments)

| # | Time | Intensity | Type | Notes |
|---|------|-----------|------|-------|
| 1 | {{TIME}} | 0.XX | main_drop | {{NOTES}} |
| 2 | {{TIME}} | 0.XX | second_drop | {{NOTES}} |
| 3 | {{TIME}} | 0.XX | outro_drop | {{NOTES}} |

## Buildups

| # | Start | End | Duration | Curve | Notes |
|---|-------|-----|----------|-------|-------|
| 1 | {{TIME}} | {{TIME}} | {{DUR}} | exponential | pre-main-drop |
| 2 | {{TIME}} | {{TIME}} | {{DUR}} | linear | pre-second-drop |

## Breakdowns (rest beats)

| # | Start | End | Duration | Intensity | Notes |
|---|-------|-----|----------|-----------|-------|
| 1 | {{TIME}} | {{TIME}} | {{DUR}} | 0.2 | breath before drop 2 |

## Key vocal moments

Lines worth sync targeting:

| Time | Line | Sync target |
|------|------|-------------|
| {{TIME}} | "{{LINE}}" | literal / thematic |

## Downbeats (first 8 bars)

| Bar | Downbeat time | Notes |
|-----|---------------|-------|
| 1 | {{TIME}} | |
| 2 | {{TIME}} | |
| 3 | {{TIME}} | |
| 4 | {{TIME}} | |
| 5 | {{TIME}} | |
| 6 | {{TIME}} | |
| 7 | {{TIME}} | |
| 8 | {{TIME}} | |

(Full beat array lives in `beat-map.json` — use the tool to generate it)

## Energy curve

Rough per-section energy (0-100). Used for matching shots to the right intensity.

| Time range | Energy | Section label |
|------------|--------|---------------|
| 0:00 – 0:10 | 20 | intro |
| 0:10 – 0:30 | 45 | verse 1 |
| 0:30 – 0:45 | 65 | pre-chorus / buildup |
| 0:45 – 1:00 | 95 | first drop / chorus |
| 1:00 – 1:15 | 55 | post-drop valley |
| 1:15 – 1:40 | 75 | verse 2 / rise |
| 1:40 – 1:55 | 100 | second drop |
| 1:55 – 2:15 | 50 | outro / comedown |
| 2:15 – end | 80 | final lift / landing |

## Cut rhythm recommendations

| Section | Cut frequency | Transition style |
|---------|---------------|------------------|
| Intro | Long holds (2-4s) | Slow dissolves or stillness |
| Buildup | Accelerating (2s → 0.5s) | Hard cuts, invisible zoom |
| Drop | Hold on first shot 1-2 beats, then match beat | Flash-stack on impact, then hard cuts |
| Valley | Medium (1-2s) | Match cuts on motion |
| Second drop | Fast (0.3-0.5s) | Hard cuts on beat |
| Outro | Gradually slowing | Hold final shot 2-3s, dissolve to black |

## Cut heat map

Where NOT to cut:
- Last 4 frames before each drop (tension preservation)
- During held vocal notes
- During silent gaps longer than 200ms

Where cuts always land well:
- Downbeats (every bar start)
- Vocal hook hits
- Snare hits in the choruses

## Song credit

When you publish, credit exactly as:

```
Song: {{ARTIST}} — {{SONG_TITLE}}
{{ALBUM / YEAR if known}}
{{LINK TO ARTIST if you want to promote them}}
```
