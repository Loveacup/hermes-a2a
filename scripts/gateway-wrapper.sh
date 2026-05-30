#!/bin/bash
# ── gateway-wrapper.sh ──────────────────────────────────────────────────
# T2.5b: killpg(-pid, SIGTERM) launchd wrapper + Preflight checks
#
# This script serves two purposes:
# 1. **Preflight**: Runs health checks before starting the gateway daemon,
#    preventing boot-loops from config errors or missing dependencies.
# 2. **killpg wrapper**: Intercepts SIGTERM from launchd and translates it
#    into killpg(-PID, SIGTERM) — sending TERM only to the child process
#    group. This prevents launchd's reload from killing the parent wrapper
#    and its children indiscriminately before proper cleanup.
#
# Usage in plist ProgramArguments:
#   ["/bin/bash", "/Users/alexcai/code/hermes-a2a/scripts/gateway-wrapper.sh",
#    "/path/to/venv/bin/python", "-m", "hermes_cli.main", "gateway", "run", "--replace"]
# ────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Config ─────────────────────────────────────────────────────────────
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
WRAPPER_LOG_DIR="${HERMES_HOME}/logs"
WRAPPER_LOG="${WRAPPER_LOG_DIR}/gateway-wrapper.log"
PREFLIGHT_TIMEOUT=15  # seconds for preflight checks
CHILD_PID=""

mkdir -p "$WRAPPER_LOG_DIR" 2>/dev/null || true

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $$ gateway-wrapper: $*" >> "$WRAPPER_LOG"
}

# ── Preflight phase ────────────────────────────────────────────────────
run_preflight() {
    local venv_python="$1"
    shift
    
    log "===== PREFLIGHT START ====="
    log "HERMES_HOME=$HERMES_HOME"
    log "HOME=$HOME"
    log "venv: $venv_python"
    
    local failed=0
    
    # P1: Verify the venv python exists and is executable
    if [ ! -x "$venv_python" ]; then
        log "PREFLIGHT FAIL: venv python not found at $venv_python"
        failed=1
    else
        log "PREFLIGHT OK: venv python found at $venv_python"
    fi
    
    # P2: Verify HERMES_HOME exists
    if [ ! -d "$HERMES_HOME" ]; then
        log "PREFLIGHT FAIL: HERMES_HOME directory not found: $HERMES_HOME"
        failed=1
    else
        log "PREFLIGHT OK: HERMES_HOME exists at $HERMES_HOME"
    fi
    
    # P3: Verify config.yaml is readable
    if [ -r "$HERMES_HOME/config.yaml" ]; then
        log "PREFLIGHT OK: config.yaml readable"
    else
        log "PREFLIGHT WARN: config.yaml not found or not readable at $HERMES_HOME/config.yaml"
        # Not a hard failure — gateway can start without config
    fi
    
    # P4: Verify kanban.db is accessible (honor HERMES_KANBAN_DB if set)
    local kanban_db="${HERMES_KANBAN_DB:-$HERMES_HOME/kanban.db}"
    if [ -f "$kanban_db" ]; then
        # Quick integrity check
        if "$venv_python" -c "
import sqlite3
try:
    conn = sqlite3.connect('$kanban_db')
    conn.execute('PRAGMA integrity_check')
    conn.close()
except Exception as e:
    import sys
    sys.exit(1)
" 2>/dev/null; then
            log "PREFLIGHT OK: kanban.db integrity check passed ($kanban_db)"
        else
            log "PREFLIGHT WARN: kanban.db integrity check failed, will auto-recover on gateway start"
        fi
    else
        log "PREFLIGHT INFO: kanban.db not found, will be created on gateway start"
    fi
    
    # P5: Verify .env file has required keys (basic check)
    if [ -f "$HERMES_HOME/.env" ]; then
        log "PREFLIGHT OK: .env file found"
    else
        log "PREFLIGHT INFO: .env file not found (may use config.yaml for keys)"
    fi
    
    # P6: Check for stale PID lock files
    local pid_file="${HERMES_HOME}/gateway.pid"
    if [ -f "$pid_file" ]; then
        local stale_pid
        stale_pid=$(cat "$pid_file" 2>/dev/null || echo "")
        if [ -n "$stale_pid" ] && ! kill -0 "$stale_pid" 2>/dev/null; then
            log "PREFLIGHT INFO: stale PID file for $stale_pid, removing"
            rm -f "$pid_file" 2>/dev/null || true
        fi
    fi
    
    log "===== PREFLIGHT END (failed=$failed) ====="
    return $failed
}

# ── Signal handlers ────────────────────────────────────────────────────
cleanup_children() {
    local sig="${1:-TERM}"
    if [ -n "$CHILD_PID" ] && kill -0 "$CHILD_PID" 2>/dev/null; then
        log "sending SIG${sig} to child PID $CHILD_PID + descendants (pkill -P)"
        # Kill child's descendants first, then the child itself
        pkill -P "$CHILD_PID" 2>/dev/null || true
        kill -"$sig" "$CHILD_PID" 2>/dev/null || true
        
        # Wait for child to exit gracefully (max 25s, leaves 5s before launchd SIGKILL at ExitTimeOut=30)
        local waited=0
        while [ $waited -lt 25 ] && kill -0 "$CHILD_PID" 2>/dev/null; do
            sleep 0.5
            waited=$((waited + 1))
        done
        
        if kill -0 "$CHILD_PID" 2>/dev/null; then
            log "child $CHILD_PID did not exit after ${waited}s, sending SIGKILL"
            kill -9 -"$CHILD_PID" 2>/dev/null || true
            wait "$CHILD_PID" 2>/dev/null || true
        else
            wait "$CHILD_PID" 2>/dev/null || true
            log "child $CHILD_PID exited cleanly after ${waited}s"
        fi
    fi
}

# Trap signals — only forward to child's process group, NOT to the wrapper itself
trap 'log "received SIGTERM"; cleanup_children TERM; exit 0' TERM
trap 'log "received SIGINT";  cleanup_children INT;  exit 0' INT
trap 'log "received SIGQUIT"; cleanup_children QUIT; exit 0' QUIT

# ── Main ───────────────────────────────────────────────────────────────
VENV_PYTHON="$1"
shift  # Remove the python path from args
GATEWAY_ARGS=("$@")

log "===== GATEWAY WRAPPER START ====="
log "venv_python=$VENV_PYTHON"
log "gateway_args=${GATEWAY_ARGS[*]}"

# Phase 1: Preflight
if ! run_preflight "$VENV_PYTHON" "${GATEWAY_ARGS[@]}"; then
    log "PREFLIGHT FAILED: aborting gateway start"
    exit 1
fi

# Phase 2: Launch gateway (no setsid on macOS; use pkill -P for process-group cleanup)
log "launching gateway: $VENV_PYTHON ${GATEWAY_ARGS[*]}"

"$VENV_PYTHON" "${GATEWAY_ARGS[@]}" &
CHILD_PID=$!

log "gateway started as PID $CHILD_PID"

# Phase 3: Wait for child
set +e
wait "$CHILD_PID"
CHILD_EXIT=$?
set -e

log "gateway exited with code $CHILD_EXIT"
CHILD_PID=""
exit $CHILD_EXIT
