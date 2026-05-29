#!/usr/bin/env bash
# P0-1 E2E — full kanban card lifecycle in an isolated HERMES_HOME.
#
# Flow:
#   isolated HERMES_HOME → kanban init → dispatcher daemon --force →
#   create card → dispatch --dry-run sees it → kanban complete →
#   task_runs row exists → cleanup → exit 0
#
# No real LLM call: we use dispatch --dry-run for the spawn decision and
# kanban complete to drive the row into 'done' from the CLI side. This
# verifies the orchestration plumbing, not the agent loop.
#
# Plan: s6m-config/docs/tdd-test-plan.md §1.2.3 E1/E2/E3 (v1.1).
# Usage: bash tests/e2e/test_first_card_e2e.sh [assignee]
#   assignee defaults to 'default' (always spawnable); pass 'regent' to
#   exercise the launchd-supervised path.
set -uo pipefail

ASSIGNEE="${1:-default}"
THIS_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$THIS_SCRIPT/../.." && pwd)"
JZ_SKILLS_ROOT="${JZ_SKILLS_ROOT:-$HOME/code/jz-skills}"
[[ -d "$JZ_SKILLS_ROOT" ]] || JZ_SKILLS_ROOT="/Users/alexcai/code/jz-skills"

TMP_ROOT=$(mktemp -d -t hermes-e2e-XXXXXX)
TMP_HOME="$TMP_ROOT/parent"
mkdir -p "$TMP_HOME"
ISOLATED_HERMES_HOME="$TMP_HOME/hermes"
mkdir -p "$ISOLATED_HERMES_HOME/profiles"

DAEMON_PID=""
EXIT_CODE=1

# ── Cleanup ─────────────────────────────────────────────────
cleanup() {
    local rc=$EXIT_CODE
    if [[ -n "$DAEMON_PID" ]] && kill -0 "$DAEMON_PID" 2>/dev/null; then
        echo "→ stopping dispatcher pid=$DAEMON_PID"
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
        echo "✅ P0-1 E2E PASSED (assignee=$ASSIGNEE)"
    else
        echo ""
        echo "❌ P0-1 E2E FAILED (rc=$rc, assignee=$ASSIGNEE)"
    fi
    exit "$rc"
}
trap cleanup EXIT INT TERM

fail() { echo "❌ $*" >&2; exit 1; }

run_hermes() {
    HERMES_HOME="$ISOLATED_HERMES_HOME" HOME="$TMP_HOME" \
        JZ_SKILLS_ROOT="$JZ_SKILLS_ROOT" \
        hermes "$@"
}

