#!/bin/bash
# orchestrator-stop.sh — shut down the autonomous orchestrator daemon.
#
# Sends SIGTERM (clean shutdown, requeues in-flight task). If the daemon
# doesn't exit within 10s, escalates to SIGKILL.
set -euo pipefail

PID_FILE=/tmp/claude/ff-ingest/orchestrator.pid

if [ ! -f "$PID_FILE" ]; then
    echo "no pid file at $PID_FILE — orchestrator not running via script"
    # Fallback: try to find any stray daemon
    STRAY=$(pgrep -f "ff orchestrator run" 2>/dev/null || true)
    if [ -n "$STRAY" ]; then
        echo "found stray daemon(s): $STRAY"
        echo "kill them with: kill $STRAY"
    fi
    exit 0
fi

PID=$(cat "$PID_FILE")
if ! ps -p "$PID" > /dev/null 2>&1; then
    echo "pid $PID not running — cleaning up stale pid file"
    rm -f "$PID_FILE"
    exit 0
fi

echo "sending SIGTERM to orchestrator (pid $PID)..."
kill -TERM "$PID" 2>/dev/null || true

# Wait up to 10s for clean shutdown
for i in 1 2 3 4 5 6 7 8 9 10; do
    if ! ps -p "$PID" > /dev/null 2>&1; then
        echo "stopped cleanly."
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

echo "not stopping cleanly — escalating to SIGKILL"
kill -9 "$PID" 2>/dev/null || true
sleep 1
rm -f "$PID_FILE"
echo "killed."
