# Reference Catalog — What the Sync Planner Learned

Last regenerated: 2026-04-19 · Deep analyzer v1 · 148 videos across 5 corpora

This doc is what FandomForge learned by actually watching fandom edits instead
of guessing. Each playlist Nick dropped became a corpus; each corpus has a
`reference-priors.json` with per-video metrics plus rolled-up statistics. The
sync planner biases its matches toward these priors — duration targets come
from real fandom edits, not arbitrary heuristics.

## TL;DR

All five corpora cluster as **peaks-and-valleys pacing** — the signature
fandom-edit rhythm where the edit surges on drops and breathes on quieter
passages. Shared fingerprints across every corpus:

- **~60% beat-sync rate** — real editors lock ~6 of every 10 cuts to the beat
- **Tempo 117–129 BPM** — standard cinematic-action music range
- **Dark-graded** — 45–53% of shots land in the bottom quintile of luma
- **Fast intro** — first cut lands 2–4 seconds in, no slow opens
- **Balanced acts** — each third of the edit carries roughly equal shot count

Where they differ is **tempo of the cuts themselves** — pl4 cuts every 0.94s
(Believer / Skinhead territory), pl3/pl5 let shots breathe at 1.4s.

## The five corpora

### action-pl1 — Mixed multi-fandom, mid-tempo

| Signal | Value |
|---|---|
| Videos analyzed | 30 |
| Median shot duration | 1.07s |
| Cuts per minute | 44.0 |
| Shot duration range (p10–p90) | 0.93s – 2.00s |
| Act 1 / 2 / 3 shot distribution | 33% / 35% / 32% |
| Beat-sync rate | 60.5% |
| Median tempo | 117.5 BPM |
| Avg luma | 0.22 (dark) |
| Dark / bright shot % | 52.8% / 0.3% |
| Saturation | 0.41 |
| Intro latency | 2.4s |

**Hottest cuts:** Radioactive in the dark (74 cpm), Die In This Town (66 cpm),
MARVEL / The Search (64 cpm).

### action-pl2 — Similar to pl1 but slightly calmer

| Signal | Value |
|---|---|
| Videos analyzed | 30 |
| Median shot duration | 1.12s |
| Cuts per minute | 41.6 |
| Shot duration range (p10–p90) | 0.82s – 2.25s |
| Act 1 / 2 / 3 shot distribution | 30% / 35% / 35% |
| Beat-sync rate | 59.9% |
| Median tempo | 123 BPM |
| Avg luma | 0.24 |
| Dark / bright shot % | 48.0% / 1.1% |
| Saturation | 0.41 |
| Intro latency | 2.9s |

**Hottest cuts:** Radioactive in the dark (74 cpm), Multifandom Mashup 2014
(73 cpm), What a Wonderful World (70 cpm).

### action-pl3 — Most cinematic, longest holds

| Signal | Value |
|---|---|
| Videos analyzed | 30 |
| Median shot duration | 1.39s |
| Cuts per minute | 36.0 |
| Shot duration range (p10–p90) | 0.93s – 3.19s |
| Act 1 / 2 / 3 shot distribution | 34% / 36% / 30% |
| Beat-sync rate | 54.4% |
| Median tempo | 123 BPM |
| Avg luma | 0.25 |
| Dark / bright shot % | 47.9% / 1.6% |
| Saturation | 0.37 |
| Intro latency | 3.4s |

Notably lower beat-sync (54%) and longer intro — more willing to let a moment
land before cutting. Best reference for emotional edits that need breath.

**Hottest cuts:** Marvel / battle royale (73 cpm), Glitter & Gold (67 cpm),
GOTHAM / I'm So Sorry (65 cpm).

### action-pl4 — Fastest, most aggressive

