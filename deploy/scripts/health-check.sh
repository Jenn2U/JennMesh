#!/bin/bash
#
# health-check.sh — JennMesh post-deploy health verification
#
# Checks all 4 systemd services, dashboard HTTP, MQTT port, USB device, and DB.
# Exit 0 = healthy, Exit 1 = unhealthy
#
# Usage:
#   ./health-check.sh
#   ./health-check.sh --verbose
#

set -uo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
VERBOSE="${1:-}"
FAILURES=0

check() {
    local name="$1"
    local result="$2"
    local detail="${3:-}"

    if [[ "$result" == "ok" ]]; then
        echo -e "  ${GREEN}[PASS]${NC} $name"
        if [[ -n "$detail" && "$VERBOSE" == "--verbose" ]]; then
            echo "         $detail"
        fi
    else
        echo -e "  ${RED}[FAIL]${NC} $name"
        if [[ -n "$detail" ]]; then
            echo -e "         ${YELLOW}$detail${NC}"
        fi
        FAILURES=$((FAILURES + 1))
    fi
}

echo "JennMesh Health Check"
echo "====================="
echo ""

# ── Systemd Services ─────────────────────────────────────────────
echo "Services:"
for svc in jenn-mesh-broker jenn-mesh-dashboard jenn-mesh-agent jenn-sentry-agent; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        uptime=$(systemctl show "$svc" --property=ActiveEnterTimestamp --value 2>/dev/null || echo "unknown")
        check "$svc" "ok" "since $uptime"
    else
        status=$(systemctl is-active "$svc" 2>/dev/null || echo "not found")
        check "$svc" "fail" "status: $status"
    fi
done

echo ""

# ── Dashboard HTTP ────────────────────────────────────────────────
echo "Endpoints:"
if command -v curl &>/dev/null; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8002/health --max-time 5 2>/dev/null || echo "000")
    if [[ "$HTTP_CODE" == "200" ]]; then
        check "Dashboard HTTP :8002/health" "ok" "HTTP $HTTP_CODE"
    else
        check "Dashboard HTTP :8002/health" "fail" "HTTP $HTTP_CODE"
    fi
else
    # Fallback: use bash /dev/tcp
    if (echo > /dev/tcp/127.0.0.1/8002) 2>/dev/null; then
        check "Dashboard TCP :8002" "ok" "port open"
    else
        check "Dashboard TCP :8002" "fail" "port closed"
    fi
fi

# ── MQTT Broker ───────────────────────────────────────────────────
if (echo > /dev/tcp/127.0.0.1/1884) 2>/dev/null; then
    check "MQTT Broker :1884" "ok" "port open"
else
    check "MQTT Broker :1884" "fail" "port closed"
fi

echo ""

# ── USB Radio Device ──────────────────────────────────────────────
echo "Hardware:"
MESHTASTIC_DEVS=$(ls /dev/meshtastic* 2>/dev/null | wc -l)
if [[ "$MESHTASTIC_DEVS" -gt 0 ]]; then
    dev_list=$(ls /dev/meshtastic* 2>/dev/null | tr '\n' ' ')
    check "Meshtastic USB device" "ok" "$dev_list"
else
    # Check for raw tty devices that might be radios
    TTY_DEVS=$(ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null | wc -l)
    if [[ "$TTY_DEVS" -gt 0 ]]; then
        check "Meshtastic USB device" "ok" "found /dev/tty* (udev symlink may be pending)"
    else
        check "Meshtastic USB device" "fail" "no USB radio detected — plug in a Meshtastic radio"
    fi
fi

echo ""

# ── SQLite Database ───────────────────────────────────────────────
echo "Data:"
DB_PATH="${JENN_MESH_DB_PATH:-/var/lib/jenn-mesh/mesh.db}"
if [[ -f "$DB_PATH" ]]; then
    if sqlite3 "$DB_PATH" "SELECT 1;" &>/dev/null; then
        db_size=$(du -h "$DB_PATH" | cut -f1)
        check "SQLite database" "ok" "$DB_PATH ($db_size)"
    else
        check "SQLite database" "fail" "database exists but unreadable"
    fi
else
    check "SQLite database" "fail" "not found at $DB_PATH (will be created on first request)"
    # This isn't a fatal failure — DB is created on first run
    FAILURES=$((FAILURES - 1))
fi

echo ""
echo "====================="
if [[ "$FAILURES" -le 0 ]]; then
    echo -e "${GREEN}Health check: ALL PASSED${NC}"
    exit 0
else
    echo -e "${RED}Health check: $FAILURES FAILURE(S)${NC}"
    exit 1
fi
