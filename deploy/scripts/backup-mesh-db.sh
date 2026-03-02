#!/bin/bash
#
# backup-mesh-db.sh — Nightly SQLite backup for JennMesh
#
# Uses SQLite .backup command (safe with WAL mode).
# Retains 14 days of backups.
#
# Installed via cron: 0 2 * * * /opt/jenn-mesh/current/deploy/scripts/backup-mesh-db.sh
#

set -euo pipefail

DB_PATH="${JENN_MESH_DB_PATH:-/var/lib/jenn-mesh/mesh.db}"
BACKUP_DIR="${JENN_MESH_BACKUP_DIR:-/var/lib/jenn-mesh/backups}"
RETAIN_DAYS=14
DATE=$(date +%Y%m%d-%H%M%S)
BACKUP_FILE="$BACKUP_DIR/mesh-$DATE.db"

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

# Check database exists
if [[ ! -f "$DB_PATH" ]]; then
    echo "Database not found: $DB_PATH — skipping backup"
    exit 0
fi

# SQLite online backup (safe with WAL mode and concurrent writers)
sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"

# Compress
gzip "$BACKUP_FILE"

echo "Backup created: ${BACKUP_FILE}.gz ($(du -h "${BACKUP_FILE}.gz" | cut -f1))"

# Prune old backups (keep last N days)
find "$BACKUP_DIR" -name "mesh-*.db.gz" -mtime +"$RETAIN_DAYS" -delete
REMAINING=$(find "$BACKUP_DIR" -name "mesh-*.db.gz" | wc -l)
echo "Backups retained: $REMAINING"
