# FandomForge — Vercel frontend

Static paste-link UI that talks to a remote `ff serve` backend (typically running on your Mac via Cloudflare Tunnel). This site has no backend of its own — every API call goes to the URL you enter in the "First-time setup" panel.

## Deploy

```sh
cd vercel-site
npx vercel login     # one-time
npx vercel --prod    # deploy
```

Vercel prints a URL like `https://fandomforge.vercel.app`. Visit it, and the setup panel prompts for:

- **Backend URL** — paste the output of `ff tunnel-url` on your Mac
- **API key** — whatever `$FF_API_KEY` is set to on your Mac

Both get stored in localStorage. Every API call to the backend attaches the key via the `X-API-Key` header.

## Updating the backend URL

Cloudflare quick tunnels regenerate their URL on restart. When your tunnel changes:

1. Run `ff tunnel-url` locally to get the new URL
2. Click **settings** in the top-right of the Vercel site
3. Paste the new URL, save

If you want a stable URL, use `ff tunnel --via tailscale` instead.

## Prerequisites on the backend

Your `ff serve` process must have CORS configured to allow requests from the Vercel domain. Either:

- Leave `FF_CORS_ORIGINS` unset (defaults to `*`, any origin allowed)
- Or set `FF_CORS_ORIGINS=https://fandomforge.vercel.app` to lock it down

## Architecture

```
Browser (Vercel)
    ↓ fetch with X-API-Key header
Cloudflare Tunnel
    ↓ forwards to 127.0.0.1:4321
ff serve (your Mac)
    ↓ runs forensic pipeline
disk (.cache/ff/, references/)
```

Every piece of FandomForge data still lives on your Mac. Vercel only hosts the HTML/CSS/JS.
