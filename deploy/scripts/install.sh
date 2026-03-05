#!/bin/bash
#
# install.sh — JennMesh Mesh Appliance Installer
#
# Installs dashboard + agent + MQTT broker on ARM64 Linux
# Idempotent — safe to re-run for upgrades
#
# Usage:
#   sudo ./install.sh --version 0.2.0 --tarball /tmp/jenn-mesh-0.2.0-arm64.tar.gz
#   sudo ./install.sh --version 0.2.0 --tarball /tmp/jenn-mesh-0.2.0-arm64.tar.gz --subnet 10.10.50.0/24
#

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

# ── Defaults ──────────────────────────────────────────────────────
INSTALL_BASE="/opt/jenn-mesh"
CONFIG_DIR="/etc/jenn-mesh"
DATA_DIR="/var/lib/jenn-mesh"
LOG_DIR="/var/log/jenn-mesh"
SERVICE_USER="jenn-mesh"
SERVICE_GROUP="jenn-mesh"
LAN_SUBNET=""
VERSION=""
TARBALL=""

# ── Parse Arguments ───────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)  VERSION="$2"; shift 2 ;;
        --tarball)  TARBALL="$2"; shift 2 ;;
        --subnet)   LAN_SUBNET="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: sudo ./install.sh --version <ver> --tarball <path> [--subnet <cidr>]"
            echo ""
            echo "Options:"
            echo "  --version   Version to install (e.g., 0.2.0)"
            echo "  --tarball   Path to jenn-mesh-<ver>-arm64.tar.gz"
            echo "  --subnet    LAN subnet for firewall rules (e.g., 10.10.50.0/24)"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ -z "$VERSION" || -z "$TARBALL" ]]; then
    echo -e "${RED}Error: --version and --tarball are required${NC}"
    echo "Usage: sudo ./install.sh --version 0.2.0 --tarball /tmp/jenn-mesh-0.2.0-arm64.tar.gz"
    exit 1
fi

INSTALL_DIR="$INSTALL_BASE/$VERSION"

echo ""
echo -e "${CYAN}${BOLD}=================================================="
echo "  JennMesh Mesh Appliance Installer"
echo "  Version: $VERSION"
echo "==================================================${NC}"
echo ""

# ── Phase 1: Pre-flight Checks ───────────────────────────────────
echo -e "${BOLD}[1/9] Pre-flight checks...${NC}"

# Must be root
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}Error: Must run as root (sudo)${NC}"
    exit 1
fi

# Check architecture
ARCH=$(uname -m)
if [[ "$ARCH" != "aarch64" && "$ARCH" != "arm64" ]]; then
    echo -e "${YELLOW}Warning: Expected ARM64 (aarch64), got $ARCH${NC}"
    echo "Proceeding anyway — this may work on x86_64 for testing"
fi

# Check for systemd
if ! command -v systemctl &>/dev/null; then
    echo -e "${RED}Error: systemd is required${NC}"
    exit 1
fi

# Find Python 3.11-3.13
PYTHON_BIN=""
for pyver in python3.13 python3.12 python3.11 python3; do
    if command -v "$pyver" &>/dev/null; then
        py_version=$("$pyver" -c "import sys; print(f'{sys.version_info.minor}')")
        if [[ "$py_version" -ge 11 && "$py_version" -le 13 ]]; then
            PYTHON_BIN=$(command -v "$pyver")
            break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    echo -e "${RED}Error: Python 3.11-3.13 required (3.14+ not supported)${NC}"
    echo "Install with: apt-get install python3.12 python3.12-venv"
    exit 1
fi
echo -e "  Python: $PYTHON_BIN ($($PYTHON_BIN --version))"

# Check tarball exists
if [[ ! -f "$TARBALL" ]]; then
    echo -e "${RED}Error: Tarball not found: $TARBALL${NC}"
    exit 1
fi

echo -e "  ${GREEN}Pre-flight checks passed${NC}"

