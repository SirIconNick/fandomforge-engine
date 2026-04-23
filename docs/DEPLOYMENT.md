# Deploying FandomForge

TL;DR — for $0/month you run the backend on your Mac and expose it via Cloudflare Tunnel. Your laptop needs to be on for the public URL to work, which is fine if you're using the tool yourself anyway.

## The $0 path — Cloudflare Tunnel

One terminal, three commands. The `ff serve` server already bundles the HTML UI + API on the same port, so one tunnel URL gives you the whole app.

```sh
# 1. Install cloudflared once
brew install cloudflared

# 2. Start the server in one terminal
tools/.venv/bin/ff serve

# 3. Start the tunnel in a second terminal
tools/.venv/bin/ff tunnel
```

The tunnel command prints your public URL in a big banner:

```
============================================================
  PUBLIC URL:  https://adjective-adjective-1234.trycloudflare.com
============================================================
```

That URL is also written to `.cache/ff/tunnel-url.txt` and surfaces on `/api/health`. Bookmark it. It stays valid as long as cloudflared keeps running.

**The URL changes every time you restart `ff tunnel`.** That's the tradeoff for zero signup. To stop: `Ctrl+C` in the tunnel terminal.

### Protecting the public URL with an API key

Anyone with the URL can hit your API. If you don't want that:

```sh
# Pick a strong random key — openssl rand is fine
export FF_API_KEY="$(openssl rand -hex 32)"

# Restart the server so it picks up the env var
tools/.venv/bin/ff serve
```

Now `/api/*` endpoints require the `X-API-Key` header matching your key. The HTML UI at `/` and `/static/*` stay open. `/api/health` stays open too so you can check liveness without the key.

`ff deploy-check` verifies your environment before you go public:

```sh
$ tools/.venv/bin/ff deploy-check
  ! FF_API_KEY set  open-access mode (SET THIS BEFORE TUNNELING PUBLIC)
  ✓ cloudflared installed
  ✓ .cache/ff/ writable
  ✓ ≥1 bucket-report.json on disk
```

## Stable-URL alternative — Tailscale Funnel

Cloudflare quick tunnels regenerate their URL on every restart. If you share your URL with friends or keep bookmarks, that's annoying. Tailscale Funnel gives you a stable `https://<machine>.<tailnet>.ts.net` URL for free (personal use).

```sh
# One-time: install and sign in
brew install tailscale
tailscale up

# Each session: expose the port via Funnel
tools/.venv/bin/ff tunnel --via tailscale
tools/.venv/bin/ff tunnel-stop --via tailscale  # to stop
```

Tailscale runs in the background and survives terminal closures, unlike cloudflared quick tunnels.

## Paid-but-always-up alternatives

If you want the site accessible without your laptop running, you need a cloud VM. Costs ~$5-8/month minimum for the RAM our forensic pipeline needs.

### Fly.io recipe (recommended paid path)

The forensic pipeline needs ~1GB RAM (OpenCLIP + whisper models loaded). Fly's `shared-cpu-1x` with 1GB is ~$5/month.

```sh
# Install flyctl
brew install flyctl
fly auth login

# In repo root — launch creates fly.toml + Dockerfile if needed
fly launch --name fandomforge --no-deploy

# Attach persistent volume for corrections journal + forensic cache
fly volumes create ff_data --size 10 --region <your-region>

# Set secrets (never commit these)
fly secrets set FF_API_KEY="$(openssl rand -hex 32)"
fly secrets set FF_JOB_STORE="sqlite"
fly secrets set FF_DISABLE_DOCS="1"

# Deploy
fly deploy
```

A working `Dockerfile` + `fly.toml` is what `fly launch` will produce. Key bits:
- Python 3.13 base image
- `RUN pip install -r requirements.txt` including fastapi/uvicorn
- `RUN apt-get install ffmpeg` (yt-dlp needs it)
- `VOLUME /data` mounted at `.cache/ff/`
- `CMD uvicorn fandomforge.web.server:app --host 0.0.0.0 --port 8080`

### Railway recipe

Similar flow but Railway's dashboard is more hand-holdy. Also ~$5-10/month.

```sh
railway init
railway up
railway variables set FF_API_KEY=...
```

## Environment variables the server reads

| Var | Default | Purpose |
|---|---|---|
| `FF_API_KEY` | unset | Require this in X-API-Key header for /api/*. Unset = open. |
| `FF_JOB_STORE` | memory | Set to `sqlite` to use the persistent job store at `.cache/ff/jobs.sqlite`. |
| `FF_JOBS_DB` | `.cache/ff/jobs.sqlite` | Override SQLite job store path. |
| `FF_DISABLE_DOCS` | unset | Set to `1` to disable the auto /api/docs endpoint (reduces surface). |
| `FF_CORRECTIONS_BIAS` | `1` | Set to `0` to disable the human-correction bias layer. |
| `FF_TRAINING_BIAS` | `1` | Set to `0` to disable the training-journal bias layer. |
| `FF_FORENSIC_BIAS` | `1` | Set to `0` to disable the corpus-forensic bias layer. |
| `FF_API_KEY` public path | unset | exempts from auth: `/`, `/api/health`, `/static/*`, `/api/docs`, `/openapi.json`. |

## Verifying the deployment

After `ff tunnel` prints a URL, verify:

```sh
# 1. HTML loads
curl -s https://<your-url>/ | head -5

# 2. Health endpoint (always open)
curl -s https://<your-url>/api/health

# 3. If FF_API_KEY is set, unauthed /api/ requests should 401
curl -s https://<your-url>/api/buckets
# {"detail": "API key required. Set X-API-Key header or ?api_key= query."}

# 4. With the key
curl -s -H "X-API-Key: $FF_API_KEY" https://<your-url>/api/buckets
```

## Troubleshooting

**Tunnel says "failed to register tunnel"** — Your cloudflared is too old or Cloudflare's edge is down. `brew upgrade cloudflared` and retry.

**Site works locally but not via tunnel** — Check the server is bound to `127.0.0.1`, not `localhost` or `::1`. Cloudflare's tunnel connects to IPv4 loopback. `ff serve --host 127.0.0.1 --port 4321` is the default and works.

**API calls hang via tunnel but work locally** — Cloudflare Tunnel has a 100-second timeout per request. The forensic analyze endpoint is already async (returns job_id immediately, UI polls). If you're hitting a synchronous endpoint that takes >100s, refactor to async.

**Lost my public URL** — `ff tunnel-url` prints the last one from disk.

**Server restarted, jobs vanished** — You're running without `FF_JOB_STORE=sqlite`. The in-memory store is process-local. Switch to SQLite for persistence.
