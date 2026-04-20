#!/bin/bash
# orchestrator-watch.sh — live dashboard view of the orchestrator.
#
# Every 5 seconds: clears screen, prints
#   - daemon status (pid + alive)
#   - PC thermal state (load avg + zone)
#   - queue summary (pending / running / done / failed)
#   - currently-running task
#   - recently-completed tasks (last 5)
#   - last 15 log lines
#
# Ctrl+C to exit (doesn't stop the daemon — use orchestrator-stop.sh for that).
set -euo pipefail

REPO=/Users/damato/Projects/fandomforge-engine
VENV_FF="$REPO/tools/.venv/bin/ff"
QUEUE="$REPO/references/orchestrator-queue.json"
LOG=/tmp/claude/ff-ingest/orchestrator.log
PID_FILE=/tmp/claude/ff-ingest/orchestrator.pid

# Colors (disabled if stdout is piped)
if [ -t 1 ]; then
    RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; BLU=$'\e[34m'; BOLD=$'\e[1m'; OFF=$'\e[0m'
else
    RED=; GRN=; YEL=; BLU=; BOLD=; OFF=
fi

trap 'echo; echo "watch stopped (daemon still running — use orchestrator-stop.sh to stop)"; exit 0' INT

while true; do
    clear
    echo "${BOLD}=== FandomForge orchestrator — live dashboard ===${OFF}"
    echo "$(date '+%Y-%m-%d %H:%M:%S')  (refresh every 5s, ctrl+c to exit)"
    echo

    # Daemon status
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            echo "${BOLD}daemon:${OFF} ${GRN}running${OFF} (pid $PID)"
        else
            echo "${BOLD}daemon:${OFF} ${RED}DEAD${OFF} (pid $PID stale — run orchestrator-start.sh)"
        fi
    else
        echo "${BOLD}daemon:${OFF} ${YEL}not started via script${OFF} (run orchestrator-start.sh)"
    fi

    # Thermal state
    LOAD_LINE=$(uptime)
    LOAD_1M=$(echo "$LOAD_LINE" | awk -F'load averages?:' '{print $2}' | awk '{print $1}' | tr -d ',')
    LOAD_5M=$(echo "$LOAD_LINE" | awk -F'load averages?:' '{print $2}' | awk '{print $2}' | tr -d ',')
    # Parse gate thresholds out of the daemon's env if we can — else use defaults
    START_LT=${FF_ORCH_LOAD_START_LT:-4.0}
    KILL_GT=${FF_ORCH_LOAD_KILL_GT:-7.0}
    ZONE="cool"; ZONE_COLOR="$GRN"
    if (( $(echo "$LOAD_1M > $KILL_GT" | bc -l 2>/dev/null || echo 0) )); then
        ZONE="EMERGENCY"; ZONE_COLOR="$RED"
    elif (( $(echo "$LOAD_1M > $START_LT" | bc -l 2>/dev/null || echo 0) )); then
        ZONE="throttle"; ZONE_COLOR="$YEL"
    fi
    echo "${BOLD}thermal:${OFF} ${ZONE_COLOR}${ZONE}${OFF}   load_1m=$LOAD_1M  load_5m=$LOAD_5M  (start<$START_LT kill>$KILL_GT)"

    # Queue summary (via CLI)
    echo
    echo "${BOLD}queue:${OFF}"
    FF_REFERENCES_DIR="$REPO/references" "$VENV_FF" orchestrator status 2>&1 \
        | sed -e 's/^/  /' \
        | head -25

    # Recent log lines
    echo
    echo "${BOLD}recent log:${OFF}"
    if [ -f "$LOG" ]; then
        tail -15 "$LOG" | sed 's/^/  /'
    else
        echo "  ${YEL}(no log yet)${OFF}"
    fi

    sleep 5
done
