# Orchestrator activation

The autonomous daemon is built, tested, and ready but Claude's safety policy blocks the Claude Code session from starting persistent autonomous processes on its own. You start it with **one** of these three commands depending on how persistent you want it.

## Easiest — use the helper scripts

```bash
./scripts/orchestrator-start.sh           # launches the daemon in background
./scripts/orchestrator-watch.sh           # live dashboard (pid, thermal, queue, log)
./scripts/orchestrator-stop.sh            # SIGTERM graceful stop; SIGKILL fallback
```

The start script picks sane thermal defaults and sets all the env vars. Use `--gentle` for tighter thresholds on a hot day, `--aggressive` when you've got headroom.

```bash
./scripts/orchestrator-start.sh --gentle       # start<3 kill>5 cool=240s
./scripts/orchestrator-start.sh --aggressive   # start<6 kill>9 cool=90s
```

---

## 1. Launchd (most persistent — survives reboot)

This is what you want if the laptop will be closed/reopened or restarted while the daemon works.

```bash
launchctl load ~/Library/LaunchAgents/com.fandomforge.orchestrator.plist
```

To stop:
```bash
launchctl unload ~/Library/LaunchAgents/com.fandomforge.orchestrator.plist
```

Log tails into `/tmp/claude/ff-ingest/orchestrator.log`.

## 2. nohup (survives terminal close, NOT reboot)

If you don't want launchd but want the daemon to keep running after you close the terminal:

```bash
FF_REFERENCES_DIR=/Users/damato/Projects/fandomforge-engine/references \
FF_YT_DLP_COOKIES_FILE=/tmp/claude/ff-ingest/yt-cookies.txt \
FF_ORCH_LOAD_START_LT=4.0 FF_ORCH_LOAD_KILL_GT=7.0 FF_ORCH_COOL_DOWN_SEC=180 \
PYTHONUNBUFFERED=1 \
nohup /Users/damato/Projects/fandomforge-engine/tools/.venv/bin/ff orchestrator run \
  --log /tmp/claude/ff-ingest/orchestrator.log > /dev/null 2>&1 & disown
```

## 3. Foreground (you watch it)

Simplest option — open a terminal tab, run this, leave it open:

```bash
cd /Users/damato/Projects/fandomforge-engine
FF_REFERENCES_DIR=$(pwd)/references \
FF_YT_DLP_COOKIES_FILE=/tmp/claude/ff-ingest/yt-cookies.txt \
FF_ORCH_LOAD_START_LT=4.0 \
./tools/.venv/bin/ff orchestrator run
```

Ctrl+C to stop (clean shutdown, requeues any in-flight task).

---

## Monitoring

```
ff orchestrator status    # queue summary + next 10 pending
ff orchestrator tail      # last 50 log lines
tail -f /tmp/claude/ff-ingest/orchestrator.log   # live stream
```

## Thermal thresholds

All three startup options respect these env vars (or the defaults in parens):

- `FF_ORCH_LOAD_START_LT` (5.0) — don't START a task if load_1m above this
- `FF_ORCH_LOAD_KILL_GT` (8.0) — KILL the running task if load_1m exceeds
- `FF_ORCH_COOL_DOWN_SEC` (120) — sleep between tasks

Laptop fans loud? Tighten: `FF_ORCH_LOAD_START_LT=3.0 FF_ORCH_LOAD_KILL_GT=5.5`.
Feeling cool and productive? Loosen: `FF_ORCH_LOAD_START_LT=6.0 FF_ORCH_LOAD_KILL_GT=10`.

## Initial queue (already seeded)

23 tasks sitting at `references/orchestrator-queue.json`:
- 17 `whisper_tag` (remaining reference corpus lyric alignment)
- 2 `render_verify` (fresh-clone action-legends, before + after code upgrades)
- 1 `rescue_playlist_search` (the missing `PL6z3...KoH4` playlist)
- 1 `dialogue_test_scaffold` (Phase 6 end-to-end smoke)
- 2 code markers (already landed, just mark done)

Sequential run time ≈ 6-20 hours depending on whisper throughput.

## Re-seed if queue drifts

```
ff orchestrator clear      # drop pending
ff orchestrator seed       # re-add the 23 standard tasks
```