# ── Phase 2: Create System User ──────────────────────────────────
echo -e "${BOLD}[2/9] Creating system user...${NC}"

if id "$SERVICE_USER" &>/dev/null; then
    echo "  User '$SERVICE_USER' already exists"
else
    useradd --system --shell /usr/sbin/nologin --home-dir "$DATA_DIR" \
        --create-home "$SERVICE_USER"
    echo -e "  ${GREEN}Created user '$SERVICE_USER'${NC}"
fi

# Ensure dialout group membership for USB serial access
if groups "$SERVICE_USER" | grep -q dialout; then
    echo "  User already in dialout group"
else
    usermod -aG dialout "$SERVICE_USER"
    echo -e "  ${GREEN}Added '$SERVICE_USER' to dialout group${NC}"
fi

# ── Phase 3: Create Directory Structure ──────────────────────────
echo -e "${BOLD}[3/9] Creating directories...${NC}"

mkdir -p "$INSTALL_BASE"
mkdir -p "$CONFIG_DIR"
mkdir -p "$DATA_DIR/mosquitto"
mkdir -p "$DATA_DIR/backups"
mkdir -p "$LOG_DIR"

chown -R "$SERVICE_USER:$SERVICE_GROUP" "$DATA_DIR"
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$LOG_DIR"

echo -e "  ${GREEN}Directories created${NC}"

# ── Phase 4: Extract + Install Application ───────────────────────
echo -e "${BOLD}[4/9] Installing application (v$VERSION)...${NC}"

# Track previous version for rollback
if [[ -L "$INSTALL_BASE/current" ]]; then
    PREV_TARGET=$(readlink "$INSTALL_BASE/current")
    if [[ "$PREV_TARGET" != "$INSTALL_DIR" ]]; then
        ln -sfn "$PREV_TARGET" "$INSTALL_BASE/previous"
        echo "  Previous version saved: $PREV_TARGET"
    fi
fi

# Extract tarball
if [[ -d "$INSTALL_DIR" ]]; then
    echo "  Removing existing $INSTALL_DIR"
    rm -rf "$INSTALL_DIR"
fi

mkdir -p "$INSTALL_DIR"
tar xzf "$TARBALL" -C "$INSTALL_DIR" --strip-components=1

# Create virtualenv and install
echo "  Creating virtualenv..."
"$PYTHON_BIN" -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip wheel --quiet

echo "  Installing jenn-mesh package..."
"$INSTALL_DIR/venv/bin/pip" install -e "$INSTALL_DIR[dashboard,agent]" --quiet

# Update current symlink
ln -sfn "$INSTALL_DIR" "$INSTALL_BASE/current"
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR"

echo -e "  ${GREEN}Application installed: $INSTALL_BASE/current -> $VERSION${NC}"

# ── Phase 5: Install Mosquitto ────────────────────────────────────
echo -e "${BOLD}[5/9] Installing Mosquitto MQTT broker...${NC}"

if command -v mosquitto &>/dev/null; then
    echo "  Mosquitto already installed ($(mosquitto -h 2>&1 | head -1 || echo 'version unknown'))"
else
    apt-get update -qq
    apt-get install -y -qq mosquitto mosquitto-clients
    # Disable default mosquitto service — we run our own
    systemctl stop mosquitto 2>/dev/null || true
    systemctl disable mosquitto 2>/dev/null || true
    echo -e "  ${GREEN}Mosquitto installed${NC}"
fi

# Generate MQTT password file if it doesn't exist
if [[ ! -f "$CONFIG_DIR/mosquitto_passwd" ]]; then
    MQTT_PASS=$(openssl rand -base64 16 | tr -d '=+/')
    mosquitto_passwd -b -c "$CONFIG_DIR/mosquitto_passwd" "jenn-mesh" "$MQTT_PASS"
    chown "$SERVICE_USER:$SERVICE_GROUP" "$CONFIG_DIR/mosquitto_passwd"
    chmod 640 "$CONFIG_DIR/mosquitto_passwd"
    echo -e "  ${GREEN}MQTT password generated${NC}"
    echo -e "  ${YELLOW}MQTT password: $MQTT_PASS${NC}"
    echo "  (Save this — it won't be shown again)"

    # Update env file with generated password
    if [[ -f "$CONFIG_DIR/env" ]]; then
        sed -i "s/MQTT_PASSWORD=changeme/MQTT_PASSWORD=$MQTT_PASS/" "$CONFIG_DIR/env"
    fi
