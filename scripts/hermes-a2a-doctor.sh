#!/usr/bin/env bash
# hermes-a2a-doctor — aggregate health check for all A2A + API Server endpoints
# Usage: bash hermes-a2a-doctor.sh [--json]

set -euo pipefail

A2A_PORTS=(8668 8676 8686 8689 8695 8698)
API_PORTS=(8642 8643)
TIMEOUT=3
ALL_OK=true
JSON_MODE=false
[[ "${1:-}" == "--json" ]] && JSON_MODE=true

check_endpoint() {
    local port=$1 label=$2 url=$3
    local resp code
    resp=$(curl -s -m "$TIMEOUT" -w "\n%{http_code}" "http://127.0.0.1:$port$url" 2>/dev/null) || true
    code=$(echo "$resp" | tail -1)
    body=$(echo "$resp" | sed '$d')

    if [[ "$code" == "200" ]]; then
        if $JSON_MODE; then
            echo "  {\"label\":\"$label\",\"port\":$port,\"status\":\"ok\",\"code\":$code}"
        else
            echo "  ✅ $label :$port → $code"
        fi
    else
        ALL_OK=false
        if $JSON_MODE; then
            echo "  {\"label\":\"$label\",\"port\":$port,\"status\":\"dead\",\"code\":$code}"
        else
            echo "  ❌ $label :$port → $code"
        fi
    fi
}

check_skills() {
    local port=$1 label=$2
    local n
    n=$(curl -s -m "$TIMEOUT" "http://127.0.0.1:$port/a2a/.well-known/agent-card.json" 2>/dev/null | \
        python3 -c "import sys,json; print(len(json.load(sys.stdin)['skills']))" 2>/dev/null || echo "DEAD")
    if $JSON_MODE; then
        echo "  {\"label\":\"$label\",\"port\":$port,\"skills\":\"$n\"}"
    else
        echo "     skills: $n"
    fi
}

if $JSON_MODE; then
    echo "{"
    echo '  "timestamp":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",'
else
    echo "=== hermes-a2a-doctor @ $(date) ==="
fi

# A2A endpoints
if $JSON_MODE; then
    echo '  "a2a": ['
else
    echo ""
    echo "--- A2A Endpoints ---"
fi

first=true
for port in "${A2A_PORTS[@]}"; do
    label=""
    case $port in
        8668) label="engineer" ;;
        8676) label="shangshu" ;;
        8686) label="budget" ;;
        8689) label="regent" ;;
        8695) label="default" ;;
        8698) label="gongbu" ;;
    esac
    if $JSON_MODE; then
        $first || echo ","
        first=false
    fi
    check_endpoint "$port" "$label" "/health"
    check_skills "$port" "$label"
done

if $JSON_MODE; then
    echo ""
    echo '  ],'
    echo '  "api_server": ['
else
    echo ""
    echo "--- API Server ---"
fi

first=true
for port in "${API_PORTS[@]}"; do
    label=""
    case $port in
        8642) label="default" ;;
        8643) label="regent" ;;
    esac
    if $JSON_MODE; then
        $first || echo ","
        first=false
    fi
    check_endpoint "$port" "$label" "/health"
done

if $JSON_MODE; then
    echo ""
    echo "  ],"
    echo "  \"all_ok\": $ALL_OK"
    echo "}"
else
    echo ""
    if $ALL_OK; then
        echo "✅ ALL HEALTHY"
    else
        echo "❌ SOME DEAD — investigate!"
    fi
fi

exit $($ALL_OK && echo 0 || echo 1)