| Signal | Value |
|---|---|
| Videos analyzed | 30 |
| Median shot duration | 0.94s |
| Cuts per minute | 46.0 |
| Shot duration range (p10–p90) | 0.78s – 2.14s |
| Act 1 / 2 / 3 shot distribution | 30% / 34% / 35% |
| Beat-sync rate | 59.8% |
| Median tempo | 129 BPM |
| Avg luma | 0.25 |
| Dark / bright shot % | 45.1% / 1.7% |
| Saturation | 0.39 |
| Intro latency | 4.2s |

Tightest cuts in the catalog. Act 3 carries the most shots — payoff-heavy
structure. Use as the reference when building high-energy combat edits.

**Hottest cuts:** MULTIFANDOM [Personal Skinhead] (87 cpm), Believer (77 cpm),
Radioactive in the dark (74 cpm).

### action-pl5 — Similar pocket to pl3

| Signal | Value |
|---|---|
| Videos analyzed | 28 |
| Median shot duration | 1.40s |
| Cuts per minute | 35.6 |
| Shot duration range (p10–p90) | 1.02s – 2.40s |
| Act 1 / 2 / 3 shot distribution | 30% / 35% / 35% |
| Beat-sync rate | 61.0% |
| Median tempo | 123 BPM |
| Avg luma | 0.22 |
| Dark / bright shot % | 52.1% / 0.6% |
| Saturation | 0.42 |
| Intro latency | 2.9s |

Highest beat-sync in the catalog — tight, disciplined editing despite the
longer holds. Good reference when the song is more rhythmic than melodic.

**Hottest cuts:** Person of Interest / Nobody Can Save Me Now (59 cpm),
Multifandom / Blood in the water (59 cpm), MARVEL / Enemy (58 cpm).

## How the planner uses this

`sync_planner.build_sync_plan` accepts a `reference_priors` arg (auto-loaded
from `reference_library.load_priors()` inside `step_sync_plan`). The scoring
function adds a `duration_prior` weight that rewards shots whose duration is
close to the learned median. With priors loaded, a shot that's 1.1s long gets
a full bonus against pl1/pl2's 1.07/1.12s median; a 3s shot gets partial
credit because it's out of the typical range.

Picking which corpus to lean on — right now: newest is used automatically. To
pin a specific one:

```bash
# use pl4's fast-cut priors for this project
cp references/action-pl4/reference-priors.json ~/.fandomforge/references/active/
```

## Extending the corpus

Add another playlist anytime:

```bash
ff reference ingest --playlist <URL> --tag <name> --max-videos 30
```

Default downloads are 360p — enough for scene-detect + beat analysis, tiny
enough that 150 videos fit in 1.6 GB. The analyzer is idempotent; re-running
`ff reference ingest --no-download --tag <name>` recomputes metrics without
re-downloading (useful when the deep analyzer gets smarter).

## Signals captured per video

Every video in every corpus carries the following in its `metrics` block:

**Shot rhythm:** shot count, avg/median/min/max/stddev shot duration, p25/p75/p90
percentiles, cuts-per-minute

**Pacing shape:** sliding-window pacing curve (30s window, 10s step), act
1/2/3 percentage, intro-to-first-cut latency

**Visual palette:** sampled shots count, avg luma, luma stddev, dark-shot %,
bright-shot %, hue median degrees, saturation mean

**Music sync:** tempo BPM, cuts-on-beat %, total cuts checked

**Motion:** ffmpeg scene-score event count (proxy for visual churn)

All of it lives in `references/<tag>/reference-priors.json`. Open one up to
see what the system actually knows.

## Gaps worth noting

- **Transition types not yet classified.** We detect cut boundaries but don't
  distinguish hard cuts from crossfades, whip pans, or speed ramps. Next
  analyzer pass.
- **Motion is a proxy, not a measurement.** We count scene-score events from
  ffmpeg; a real motion-vector analysis would be richer (and slower).
- **No per-corpus transition preference signal yet.** Currently the corpus
  differentiation is rhythm-based only.
- **One missing video per playlist on average** — a handful of yt-dlp
  downloads fail on age-gated or regionally-blocked content. Acceptable loss
  at this sample size; the priors are stable at n ≥ 25.
