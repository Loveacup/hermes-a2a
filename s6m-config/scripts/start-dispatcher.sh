#!/usr/bin/env bash
# start-dispatcher.sh — one-shot kanban init + dispatcher daemon launcher.
#
# Why this script:
#   v0.15.x deprecates `hermes kanban daemon` in favor of the gateway-embedded
#   dispatcher. We still prefer the standalone daemon for s6m deployments
#   because `hermes gateway start` would also pull up the messaging gateway
#   (Telegram/Discord/WhatsApp), which has different lifecycle and credentials.
#   Using --force keeps the standalone daemon usable.
#
# Plan: tdd-test-plan.md §1.4 (P0-1 GREEN).
# Behavior:
#   - Idempotent: re-running re-uses the existing kanban.db; safe to call from
#     cron / launchd.
#   - Honors HERMES_HOME and HOME (matches conftest fixture contract).
#   - Writes pidfile to $HERMES_HOME/dispatcher.pid for doctor.sh check 10.
#
# Usage:
#   bash start-dispatcher.sh [--interval SECONDS] [--foreground]
set -euo pipefail

INTERVAL=3600         # default: rarely-tick standalone, matches conftest fixture
FOREGROUND=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --interval)   INTERVAL="$2"; shift 2 ;;
        --foreground) FOREGROUND=true; shift ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "unknown arg: $1" >&2
            exit 2
            ;;
    esac
done

: "${HERMES_HOME:=$HOME/.hermes}"
: "${HOME:=/Users/$USER}"
PIDFILE="$HERMES_HOME/dispatcher.pid"
LOGFILE="$HERMES_HOME/dispatcher.log"

hermes_bin="$(command -v hermes || true)"
[[ -z "$hermes_bin" ]] && { echo "hermes CLI not on PATH" >&2; exit 3; }

echo "→ Ensuring kanban.db is initialized at $HERMES_HOME ..."
HERMES_HOME="$HERMES_HOME" HOME="$HOME" "$hermes_bin" kanban init

# If a daemon is already running for this pidfile, do not double-start
if [[ -f "$PIDFILE" ]]; then
    existing=$(tr -d '[:space:]' < "$PIDFILE" || true)
    if [[ -n "$existing" ]] && kill -0 "$existing" 2>/dev/null; then
        echo "✓ Dispatcher already running (pid=$existing). Nothing to do."
        exit 0
    fi
    rm -f "$PIDFILE"
fi

CMD=(
    "$hermes_bin" kanban daemon --force
    --pidfile "$PIDFILE"
    --interval "$INTERVAL"
    --verbose
)

if $FOREGROUND; then
    echo "→ Starting dispatcher (foreground, interval=${INTERVAL}s, --force)"
    exec env HERMES_HOME="$HERMES_HOME" HOME="$HOME" "${CMD[@]}"
fi

echo "→ Starting dispatcher (background, interval=${INTERVAL}s, --force)"
nohup env HERMES_HOME="$HERMES_HOME" HOME="$HOME" \
      "${CMD[@]}" >> "$LOGFILE" 2>&1 &
disown $! || true

# Give it 5s to write pidfile, then verify
for _ in 1 2 3 4 5; do
    if [[ -f "$PIDFILE" ]]; then
        pid=$(tr -d '[:space:]' < "$PIDFILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "✓ Dispatcher started (pid=$pid, pidfile=$PIDFILE)"
            echo "  Logs: tail -F $LOGFILE"
            exit 0
        fi
    fi
    sleep 1
done

echo "✗ Dispatcher failed to write pidfile within 5s. Check $LOGFILE" >&2
exit 4
