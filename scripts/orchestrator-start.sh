#!/bin/bash
# orchestrator-start.sh — launch the autonomous orchestrator daemon
#
# Starts the daemon in the background with sensible thermal thresholds.
# Survives terminal close. Does NOT survive reboot (use launchctl for that).
#
# Usage:
#   ./scripts/orchestrator-start.sh                 # default thresholds
#   ./scripts/orchestrator-start.sh --gentle        # extra-careful: start<3, kill>5
#   ./scripts/orchestrator-start.sh --aggressive    # loose: start<6, kill>9
#
set -euo pipefail

REPO=/Users/damato/Projects/fandomforge-engine
VENV_FF="$REPO/tools/.venv/bin/ff"
LOG=/tmp/claude/ff-ingest/orchestrator.log
PID_FILE=/tmp/claude/ff-ingest/orchestrator.pid
COOKIES=/tmp/claude/ff-ingest/yt-cookies.txt

# Thresholds — tune with --gentle / --aggressive
LOAD_START_LT=4.0
LOAD_KILL_GT=7.0
COOL_DOWN=180

while [ "${1:-}" ]; do
    case "$1" in
        --gentle)     LOAD_START_LT=3.0; LOAD_KILL_GT=5.0; COOL_DOWN=240 ;;
        --aggressive) LOAD_START_LT=6.0; LOAD_KILL_GT=9.0; COOL_DOWN=90 ;;
        --help|-h)    sed -n '/^# /,/^$/p' "$0"; exit 0 ;;
        *) echo "unknown flag: $1"; exit 1 ;;
    esac
    shift
done

# Already running?
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "orchestrator already running (pid $PID)"
        echo "  stop with: ./scripts/orchestrator-stop.sh"
        echo "  watch with: ./scripts/orchestrator-watch.sh"
        exit 0
    else
        rm -f "$PID_FILE"
    fi
fi

mkdir -p "$(dirname "$LOG")"

echo "launching orchestrator daemon..."
echo "  thresholds: start<$LOAD_START_LT  kill>$LOAD_KILL_GT  cool-down=${COOL_DOWN}s"

# nohup + disown so the daemon survives this script exiting
FF_REFERENCES_DIR="$REPO/references" \
FF_YT_DLP_COOKIES_FILE="$COOKIES" \
FF_ORCH_LOAD_START_LT="$LOAD_START_LT" \
FF_ORCH_LOAD_KILL_GT="$LOAD_KILL_GT" \
FF_ORCH_COOL_DOWN_SEC="$COOL_DOWN" \
PATH="$REPO/tools/.venv/bin:$PATH" \
PYTHONUNBUFFERED=1 \
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 TORCH_NUM_THREADS=2 NUMEXPR_NUM_THREADS=2 \
nohup "$VENV_FF" orchestrator run --log "$LOG" >> "$LOG" 2>&1 &

DAEMON_PID=$!
echo "$DAEMON_PID" > "$PID_FILE"
disown
sleep 2

if ps -p "$DAEMON_PID" > /dev/null 2>&1; then
    echo "  started (pid $DAEMON_PID)"
    echo "  log:    $LOG"
    echo "  status: ff orchestrator status"
    echo "  watch:  ./scripts/orchestrator-watch.sh"
    echo "  stop:   ./scripts/orchestrator-stop.sh"
else
    echo "ERROR: daemon died immediately. Check the log:"
    tail -20 "$LOG"
    rm -f "$PID_FILE"
    exit 1
fi
