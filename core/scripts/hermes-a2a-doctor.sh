#!/usr/bin/env bash
# hermes-a2a-doctor — aggregate health check for A2A endpoints + API Servers.
# Usage:
#   bash hermes-a2a-doctor.sh [--json] [--port-map PATH]
# Port list source (in order of precedence):
#   1. --port-map <path>         — explicit CLI flag
#   2. PORT_MAP=<path> env var
#   3. ../../s6m-config/port-map.md (sibling to core/, default for the s6m monorepo layout)
#   4. Built-in 6-profile fallback (engineer/shangshu/budget/regent/default/gongbu)
set -euo pipefail

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

API_PAIRS=(
    "default:8642"
    "regent:8643"
)

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

if $JSON_MODE; then
    echo ""
    echo "  ],"
    echo "  \"a2a_summary\": \"$ok_a2a/$total_a2a\","
    echo "  \"api_summary\": \"$ok_api/$total_api\","
    echo "  \"all_ok\": $ALL_OK"
    echo "}"
else
    echo ""
    echo "A2A: $ok_a2a/$total_a2a healthy"
    echo "API: $ok_api/$total_api healthy"
    if $ALL_OK; then
        echo "✅ ALL HEALTHY"
    else
        echo "❌ SOME DEAD — investigate!"
    fi
fi

$ALL_OK
