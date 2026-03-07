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

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
VERBOSE="${1:-}"
FAILURES=0
WARNINGS=0

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

warn() {
    local name="$1"
    local detail="${2:-}"
    echo -e "  ${CYAN}[SKIP]${NC} $name"
    if [[ -n "$detail" ]]; then
        echo -e "         ${YELLOW}$detail${NC}"
    fi
    WARNINGS=$((WARNINGS + 1))
}

echo "JennMesh Health Check"
echo "====================="
echo ""

# ── Systemd Services ─────────────────────────────────────────────
# Core services (broker, dashboard) are hard failures.
# Conditional services (agent, sentry) are context-dependent:
#   - agent: only a failure if radio hardware is present
#   - sentry: only a failure if it's enabled on this host
echo "Services:"

# Detect radio hardware upfront (used for agent check)
HAS_RADIO=false
if ls /dev/meshtastic* /dev/ttyUSB* /dev/ttyACM* &>/dev/null 2>&1; then
    HAS_RADIO=true
fi

for svc in jenn-mesh-broker jenn-mesh-dashboard jenn-mesh-agent jenn-radio-watcher jenn-sentry-agent; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        uptime=$(systemctl show "$svc" --property=ActiveEnterTimestamp --value 2>/dev/null || echo "unknown")
        check "$svc" "ok" "since $uptime"
    else
        status=$(systemctl is-active "$svc" 2>/dev/null || echo "not found")

        # Context-aware: mesh-agent without radio is expected
        if [[ "$svc" == "jenn-mesh-agent" && "$HAS_RADIO" == "false" ]]; then
            warn "$svc" "no radio hardware — agent disabled until radio is plugged in"
            continue
        fi

        # Context-aware: radio watcher not enabled on this host
        if [[ "$svc" == "jenn-radio-watcher" ]]; then
            if ! systemctl is-enabled --quiet "$svc" 2>/dev/null; then
                warn "$svc" "not enabled on this host"
                continue
            fi
        fi

        # Context-aware: sentry managed by another venv or not enabled
        if [[ "$svc" == "jenn-sentry-agent" ]]; then
            if ! systemctl is-enabled --quiet "$svc" 2>/dev/null; then
                warn "$svc" "not enabled on this host"
                continue
            fi
        fi

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
# Radio is hardware-dependent — missing radio is a warning, not a failure.
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
        warn "Meshtastic USB device" "no radio detected — plug in a Meshtastic radio when ready"
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
    if [[ "$WARNINGS" -gt 0 ]]; then
        echo -e "${GREEN}Health check: PASSED${NC} (${WARNINGS} skipped)"
    else
        echo -e "${GREEN}Health check: ALL PASSED${NC}"
    fi
    exit 0
else
    echo -e "${RED}Health check: $FAILURES FAILURE(S)${NC}"
    if [[ "$WARNINGS" -gt 0 ]]; then
        echo -e "${YELLOW}  ($WARNINGS skipped — hardware/config dependent)${NC}"
    fi
    exit 1
fi
