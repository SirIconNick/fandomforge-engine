# FandomForge Web UI

Paste-link forensic + human-in-the-loop training, running locally on port 4321.

## What it does

1. You paste a YouTube URL
2. FandomForge downloads it, runs the full forensic pipeline (PySceneDetect + librosa beat analysis + madmom downbeat snap + OpenCLIP source clustering + whisper dialogue + audio forensics)
3. Returns auto-tags: bucket classification, projected grade, detected strengths/weaknesses, craft techniques spotted
4. You can correct anything it got wrong — bucket label, craft weight sliders, free-text notes
5. Your correction writes to `.cache/ff/training/corrections.jsonl` and feeds back into `craft_weights_for()` at 40% blend, alongside the forensic corpus bias (20%) and training bias (30%) on top of the hand-tuned table

## Run it

```sh
# First time — install web deps into the venv
tools/.venv/bin/python -m pip install fastapi "uvicorn[standard]" jinja2 python-multipart

# Run
tools/.venv/bin/ff serve
# or bind to LAN so you can hit it from your phone
tools/.venv/bin/ff serve --host 0.0.0.0 --port 4321
```

Then open http://127.0.0.1:4321/.

## Architecture

```
Browser
  ↓ POST /api/analyze { url, bucket_hint }
FastAPI
  ↓ spawn thread → run_pipeline
yt-dlp → .cache/ff/web/incoming/<video_id>/<video_id>.mp4
      → .cache/ff/web/incoming/<video_id>/<video_id>.song.wav
deconstruct_video()
  → PySceneDetect (scenes)
  → librosa beat analysis (primary) + madmom downbeat snap
  → OpenCLIP source clustering
  → whisper dialogue detection
  → audio forensics (SFX + dropout windows)
  → emits <video_id>.forensic.json
analyze_forensic() → bucket guess + strengths/weaknesses/techniques
  ↓ stored in in-memory JobStore
Browser polls /api/job/<job_id> every 1.5s until status=done
Browser renders result + correction form
  ↓ POST /api/correct { forensic_id, corrected_bucket, corrected_craft_weights, notes }
append_correction() → .cache/ff/training/corrections.jsonl
clear_cache() on forensic_craft_bias module
  ↓ next call to craft_weights_for('action') pulls 40% toward user's weights
```

## Correction flow — how the engine learns

Three bias layers stack on top of the hand-tuned `MFV_CRAFT_WEIGHTS` table:

| Layer | Source | Blend weight | Env toggle |
|---|---|---|---|
| 1 | Forensic corpus (reference MFVs) | 20% | `FF_FORENSIC_BIAS` |
| 2 | Training journal (real render outcomes) | 30% | `FF_TRAINING_BIAS` |
| 3 | **Human corrections (this UI)** | **40%** | `FF_CORRECTIONS_BIAS` |

Each layer's env var defaults to on. Set any to `0`/`false`/`no` for a clean A/B.

Corrections accumulate — the weighted mean across all corrections for a bucket becomes that bucket's correction signal, with a mild age decay so recent corrections pull harder than old ones.

## Deployment options

The forensic pipeline is CPU-heavy (OpenCLIP + whisper + madmom) and needs minutes per video. That rules out pure Vercel serverless hosting.

| Option | Frontend | Backend | Notes |
|---|---|---|---|
| **Local-only** (current) | uvicorn serves HTML | same FastAPI process | Simplest. Run `ff serve`. |
| **LAN-exposed** | uvicorn on 0.0.0.0 | same | `ff serve --host 0.0.0.0`. Works from any device on your network. |
| **Vercel frontend + self-hosted backend** | Next.js on Vercel | FastAPI on your machine or Railway/Fly | Frontend calls a public-URL backend. Needs reverse-proxy/tunnel (Cloudflare Tunnel, ngrok) if backend is on your laptop. |
| **Full cloud** | Next.js on Vercel | FastAPI on Railway + persistent disk | Real production setup. Ship state lives on Railway's disk. Corrections sync via git or S3. |

### Quick Cloudflare Tunnel recipe (for Vercel frontend hitting your local backend)

```sh
# One-time: install cloudflared, log in
brew install cloudflared
cloudflared tunnel login
cloudflared tunnel create fandomforge
cloudflared tunnel route dns fandomforge ff.<your-domain>

# Run the backend + tunnel
ff serve --host 127.0.0.1 --port 4321
cloudflared tunnel run --url http://127.0.0.1:4321 fandomforge
```

Then a Next.js frontend on Vercel can fetch from `https://ff.<your-domain>/api/*`.

## Data files

* **`.cache/ff/training/corrections.jsonl`** — append-only JSONL of every correction. Bias aggregator reads this.
* **`.cache/ff/training/journal.jsonl`** — render-outcome journal. Populated by `ff autopilot` runs.
* **`.cache/ff/web/incoming/<video_id>/`** — one dir per analyzed URL. Cached mp4/wav/forensic.json.
* **`references/<bucket>/bucket-report.json`** — corpus consensus. Rebuilt by `ff auto`.

## Autonomous mode

The launchd agent installed via `ff install-agent` runs `ff auto --limit 2` hourly. That:

1. Pulls up to 2 new URLs per bucket from `references/corpus.yaml` (skips what's already analyzed)
2. Re-mines bucket priors
3. Rebuilds bucket reports
4. Refreshes training priors from the journal

Logs: `.cache/ff/auto.log`. Uninstall: `ff uninstall-agent`.

## Tests

```sh
cd tools && .venv/bin/python -m pytest tests/test_web_server.py tests/test_corrections_journal.py -v
```

Full suite: `1193 passed, 10 skipped` as of this build.