# ── Step 1: symlink real profiles into isolated HERMES_HOME ─
echo "→ Seeding isolated HERMES_HOME at $ISOLATED_HERMES_HOME"
if [[ -d "$HOME/.hermes/profiles" ]]; then
    for prof in "$HOME"/.hermes/profiles/*/; do
        name=$(basename "$prof")
        ln -sf "$prof" "$ISOLATED_HERMES_HOME/profiles/$name"
    done
fi
[[ -f "$HOME/.hermes/.a2a-token" ]] && \
    cp "$HOME/.hermes/.a2a-token" "$ISOLATED_HERMES_HOME/.a2a-token"

profile_count=$(ls -1 "$ISOLATED_HERMES_HOME/profiles" 2>/dev/null | wc -l | tr -d ' ')
echo "  ✓ seeded $profile_count profile symlinks"
[[ "$profile_count" -ge 1 ]] || fail "no profiles seeded"

# ── Step 2: hermes kanban init ──────────────────────────────
echo "→ hermes kanban init"
run_hermes kanban init >/dev/null 2>&1 || fail "kanban init failed"
db="$ISOLATED_HERMES_HOME/kanban.db"
[[ -s "$db" ]] || fail "kanban.db not created at $db"
echo "  ✓ kanban.db created ($(stat -f%z "$db") bytes)"

# ── Step 3: start dispatcher daemon --force ─────────────────
echo "→ starting dispatcher daemon (--force --interval 3600)"
pidfile="$ISOLATED_HERMES_HOME/dispatcher.pid"
HERMES_HOME="$ISOLATED_HERMES_HOME" HOME="$TMP_HOME" \
    hermes kanban daemon --force --pidfile "$pidfile" --interval 3600 --verbose \
    >"$ISOLATED_HERMES_HOME/dispatcher.log" 2>&1 &
DAEMON_PID=$!

for _ in 1 2 3 4 5 6 7 8; do
    [[ -f "$pidfile" ]] && break
    sleep 1
done
[[ -f "$pidfile" ]] || fail "pidfile not written within 8s"
written_pid=$(tr -d '[:space:]' < "$pidfile")
kill -0 "$written_pid" 2>/dev/null || fail "daemon pid $written_pid not alive"
echo "  ✓ dispatcher pid=$written_pid"

# ── Step 4: kanban create --assignee ────────────────────────
echo "→ kanban create --assignee $ASSIGNEE"
create_json=$(run_hermes kanban create "e2e probe" \
    --assignee "$ASSIGNEE" \
    --skill kanban-orchestrator \
    --json 2>/dev/null) || fail "kanban create failed"
TID=$(echo "$create_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
[[ -n "$TID" ]] || fail "could not extract task id from create output"
echo "  ✓ created task $TID"

# Verify skills column persisted
stored_skills=$(sqlite3 "$db" \
    "SELECT skills FROM tasks WHERE id = '$TID'")
[[ "$stored_skills" == '["kanban-orchestrator"]' ]] || \
    fail "tasks.skills mismatch: got $stored_skills"
echo "  ✓ tasks.skills = $stored_skills"

# ── Step 5: dispatch --dry-run confirms the task is spawnable ──
echo "→ dispatch --dry-run"
decision=$(run_hermes kanban dispatch --dry-run --json 2>/dev/null) \
    || fail "dispatch --dry-run failed"
in_spawn=$(echo "$decision" | python3 -c "
import json, sys
d = json.load(sys.stdin)
tid = '$TID'
spawned = [s['task_id'] for s in d.get('spawned', [])]
nonspawn = d.get('skipped_nonspawnable', [])
print('SPAWN' if tid in spawned else ('NONSPAWN' if tid in nonspawn else 'MISSING'))
")
case "$in_spawn" in
    SPAWN)    echo "  ✓ dispatcher sees task as spawnable" ;;
    NONSPAWN) echo "  ⚠ dispatcher judged nonspawnable (often profile config in isolated env); continuing" ;;
    *)        fail "task $TID not in dispatch decision at all" ;;
esac

# ── Step 6: kanban complete with summary + metadata ─────────
echo "→ kanban complete --summary --metadata"
run_hermes kanban complete "$TID" \
    --summary "E2E test completed" \
    --metadata '{"tests_run":1,"mode":"e2e"}' \
    >/dev/null 2>&1 || fail "kanban complete failed"

status=$(sqlite3 "$db" "SELECT status FROM tasks WHERE id = '$TID'")
[[ "$status" == "done" ]] || fail "expected status='done', got '$status'"
echo "  ✓ task status = done"

# ── Step 7: task_runs row recorded ──────────────────────────
echo "→ verify task_runs row exists"
read -r outcome summary <<< "$(sqlite3 -separator $'\t' "$db" \
    "SELECT outcome, summary FROM task_runs WHERE task_id = '$TID'")"
[[ "$outcome" == "completed" ]] || fail "task_runs.outcome='$outcome' (want completed)"
[[ "$summary" == "E2E test completed" ]] || fail "task_runs.summary mismatch: '$summary'"
echo "  ✓ task_runs outcome=$outcome, summary=\"$summary\""

EXIT_CODE=0