else
    echo "  MQTT password file already exists"
fi

# ── Phase 6: Deploy Configuration ────────────────────────────────
echo -e "${BOLD}[6/9] Deploying configuration...${NC}"

# Environment file — preserve existing
if [[ ! -f "$CONFIG_DIR/env" ]]; then
    cp "$INSTALL_DIR/deploy/config/env.template" "$CONFIG_DIR/env"
    chown "$SERVICE_USER:$SERVICE_GROUP" "$CONFIG_DIR/env"
    chmod 640 "$CONFIG_DIR/env"
    echo -e "  ${GREEN}Created $CONFIG_DIR/env from template${NC}"
    echo -e "  ${YELLOW}Review and edit $CONFIG_DIR/env before starting services${NC}"
else
    echo "  Environment file exists — preserved"
fi

# Mosquitto config — always update (stateless)
cp "$INSTALL_DIR/deploy/config/mosquitto-prod.conf" "$CONFIG_DIR/mosquitto.conf"
chown "$SERVICE_USER:$SERVICE_GROUP" "$CONFIG_DIR/mosquitto.conf"
echo "  Mosquitto config updated"

echo -e "  ${GREEN}Configuration deployed${NC}"

# ── Phase 7: Install Udev Rules ──────────────────────────────────
echo -e "${BOLD}[7/9] Installing udev rules...${NC}"

cp "$INSTALL_DIR/deploy/udev/99-meshtastic.rules" /etc/udev/rules.d/
udevadm control --reload-rules
udevadm trigger

echo -e "  ${GREEN}Udev rules installed — Meshtastic radios will appear as /dev/meshtastic*${NC}"

# ── Phase 8: Install Systemd Services + Cron ─────────────────────
echo -e "${BOLD}[8/9] Installing systemd services...${NC}"

# Stop existing JennMesh services gracefully
for svc in jenn-mesh-agent jenn-mesh-dashboard jenn-mesh-broker; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        systemctl stop "$svc"
        echo "  Stopped $svc"
    fi
done

# Only stop sentry if we manage it (binary exists in JennMesh venv)
if [[ -x "$INSTALL_DIR/venv/bin/jenn-sentry-agent" ]] || ! systemctl is-enabled --quiet jenn-sentry-agent 2>/dev/null; then
    if systemctl is-active --quiet jenn-sentry-agent 2>/dev/null; then
        systemctl stop jenn-sentry-agent
        echo "  Stopped jenn-sentry-agent"
    fi
else
    echo "  Sentry agent preserved (managed by another venv)"
fi

# Install service files — core JennMesh services
for svc in jenn-mesh-broker jenn-mesh-dashboard jenn-mesh-agent; do
    cp "$INSTALL_DIR/deploy/systemd/${svc}.service" /etc/systemd/system/
done

# Sentry agent: only install service if binary exists in JennMesh venv.
# On shared hosts (e.g., OrangePi with JennEdge), sentry runs from the
# JennEdge venv — overwriting its service unit breaks it.
SENTRY_BIN="$INSTALL_DIR/venv/bin/jenn-sentry-agent"
if [[ -x "$SENTRY_BIN" ]]; then
    cp "$INSTALL_DIR/deploy/systemd/jenn-sentry-agent.service" /etc/systemd/system/
    echo "  Sentry service installed (binary found in JennMesh venv)"
elif systemctl is-enabled --quiet jenn-sentry-agent 2>/dev/null; then
    echo "  Sentry service preserved (managed by another venv)"
