#!/usr/bin/env bash
# hermes-a2a-doctor — aggregate health check for A2A endpoints + API Servers.
# Usage:
#   bash hermes-a2a-doctor.sh [--json] [--port-map PATH]
# Port list source (in order of precedence):
#   1. --port-map <path>         — explicit CLI flag
#   2. PORT_MAP=<path> env var
#   3. ../../s6m-config/port-map.md (sibling to core/, default for the s6m monorepo layout)
#   4. Built-in 6-profile fallback (engineer/shangshu/budget/regent/default/gongbu)
set -uo pipefail
# Note: -e intentionally omitted — doctor aggregates many independent checks;
# one fail must not abort the rest. ALL_OK is the global verdict instead.

JSON_MODE=false
PORT_MAP=""
TIMEOUT=3
ALL_OK=true

# Parse CLI
while [[ $# -gt 0 ]]; do
    case "$1" in
        --json) JSON_MODE=true; shift ;;
        --port-map) PORT_MAP="$2"; shift 2 ;;
        -h|--help)
            grep '^#' "$0" | head -10
            exit 0
            ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

# Fall back through resolver chain
if [[ -z "$PORT_MAP" ]]; then
    PORT_MAP="${PORT_MAP_ENV:-${PORT_MAP:-}}"  # honor PORT_MAP env if set
