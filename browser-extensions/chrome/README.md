# FandomForge Grab — Chrome extension

Right-click any video or audio URL (or an open tab) and pull it straight into a
FandomForge project via the running dashboard.

## Install (unpacked, for development)

1. Start the dashboard from the repo root:
   ```bash
   scripts/dev.sh    # runs on http://localhost:4321
   ```
2. Open `chrome://extensions` in Chrome / Edge / Brave.
3. Toggle **Developer mode** on (top-right).
4. Click **Load unpacked** and pick `browser-extensions/chrome/`.
5. Open the extension's **Options** (pin the icon, click it, hit the gear / "settings" link) and set:
   - **Dashboard URL** — default `http://localhost:4321`
   - **Default project** — populated from `/api/projects`
   - Default mode / resolution / cookie source

## Usage

- **Toolbar icon → popup** — paste or auto-fill the current tab's URL, pick project + mode, hit **Grab**.
- **Right-click a video / audio link → "Grab to FandomForge"** — one-click grab using your default project and mode.
- **Right-click the page → "Grab this page URL to FandomForge"** — captures the current tab URL.

All grabs go through the dashboard's `/api/grab`, which wraps `ff grab video`:
- Modes: `both` (video+audio mp4), `video` (silent mp4), `audio` (mp3 into `assets/`)
- Resolution cascades 1080 → 720 → 480 → best on format unavailable
- Subtitle failures fall back to media-only automatically
- Sidecar `.grab.json` with url, mode, sha256, attempts, route
- Cookies: pass a browser (Chrome / Firefox / etc.) to auth age-restricted content

## How it talks to the dashboard

- `GET http://<dashboard>/api/projects` — list projects for the dropdown
- `POST http://<dashboard>/api/grab` — run the grab

Both endpoints respond with `Access-Control-Allow-Origin: *` so the extension
(a `chrome-extension://` origin) can call them cross-origin. The dashboard is
expected to run locally on a trusted machine.

## Permissions

- `contextMenus`, `storage`, `notifications`, `activeTab` — all baked in.
- `host_permissions` — `http://localhost/*` and `http://127.0.0.1/*` by default.
- `optional_host_permissions` for any other dashboard URL — requested via the
  Options page when you change **Dashboard URL**.

No broad `<all_urls>` permission. The extension never reads the content of the
pages you visit; it only sees URLs you explicitly right-click or the active
tab's URL when you open the popup.

## Firefox / Safari

- Firefox: the manifest is MV3 and mostly portable. Swap `"service_worker"` for
  `"scripts": ["background.js"]` if you ship to Firefox — TODO.
- Safari: needs an Xcode wrapper. Not done yet.

## Hacking

Pure vanilla JS / HTML / CSS, no build step. Edit files and hit "Reload" on the
extension card in `chrome://extensions`.

## Automated end-to-end test

`test-extension.mjs` launches a headless Chromium with the extension loaded,
drives the popup + options page against a live dashboard, and verifies a real
grab lands end-to-end.

Easiest way to run it — the shell wrapper handles everything (build, dashboard
lifecycle, teardown):

```bash
# First time only — download a chromium that matches the installed playwright:
pnpm --dir web exec playwright install chromium

# Run (auto-starts and tears down the dashboard):
scripts/test-extension.sh

# Or as part of the full smoke:
scripts/smoke-test-full.sh
```

The raw driver can also be called directly if you already have a dashboard
running somewhere:

```bash
FF_DASHBOARD=http://localhost:4321 node browser-extensions/chrome/test-extension.mjs
```

Exit codes:
- `0` — all 12 assertions passed
- `1` — one or more assertions failed
- `2` — skipped (chromium, playwright, or dashboard not available). Set
  `FF_EXT_STRICT=1` to treat skips as failures.

Env overrides:
- `FF_CHROMIUM_BIN` — explicit path to a chromium binary
- `FF_DASHBOARD` — dashboard base URL (default `http://localhost:4321`)
- `FF_EXT_PROJECT` — project slug to use (default `grab-smoketest`, auto-created)
- `FF_EXT_URL` — grab target URL (default "Me at the zoo" 19s clip)