else
    cp "$INSTALL_DIR/deploy/systemd/jenn-sentry-agent.service" /etc/systemd/system/
    echo "  Sentry service installed (fresh install)"
fi

systemctl daemon-reload

# Enable services to start on boot
for svc in jenn-mesh-broker jenn-mesh-dashboard jenn-mesh-agent; do
    systemctl enable "$svc"
done
# Only enable sentry if we installed it or it's not yet enabled
if [[ -x "$SENTRY_BIN" ]] || ! systemctl is-enabled --quiet jenn-sentry-agent 2>/dev/null; then
    systemctl enable jenn-sentry-agent
fi
echo "  Services enabled for boot"

# Install nightly backup cron
BACKUP_SCRIPT="$INSTALL_BASE/current/deploy/scripts/backup-mesh-db.sh"
CRON_LINE="0 2 * * * $BACKUP_SCRIPT"
if ! crontab -u "$SERVICE_USER" -l 2>/dev/null | grep -q "backup-mesh-db"; then
    (crontab -u "$SERVICE_USER" -l 2>/dev/null; echo "$CRON_LINE") | crontab -u "$SERVICE_USER" -
    echo "  Nightly backup cron installed (02:00)"
else
    echo "  Backup cron already exists"
fi

echo -e "  ${GREEN}Systemd services installed${NC}"

# ── Phase 8.5: Firewall (optional) ───────────────────────────────
if [[ -n "$LAN_SUBNET" ]] && command -v ufw &>/dev/null; then
    echo -e "${BOLD}[8.5] Configuring firewall...${NC}"
    ufw allow from "$LAN_SUBNET" to any port 8002 proto tcp comment "JennMesh Dashboard"
    ufw allow from "$LAN_SUBNET" to any port 1884 proto tcp comment "JennMesh MQTT"
    echo -e "  ${GREEN}UFW rules added for $LAN_SUBNET${NC}"
fi

# ── Phase 9: Start Services + Health Check ───────────────────────
echo -e "${BOLD}[9/9] Starting services...${NC}"

systemctl start jenn-mesh-broker
sleep 2
systemctl start jenn-mesh-dashboard
sleep 2
systemctl start jenn-mesh-agent
sleep 2

# Start sentry only if we manage it
SENTRY_BIN="$INSTALL_BASE/current/venv/bin/jenn-sentry-agent"
if [[ -x "$SENTRY_BIN" ]]; then
    systemctl start jenn-sentry-agent
elif ! systemctl is-active --quiet jenn-sentry-agent 2>/dev/null; then
    # Sentry managed by another venv — restart it if it was stopped
    systemctl start jenn-sentry-agent 2>/dev/null || true
fi

echo "  Waiting for services to stabilize..."
sleep 5

# Run health check
HEALTH_SCRIPT="$INSTALL_BASE/current/deploy/scripts/health-check.sh"
if [[ -x "$HEALTH_SCRIPT" ]]; then
    if bash "$HEALTH_SCRIPT"; then
        echo ""
        echo -e "${GREEN}${BOLD}=================================================="
        echo "  JennMesh v$VERSION installed successfully!"
        echo "==================================================${NC}"
    else
        echo ""
        echo -e "${YELLOW}${BOLD}=================================================="
        echo "  JennMesh v$VERSION installed with warnings"
        echo "  Check: journalctl -u jenn-mesh-* --since '5 min ago'"
        echo "==================================================${NC}"
    fi
else
    echo -e "  ${YELLOW}Health check script not found — skipping${NC}"
fi

echo ""
echo -e "${CYAN}Dashboard:  http://$(hostname -I | awk '{print $1}'):8002${NC}"
echo -e "${CYAN}MQTT:       $(hostname -I | awk '{print $1}'):1884${NC}"
echo -e "${CYAN}Logs:       journalctl -u jenn-mesh-* -f${NC}"
echo -e "${CYAN}USB radio:  ls -la /dev/meshtastic*${NC}"
echo ""