fi
if [[ -z "${PORT_MAP:-}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    CANDIDATE="$SCRIPT_DIR/../../s6m-config/port-map.md"
    [[ -f "$CANDIDATE" ]] && PORT_MAP="$CANDIDATE"
fi

# Build A2A_PAIRS from port-map or fall back to baked-in list
declare -a A2A_PAIRS
if [[ -n "${PORT_MAP:-}" && -f "$PORT_MAP" ]]; then
    while IFS= read -r line; do
        # match: - **<profile>** ... 端口 `<port>` ...
        if [[ "$line" =~ ^-[[:space:]]+\*\*([a-z_]+)\*\*.*端口[[:space:]]+\`([0-9]+)\` ]]; then
            A2A_PAIRS+=("${BASH_REMATCH[1]}:${BASH_REMATCH[2]}")
        fi
    done < "$PORT_MAP"
fi

# Fallback if port-map missing or parsed nothing
if [[ ${#A2A_PAIRS[@]} -eq 0 ]]; then
    A2A_PAIRS=(
        "engineer:8718"
        "shangshu:8826"
        "budget:8936"
        "regent:8939"
        "default:8945"
        "gongbu:8898"
    )
fi

# T2: 16-profile API Server 推广.
# 端口公式：8400 + sha256("api:" + profile) % 100.
# 来源优先级:
#   1. port-map.md 内的 `- **API_<profile>** ... 端口 \`<port>\`` 行（含迁移说明）
#   2. 兜底 default:8642 / regent:8643 (T2 前历史值)
declare -a API_PAIRS
if [[ -n "${PORT_MAP:-}" && -f "$PORT_MAP" ]]; then
    while IFS= read -r line; do
        if [[ "$line" =~ ^-[[:space:]]+\*\*API_([a-z_]+)\*\*.*端口[[:space:]]+\`([0-9]+)\` ]]; then
            API_PAIRS+=("${BASH_REMATCH[1]}:${BASH_REMATCH[2]}")
        fi
    done < "$PORT_MAP"
fi
if [[ ${#API_PAIRS[@]} -eq 0 ]]; then
    API_PAIRS=(
        "default:8642"
        "regent:8643"
    )
fi

check_endpoint() {
    local port=$1 label=$2 url=$3
    local resp code
    resp=$(curl -s -m "$TIMEOUT" -w "\n%{http_code}" "http://127.0.0.1:$port$url" 2>/dev/null) || true
    code=$(echo "$resp" | tail -1)
    if [[ "$code" == "200" ]]; then
        if $JSON_MODE; then
            echo "  {\"label\":\"$label\",\"port\":$port,\"status\":\"ok\",\"code\":$code}"
        else
            echo "  ✅ $label :$port → $code"
        fi
        return 0
    else
        ALL_OK=false
        if $JSON_MODE; then
            echo "  {\"label\":\"$label\",\"port\":$port,\"status\":\"dead\",\"code\":$code}"
        else
            echo "  ❌ $label :$port → $code"
        fi
        return 1
    fi
}

check_skills() {
    local port=$1 label=$2
    local n
    n=$(curl -s -m "$TIMEOUT" "http://127.0.0.1:$port/a2a/.well-known/agent-card.json" 2>/dev/null \
        | python3 -c "import sys,json; print(len(json.load(sys.stdin)['skills']))" 2>/dev/null || echo "DEAD")
    if $JSON_MODE; then
        echo "  {\"label\":\"$label\",\"port\":$port,\"skills\":\"$n\"}"
    else
        echo "     skills: $n"
    fi
}

if $JSON_MODE; then
    echo "{"
    echo '  "timestamp":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",'
    [[ -n "${PORT_MAP:-}" ]] && echo "  \"port_map\":\"$PORT_MAP\","
    echo '  "a2a": ['
else
    echo "=== hermes-a2a-doctor @ $(date) ==="
    [[ -n "${PORT_MAP:-}" ]] && echo "port-map: $PORT_MAP"
    echo ""
    echo "--- A2A Endpoints (${#A2A_PAIRS[@]} profiles) ---"
fi

first=true
ok_a2a=0
total_a2a=0
for pair in "${A2A_PAIRS[@]}"; do
    label="${pair%%:*}"
    port="${pair##*:}"
    total_a2a=$((total_a2a + 1))
    if $JSON_MODE; then
        $first || echo ","
        first=false
    fi
    if check_endpoint "$port" "$label" "/health"; then
        ok_a2a=$((ok_a2a + 1))
    fi
    check_skills "$port" "$label"
done

if $JSON_MODE; then
    echo ""
    echo '  ],'
    echo '  "api_server": ['
else
    echo ""
    echo "--- API Server (Hermes native) ---"
fi

first=true
ok_api=0
total_api=0
for pair in "${API_PAIRS[@]}"; do
    label="${pair%%:*}"
    port="${pair##*:}"
    total_api=$((total_api + 1))
    if $JSON_MODE; then
        $first || echo ","
        first=false
    fi
    if check_endpoint "$port" "$label" "/health"; then
        ok_api=$((ok_api + 1))
    fi
done

# ─────────────────────────────────────────────────────────────────────
# Configuration & Drift Checks (§5.3) — additive, no impact on endpoint
# probes above. Each function prints one OK/FAIL line and returns 0 on
# pass, non-zero on fail. ALL_OK is flipped to false on any failure.
# ─────────────────────────────────────────────────────────────────────

CHECK_PASS=0
CHECK_FAIL=0
declare -a CHECK_JSON

_record() {
    # _record <name> <pass|fail> <message>
    local name=$1 verdict=$2 msg=$3
    if [[ "$verdict" == "pass" ]]; then
        CHECK_PASS=$((CHECK_PASS + 1))
        $JSON_MODE || echo "  ✅ $name: $msg"
    else
        CHECK_FAIL=$((CHECK_FAIL + 1))
        ALL_OK=false
        $JSON_MODE || echo "  ❌ $name: $msg"
    fi
    if $JSON_MODE; then
        local escaped
        escaped=$(printf '%s' "$msg" | sed 's/\\/\\\\/g; s/"/\\"/g')
        CHECK_JSON+=("    {\"name\":\"$name\",\"verdict\":\"$verdict\",\"message\":\"$escaped\"}")
    fi
}

# 1. All hermes-a2a/server.py processes must run the venv Python (3.11),
#    not /opt/homebrew/python3 (3.14) or the system one (3.9).
check_python_interpreter() {
    local expected="/Users/alexcai/.hermes/hermes-agent/venv/bin/python"
    local procs
    procs=$(ps -eo command | grep -E "hermes-a2a.*server\.py" | grep -v grep || true)
    if [[ -z "$procs" ]]; then
        _record "python_interpreter" "fail" "no server.py procs visible"
        return 1
    fi
    local bad
    bad=$(echo "$procs" | awk '{print $1}' | sort -u | grep -v "^${expected}" || true)
    if [[ -n "$bad" ]]; then
        _record "python_interpreter" "fail" "non-venv interpreter(s): $(echo "$bad" | tr '\n' ' ')"
        return 1
    fi
    local count
    count=$(echo "$procs" | wc -l | tr -d ' ')
    _record "python_interpreter" "pass" "$count proc(s) on venv 3.11"
}

# 2. fallback_providers in default + per-profile configs must NOT contain
#    minimax-cn / MiniMax-M2.7 self-loops.
check_fallback_chain_self_loop() {
    local bad=""
    for cfg in /Users/alexcai/.hermes/config.yaml /Users/alexcai/.hermes/profiles/*/config.yaml; do
        [[ -f "$cfg" ]] || continue
        # Look for MiniMax-M2.7 specifically inside a fallback_providers list
        local hit
        hit=$(/Users/alexcai/.hermes/hermes-agent/venv/bin/python -c "
import yaml, sys
try:
    d = yaml.safe_load(open('$cfg')) or {}
    fp = d.get('fallback_providers') or []
    if isinstance(fp, list):
        for entry in fp:
            if isinstance(entry, dict) and 'MiniMax-M2.7' in str(entry.get('model','')):
                print('hit'); break
except Exception:
    pass
" 2>/dev/null)
        if [[ "$hit" == "hit" ]]; then
            bad="$bad $cfg"
        fi
    done
    if [[ -n "$bad" ]]; then
        _record "fallback_chain_self_loop" "fail" "MiniMax-M2.7 in fallback for:$bad"
        return 1
    fi
    _record "fallback_chain_self_loop" "pass" "no self-loops in any fallback_providers"
}

# 3. Every listening port in the A2A range (8650-8950) has exactly one PID
#    bound to it. Multiple binds = duplicate server.py.
check_port_uniqueness() {
    local dup
    dup=$(lsof -iTCP -sTCP:LISTEN -nP 2>/dev/null \
        | awk '/127\.0\.0\.1:8[6-9][0-9][0-9]/{split($9,a,":"); print a[2]}' \
        | sort | uniq -c | awk '$1>1{print $2"("$1")"}' | tr '\n' ' ')
    if [[ -n "$dup" ]]; then
        _record "port_uniqueness" "fail" "duplicate listeners: $dup"
        return 1
    fi
    _record "port_uniqueness" "pass" "every A2A port has ≤1 listener"
}

# 4. plist files must NOT bake in HOME=/Users/.../.hermes/profiles/<p>/home.
#    Scanning the plists directly is faster and more deterministic than
#    `launchctl print` (which can stall for several seconds per label).
check_home_hack_leak() {
    local bad=""
    for plist in /Users/alexcai/Library/LaunchAgents/com.hermes.a2a.*.plist; do
        [[ -f "$plist" ]] || continue
        # Match an EnvironmentVariables HOME entry pointing inside a profile sandbox.
        if /usr/bin/plutil -extract EnvironmentVariables.HOME raw "$plist" 2>/dev/null \
                | grep -qE "/profiles/[^/]+/home"; then
            bad="$bad $(basename "$plist" .plist)"
        fi
    done
    if [[ -n "$bad" ]]; then
        _record "home_hack_leak" "fail" "HOME hijacked in:$bad"
        return 1
    fi
    _record "home_hack_leak" "pass" "no plist bakes in a hijacked HOME"
}

# 5. core/ and deploy/ must be byte-identical (modulo __pycache__).
check_core_deploy_drift() {
    # diff returns 1 when differences exist — must guard against pipefail.
    local diff_out
    diff_out=$( { diff -rq /Users/alexcai/code/hermes-a2a/core/ \
                            /Users/alexcai/.hermes/plugins/hermes-a2a/ 2>/dev/null \
                  || true; } | grep -v __pycache__ | head -5 || true)
    if [[ -n "$diff_out" ]]; then
        local summary
        summary=$(echo "$diff_out" | wc -l | tr -d ' ')
        _record "core_deploy_drift" "fail" "$summary diff(s); first: $(echo "$diff_out" | head -1)"
        return 1
    fi
    _record "core_deploy_drift" "pass" "core/ ≡ deploy/"
}

# 6. Lightweight key-presence check (full liveness ping is too invasive for
#    a doctor run). For each unique key_env across configs, verify env var
#    is set in the main .env file used by gateway boot.
check_provider_key_presence() {
    local env_file=/Users/alexcai/.hermes/.env
    if [[ ! -f "$env_file" ]]; then
        _record "provider_key_presence" "fail" "missing $env_file"
        return 1
    fi
    local required
    required=$(grep -hE "^\s*key_env:" /Users/alexcai/.hermes/config.yaml \
                              /Users/alexcai/.hermes/profiles/*/config.yaml 2>/dev/null \
        | awk -F': ' '{gsub(/[[:space:]]/,"",$2); print $2}' \
        | sort -u | grep -v "^$")
    local missing=""
    for var in $required; do
        if ! grep -qE "^\s*$var\s*=" "$env_file" 2>/dev/null; then
            missing="$missing $var"
        fi
    done
    if [[ -n "$missing" ]]; then
        _record "provider_key_presence" "fail" "missing in .env:$missing"
        return 1
    fi
    _record "provider_key_presence" "pass" "all referenced key_env present in .env"
}

# 7. A2A task_handler.py must NOT carry Telegram bot credentials — A2A is a
#    pure intra-host RPC plane; TG creds belong to the gateway.
check_send_message_tool_in_a2a() {
    local hits
    hits=$(grep -lE "TELEGRAM_BOT_TOKEN|telegram_chat_id|send_message.*telegram" \
        /Users/alexcai/.hermes/plugins/hermes-a2a/*.py 2>/dev/null || true)
    if [[ -n "$hits" ]]; then
        _record "send_message_tool_in_a2a" "fail" "TG creds referenced in: $(echo $hits | tr '\n' ' ')"
        return 1
    fi
    _record "send_message_tool_in_a2a" "pass" "no TG creds in A2A plane"
}

# 8. The identity prefix must vary by profile. Two things must hold:
#      a) identity.py accepts and uses a `profile` parameter (no baked-in
#         identity per profile)
#      b) task_handler.py passes os.environ.get("HERMES_PROFILE", ...)
#         into the loader
check_identity_prefix_profile_aware() {
    local id_file=/Users/alexcai/.hermes/plugins/hermes-a2a/identity.py
    local th_file=/Users/alexcai/.hermes/plugins/hermes-a2a/task_handler.py
    if [[ ! -f "$id_file" || ! -f "$th_file" ]]; then
        _record "identity_prefix_profile_aware" "fail" "identity.py or task_handler.py missing"
        return 1
    fi
    if ! grep -qE "def load_identity_prefix.*profile" "$id_file"; then
        _record "identity_prefix_profile_aware" "fail" "identity.py signature lacks profile param"
        return 1
    fi
    if ! grep -qE "load_identity_prefix\(.*profile\)" "$th_file"; then
        _record "identity_prefix_profile_aware" "fail" "task_handler.py doesn't pass profile through"
        return 1
    fi
    if ! grep -qE "HERMES_PROFILE" "$th_file"; then
        _record "identity_prefix_profile_aware" "fail" "task_handler.py never reads HERMES_PROFILE"
        return 1
    fi
    _record "identity_prefix_profile_aware" "pass" "identity flows HERMES_PROFILE → task_handler → identity.py"
}

# Check 9 — Kanban DB initialized with the 6 core tables.
# Plan: tdd-test-plan.md §1.4 (P0-1 GREEN).
check_kanban_initialized() {
    local db="${HERMES_HOME:-$HOME/.hermes}/kanban.db"
    if [[ ! -f "$db" ]]; then
        _record "kanban_initialized" "fail" "kanban.db missing at $db (run: hermes kanban init)"
        return 1
    fi
    if [[ ! -s "$db" ]]; then
        _record "kanban_initialized" "fail" "kanban.db is 0 bytes — not initialized"
        return 1
    fi
    if ! command -v sqlite3 >/dev/null 2>&1; then
        _record "kanban_initialized" "fail" "sqlite3 not installed; cannot probe schema"
        return 1
    fi
    local required="tasks task_links task_comments task_events task_runs kanban_notify_subs"
    local missing=""
    for t in $required; do
        if ! sqlite3 "$db" "SELECT name FROM sqlite_master WHERE type='table' AND name='$t'" 2>/dev/null | grep -q "^$t$"; then
            missing+="$t "
        fi
    done
    if [[ -n "$missing" ]]; then
        _record "kanban_initialized" "fail" "missing tables: $missing"
        return 1
    fi
    local n_tasks
    n_tasks=$(sqlite3 "$db" "SELECT COUNT(*) FROM tasks" 2>/dev/null || echo 0)
    _record "kanban_initialized" "pass" "6 tables present, ${n_tasks} tasks"
}

# Check 10 — Dispatcher daemon alive.
# v0.15.x deprecation note: standalone daemon requires --force; gateway
# embeds a dispatcher too. We accept either signal.
# Plan: tdd-test-plan.md §1.4 (P0-1 GREEN), tdd-plan-review.md §2.3.
check_dispatcher_running() {
    local pid_standalone="${HERMES_HOME:-$HOME/.hermes}/dispatcher.pid"
    local pid_gateway="${HERMES_HOME:-$HOME/.hermes}/gateway.pid"
    local standalone_pid="" gateway_pid="" reason=""

    if [[ -f "$pid_standalone" ]]; then
        standalone_pid=$(tr -d '[:space:]' < "$pid_standalone" || true)
        if [[ -n "$standalone_pid" ]] && kill -0 "$standalone_pid" 2>/dev/null; then
            reason="standalone daemon pid=$standalone_pid"
        else
            standalone_pid=""
        fi
    fi
    if [[ -f "$pid_gateway" ]]; then
        gateway_pid=$(tr -d '[:space:]' < "$pid_gateway" || true)
        if [[ -n "$gateway_pid" ]] && kill -0 "$gateway_pid" 2>/dev/null; then
            reason="${reason:+$reason; }gateway pid=$gateway_pid (embedded dispatcher)"
        else
            gateway_pid=""
        fi
    fi

    # pgrep fallback if no pidfile (handles --force standalone without --pidfile)
    if [[ -z "$standalone_pid" && -z "$gateway_pid" ]]; then
        if pgrep -f "hermes kanban daemon" >/dev/null 2>&1; then
            reason="hermes kanban daemon found via pgrep (no pidfile)"
        elif pgrep -f "hermes.*gateway.*(run|start)" >/dev/null 2>&1; then
            reason="hermes gateway running (embedded dispatcher)"
        fi
    fi

    if [[ -z "$reason" ]]; then
        _record "dispatcher_running" "fail" "no dispatcher: start with 'bash s6m-config/scripts/start-dispatcher.sh' or 'hermes gateway start'"
        return 1
    fi
    _record "dispatcher_running" "pass" "$reason"
}

if $JSON_MODE; then
    echo ""
    echo "  ],"
    echo "  \"checks\": ["
else
    echo ""
    echo "--- Configuration & Drift Checks ---"
fi

check_python_interpreter
check_fallback_chain_self_loop
check_port_uniqueness
check_home_hack_leak
check_core_deploy_drift
check_provider_key_presence
check_send_message_tool_in_a2a
check_identity_prefix_profile_aware
check_kanban_initialized
check_dispatcher_running

if $JSON_MODE; then
    # Join CHECK_JSON entries with commas (no `local` outside a function).
    for i in "${!CHECK_JSON[@]}"; do
        if [[ $i -lt $((${#CHECK_JSON[@]} - 1)) ]]; then
            echo "${CHECK_JSON[$i]},"
        else
            echo "${CHECK_JSON[$i]}"
        fi
    done
    echo "  ],"
    echo "  \"a2a_summary\": \"$ok_a2a/$total_a2a\","
    echo "  \"api_summary\": \"$ok_api/$total_api\","
    echo "  \"checks_summary\": \"$CHECK_PASS pass / $CHECK_FAIL fail\","
    echo "  \"all_ok\": $ALL_OK"
    echo "}"
else
    echo ""
    echo "A2A:    $ok_a2a/$total_a2a healthy"
    echo "API:    $ok_api/$total_api healthy"
    echo "Checks: $CHECK_PASS pass / $CHECK_FAIL fail"
    if $ALL_OK; then
        echo "✅ ALL HEALTHY"
    else
        echo "❌ ISSUES FOUND — investigate above"
    fi
fi

$ALL_OK
