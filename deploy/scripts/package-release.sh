#!/bin/bash
#
# package-release.sh — Build JennMesh release tarball
#
# Creates a deployable tarball for ARM64 Linux mesh appliances.
# Run on build agent (CI pipeline) or developer machine.
#
# Usage:
#   ./package-release.sh [version]
#
# Output:
#   dist/jenn-mesh-<version>-arm64.tar.gz
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DIST_DIR="$PROJECT_ROOT/dist"

# Get version from VERSION file or argument
if [[ -n "${1:-}" ]]; then
    VERSION="$1"
elif [[ -f "$PROJECT_ROOT/VERSION" ]]; then
    VERSION=$(cat "$PROJECT_ROOT/VERSION" | tr -d '[:space:]')
else
    VERSION=$(grep '^version' "$PROJECT_ROOT/pyproject.toml" | sed 's/.*"\(.*\)".*/\1/')
fi

PLATFORM_ID="arm64"
RELEASE_NAME="jenn-mesh-${VERSION}-${PLATFORM_ID}"
WORK_DIR=$(mktemp -d)
RELEASE_DIR="$WORK_DIR/$RELEASE_NAME"

echo "=================================================="
echo "  JennMesh Release Packager"
echo "  Version: $VERSION"
echo "  Platform: $PLATFORM_ID"
echo "=================================================="
echo ""

# Clean previous artifacts
rm -f "$DIST_DIR/$RELEASE_NAME.tar.gz"
mkdir -p "$DIST_DIR"
mkdir -p "$RELEASE_DIR"

echo "[1/5] Copying application source..."
rsync -a \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='.pytest_cache' \
    --exclude='.mypy_cache' \
    --exclude='*.egg-info' \
    --exclude='.env' \
    --exclude='.env.*' \
    --exclude='*.db' \
    --exclude='*.log' \
    --exclude='dist/' \
    --exclude='build/' \
    --exclude='.azure-pipelines/' \
    --exclude='tests/' \
    "$PROJECT_ROOT/" \
    "$RELEASE_DIR/"

echo "[2/5] Freezing Python dependencies..."
if [[ -f "$PROJECT_ROOT/requirements.txt" ]]; then
    cp "$PROJECT_ROOT/requirements.txt" "$RELEASE_DIR/"
else
    # Generate from pyproject.toml extras
    echo "  (No requirements.txt — will install from pyproject.toml on target)"
fi

echo "[3/5] Verifying deploy files present..."
for f in deploy/systemd/jenn-mesh-broker.service \
         deploy/systemd/jenn-mesh-dashboard.service \
         deploy/systemd/jenn-mesh-agent.service \
         deploy/systemd/jenn-sentry-agent.service \
         deploy/config/env.template \
         deploy/config/mosquitto-prod.conf \
         deploy/udev/99-meshtastic.rules \
         deploy/scripts/install.sh \
         deploy/scripts/backup-mesh-db.sh \
         deploy/scripts/health-check.sh; do
    if [[ ! -f "$RELEASE_DIR/$f" ]]; then
        echo "  WARNING: Missing $f"
    fi
done

echo "[4/5] Making scripts executable..."
chmod +x "$RELEASE_DIR/deploy/scripts/"*.sh 2>/dev/null || true

echo "[5/5] Creating tarball..."
tar czf "$DIST_DIR/$RELEASE_NAME.tar.gz" -C "$WORK_DIR" "$RELEASE_NAME"

# Cleanup
rm -rf "$WORK_DIR"

SIZE=$(du -h "$DIST_DIR/$RELEASE_NAME.tar.gz" | cut -f1)
echo ""
echo "=================================================="
echo "  Release package created:"
echo "  $DIST_DIR/$RELEASE_NAME.tar.gz ($SIZE)"
echo "=================================================="
