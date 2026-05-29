#!/usr/bin/env bash
# P0-1 doctor E2E — check 9 / check 10 work end-to-end in isolation.
#
# Flow:
#   isolated HERMES_HOME → kanban init → dispatcher daemon →
#   run doctor.sh → grep for the two new checks → assert both pass.
#
# We use the human-readable output (not --json) because doctor's JSON
# emitter has known formatting drift; the line `✅ kanban_initialized:`
# and `✅ dispatcher_running:` is the stable contract.
#
# Plan: s6m-config/docs/tdd-test-plan.md §1.2.3 E4 (v1.1).
set -uo pipefail

THIS_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_SCRIPT/../.." && pwd)"
DOCTOR="$REPO_ROOT/core/scripts/hermes-a2a-doctor.sh"
[[ -f "$DOCTOR" ]] || { echo "❌ doctor.sh missing at $DOCTOR" >&2; exit 1; }

TMP_ROOT=$(mktemp -d -t hermes-doctor-e2e-XXXXXX)
TMP_HOME="$TMP_ROOT/parent"
mkdir -p "$TMP_HOME"
ISOLATED_HERMES_HOME="$TMP_HOME/hermes"
mkdir -p "$ISOLATED_HERMES_HOME/profiles"

DAEMON_PID=""
EXIT_CODE=1

cleanup() {
    local rc=$EXIT_CODE
    if [[ -n "$DAEMON_PID" ]] && kill -0 "$DAEMON_PID" 2>/dev/null; then
        kill -TERM "$DAEMON_PID" 2>/dev/null || true
        for _ in 1 2 3 4 5; do
            kill -0 "$DAEMON_PID" 2>/dev/null || break
            sleep 1
        done
        kill -0 "$DAEMON_PID" 2>/dev/null && kill -KILL "$DAEMON_PID" 2>/dev/null || true
    fi
    rm -rf "$TMP_ROOT"
    if [[ $rc -eq 0 ]]; then
        echo ""
        echo "✅ P0-1 doctor E2E PASSED (checks 9 + 10)"
    else
        echo ""
        echo "❌ P0-1 doctor E2E FAILED (rc=$rc)"
    fi
    exit "$rc"
}
trap cleanup EXIT INT TERM

fail() { echo "❌ $*" >&2; exit 1; }

# ── Seed isolated home with profile symlinks ────────────────
echo "→ seeding isolated HERMES_HOME at $ISOLATED_HERMES_HOME"
if [[ -d "$HOME/.hermes/profiles" ]]; then
    for prof in "$HOME"/.hermes/profiles/*/; do
        name=$(basename "$prof")
        ln -sf "$prof" "$ISOLATED_HERMES_HOME/profiles/$name"
    done
fi
[[ -f "$HOME/.hermes/.a2a-token" ]] && \
    cp "$HOME/.hermes/.a2a-token" "$ISOLATED_HERMES_HOME/.a2a-token"

# ── Pre-flight: check 9 must fail before init ───────────────
echo "→ pre-init doctor — check_kanban_initialized should FAIL"
pre_out=$(HERMES_HOME="$ISOLATED_HERMES_HOME" HOME="$TMP_HOME" \
    bash "$DOCTOR" 2>&1 | grep -E "kanban_initialized" || true)
if ! echo "$pre_out" | grep -q "❌ kanban_initialized"; then
    fail "pre-init check_kanban_initialized should fail, got: $pre_out"
fi
echo "  ✓ pre-init fail confirmed: $pre_out"

# ── Init + daemon ───────────────────────────────────────────
echo "→ hermes kanban init"
HERMES_HOME="$ISOLATED_HERMES_HOME" HOME="$TMP_HOME" \
    hermes kanban init >/dev/null 2>&1 || fail "kanban init failed"

echo "→ starting dispatcher daemon"
pidfile="$ISOLATED_HERMES_HOME/dispatcher.pid"
HERMES_HOME="$ISOLATED_HERMES_HOME" HOME="$TMP_HOME" \
    hermes kanban daemon --force --pidfile "$pidfile" --interval 3600 \
    >"$ISOLATED_HERMES_HOME/dispatcher.log" 2>&1 &
DAEMON_PID=$!

for _ in 1 2 3 4 5 6 7 8; do
    [[ -f "$pidfile" ]] && break
    sleep 1
done
[[ -f "$pidfile" ]] || fail "pidfile missing after 8s"

# ── Doctor — both checks must now pass ──────────────────────
echo "→ doctor.sh — expecting check_kanban_initialized ✅ + check_dispatcher_running ✅"
doctor_out=$(HERMES_HOME="$ISOLATED_HERMES_HOME" HOME="$TMP_HOME" \
    bash "$DOCTOR" 2>&1)

# check 9
if echo "$doctor_out" | grep -qE "✅ kanban_initialized:.*6 tables"; then
    line=$(echo "$doctor_out" | grep -E "kanban_initialized:")
    echo "  ✓ $line"
else
    echo "$doctor_out" | grep -E "kanban_initialized" >&2 || true
    fail "check_kanban_initialized did not pass"
fi

# check 10
if echo "$doctor_out" | grep -qE "✅ dispatcher_running:.*(standalone|pid=|daemon|gateway)"; then
    line=$(echo "$doctor_out" | grep -E "dispatcher_running:")
    echo "  ✓ $line"
else
    echo "$doctor_out" | grep -E "dispatcher_running" >&2 || true
    fail "check_dispatcher_running did not pass"
fi

EXIT_CODE=0
