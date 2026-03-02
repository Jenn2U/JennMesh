"""SQLite WAL database for JennMesh device registry, positions, and alerts."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

SCHEMA_VERSION = 2

SCHEMA_SQL = """
-- Device registry: every known radio in the fleet
CREATE TABLE IF NOT EXISTS devices (
    node_id         TEXT PRIMARY KEY,
    long_name       TEXT NOT NULL DEFAULT '',
    short_name      TEXT NOT NULL DEFAULT '',
    role            TEXT NOT NULL DEFAULT 'CLIENT',
    hw_model        TEXT NOT NULL DEFAULT 'unknown',
    firmware_version TEXT NOT NULL DEFAULT 'unknown',
    config_hash     TEXT,
    template_role   TEXT,
    template_hash   TEXT,
    battery_level   INTEGER,
    voltage         REAL,
    signal_snr      REAL,
    signal_rssi     INTEGER,
    latitude        REAL,
    longitude       REAL,
    altitude        REAL,
    last_seen       TEXT,
    registered_at   TEXT NOT NULL DEFAULT (datetime('now')),
    associated_edge_node TEXT
);

-- Position history: GPS reports over time (for tracking + locator)
CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id         TEXT NOT NULL,
    latitude        REAL NOT NULL,
    longitude       REAL NOT NULL,
    altitude        REAL,
    precision_bits  INTEGER,
    source          TEXT NOT NULL DEFAULT 'gps',
    timestamp       TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (node_id) REFERENCES devices(node_id)
);
CREATE INDEX IF NOT EXISTS idx_positions_node_time ON positions(node_id, timestamp DESC);

-- Fleet alerts: health warnings and critical events
CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id         TEXT NOT NULL,
    alert_type      TEXT NOT NULL,
    severity        TEXT NOT NULL,
    message         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at     TEXT,
    is_resolved     INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (node_id) REFERENCES devices(node_id)
);
CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(is_resolved, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_node ON alerts(node_id, is_resolved);

-- Golden config templates: version-controlled YAML configs per role
CREATE TABLE IF NOT EXISTS config_templates (
    role            TEXT PRIMARY KEY,
    yaml_content    TEXT NOT NULL,
    config_hash     TEXT NOT NULL,
    version         TEXT NOT NULL DEFAULT '1',
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Provisioning log: audit trail for bench flash operations
CREATE TABLE IF NOT EXISTS provisioning_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id         TEXT NOT NULL,
    action          TEXT NOT NULL,
    role            TEXT,
    template_hash   TEXT,
    operator        TEXT NOT NULL DEFAULT 'system',
    details         TEXT,
    timestamp       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_prov_log_node ON provisioning_log(node_id, timestamp DESC);

-- Channel definitions: centrally managed channel PSKs
CREATE TABLE IF NOT EXISTS channels (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_index   INTEGER NOT NULL,
    name            TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'custom',
    psk             TEXT NOT NULL,
    uplink_enabled  INTEGER NOT NULL DEFAULT 0,
    downlink_enabled INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(channel_index, name)
);

-- Topology edges: directed links between mesh nodes (from NEIGHBORINFO packets)
CREATE TABLE IF NOT EXISTS topology_edges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node       TEXT NOT NULL,
    to_node         TEXT NOT NULL,
    snr             REAL,
    rssi            INTEGER,
    last_updated    TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (from_node) REFERENCES devices(node_id),
    FOREIGN KEY (to_node)   REFERENCES devices(node_id),
    UNIQUE(from_node, to_node)
);
CREATE INDEX IF NOT EXISTS idx_topo_from ON topology_edges(from_node);
CREATE INDEX IF NOT EXISTS idx_topo_to   ON topology_edges(to_node);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version         INTEGER NOT NULL,
    applied_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class MeshDatabase:
    """SQLite WAL database manager for JennMesh."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(Path.home() / ".jenn-mesh" / "mesh.db")
        self.db_path = db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database with schema and WAL mode."""
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(SCHEMA_SQL)

            # Check and set schema version
            cursor = conn.execute(
                "SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            else:
                current_version = row["version"]
                if current_version < SCHEMA_VERSION:
                    # Migration v1 → v2: topology_edges table
                    # CREATE TABLE IF NOT EXISTS is idempotent — safe to re-run
                    conn.execute(
                        "INSERT INTO schema_version (version) VALUES (?)",
                        (SCHEMA_VERSION,),
                    )

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for database connections with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def upsert_device(
        self,
        node_id: str,
        *,
        long_name: Optional[str] = None,
        short_name: Optional[str] = None,
        role: Optional[str] = None,
        hw_model: Optional[str] = None,
        firmware_version: Optional[str] = None,
        battery_level: Optional[int] = None,
        voltage: Optional[float] = None,
        signal_snr: Optional[float] = None,
        signal_rssi: Optional[int] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        altitude: Optional[float] = None,
        last_seen: Optional[str] = None,
        associated_edge_node: Optional[str] = None,
    ) -> None:
        """Insert or update a device record. Only non-None fields are updated."""
        with self.connection() as conn:
            # Check if device exists
            existing = conn.execute(
                "SELECT node_id FROM devices WHERE node_id = ?", (node_id,)
            ).fetchone()

            if existing is None:
                conn.execute(
                    """INSERT INTO devices (node_id, long_name, short_name, role,
                       hw_model, firmware_version, battery_level, voltage,
                       signal_snr, signal_rssi, latitude, longitude, altitude,
                       last_seen, associated_edge_node)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        node_id,
                        long_name or "",
                        short_name or "",
                        role or "CLIENT",
                        hw_model or "unknown",
                        firmware_version or "unknown",
                        battery_level,
                        voltage,
                        signal_snr,
                        signal_rssi,
                        latitude,
                        longitude,
                        altitude,
                        last_seen,
                        associated_edge_node,
                    ),
                )
            else:
                updates: list[str] = []
                values: list[object] = []
                field_map = {
                    "long_name": long_name,
                    "short_name": short_name,
                    "role": role,
                    "hw_model": hw_model,
                    "firmware_version": firmware_version,
                    "battery_level": battery_level,
                    "voltage": voltage,
                    "signal_snr": signal_snr,
                    "signal_rssi": signal_rssi,
                    "latitude": latitude,
                    "longitude": longitude,
                    "altitude": altitude,
                    "last_seen": last_seen,
                    "associated_edge_node": associated_edge_node,
                }
                for field, value in field_map.items():
                    if value is not None:
                        updates.append(f"{field} = ?")
                        values.append(value)

                if updates:
                    values.append(node_id)
                    conn.execute(
                        f"UPDATE devices SET {', '.join(updates)} WHERE node_id = ?",
                        values,
                    )

    def get_device(self, node_id: str) -> Optional[dict]:
        """Get a single device by node_id."""
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM devices WHERE node_id = ?", (node_id,)).fetchone()
            return dict(row) if row else None

    def list_devices(self) -> list[dict]:
        """List all devices, ordered by last_seen descending."""
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM devices ORDER BY last_seen DESC").fetchall()
            return [dict(r) for r in rows]

    def add_position(
        self,
        node_id: str,
        latitude: float,
        longitude: float,
        altitude: Optional[float] = None,
        precision_bits: Optional[int] = None,
        source: str = "gps",
        timestamp: Optional[str] = None,
    ) -> None:
        """Record a GPS position for a device."""
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO positions
                   (node_id, latitude, longitude, altitude, precision_bits, source, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))""",
                (node_id, latitude, longitude, altitude, precision_bits, source, timestamp),
            )

    def get_latest_position(self, node_id: str) -> Optional[dict]:
        """Get the most recent position for a device."""
        with self.connection() as conn:
            row = conn.execute(
                """SELECT * FROM positions WHERE node_id = ?
                   ORDER BY timestamp DESC LIMIT 1""",
                (node_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_positions_in_radius(
        self,
        latitude: float,
        longitude: float,
        radius_degrees: float,
    ) -> list[dict]:
        """Get recent positions within approximate radius (bounding box filter)."""
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT p.*, d.long_name, d.last_seen FROM positions p
                   JOIN devices d ON p.node_id = d.node_id
                   WHERE p.latitude BETWEEN ? AND ?
                   AND p.longitude BETWEEN ? AND ?
                   AND p.id IN (
                       SELECT MAX(id) FROM positions GROUP BY node_id
                   )
                   ORDER BY p.timestamp DESC""",
                (
                    latitude - radius_degrees,
                    latitude + radius_degrees,
                    longitude - radius_degrees,
                    longitude + radius_degrees,
                ),
            ).fetchall()
            return [dict(r) for r in rows]

    def create_alert(
        self,
        node_id: str,
        alert_type: str,
        severity: str,
        message: str,
    ) -> int:
        """Create a new alert. Returns the alert ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO alerts (node_id, alert_type, severity, message)
                   VALUES (?, ?, ?, ?)""",
                (node_id, alert_type, severity, message),
            )
            return cursor.lastrowid or 0

    def resolve_alert(self, alert_id: int) -> None:
        """Mark an alert as resolved."""
        with self.connection() as conn:
            conn.execute(
                """UPDATE alerts SET is_resolved = 1, resolved_at = datetime('now')
                   WHERE id = ?""",
                (alert_id,),
            )

    def get_active_alerts(self, node_id: Optional[str] = None) -> list[dict]:
        """Get active (unresolved) alerts, optionally filtered by node."""
        with self.connection() as conn:
            if node_id:
                rows = conn.execute(
                    """SELECT * FROM alerts WHERE is_resolved = 0 AND node_id = ?
                       ORDER BY created_at DESC""",
                    (node_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM alerts WHERE is_resolved = 0 ORDER BY created_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def has_active_alert(self, node_id: str, alert_type: str) -> bool:
        """Check if an active alert of this type already exists for the node."""
        with self.connection() as conn:
            row = conn.execute(
                """SELECT id FROM alerts
                   WHERE node_id = ? AND alert_type = ? AND is_resolved = 0
                   LIMIT 1""",
                (node_id, alert_type),
            ).fetchone()
            return row is not None

    def log_provisioning(
        self,
        node_id: str,
        action: str,
        role: Optional[str] = None,
        template_hash: Optional[str] = None,
        operator: str = "system",
        details: Optional[str] = None,
    ) -> None:
        """Log a provisioning action."""
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO provisioning_log
                   (node_id, action, role, template_hash, operator, details)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (node_id, action, role, template_hash, operator, details),
            )

    def save_config_template(
        self, role: str, yaml_content: str, config_hash: str, version: str = "1"
    ) -> None:
        """Save or update a golden config template."""
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO config_templates (role, yaml_content, config_hash, version)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(role) DO UPDATE SET
                   yaml_content = excluded.yaml_content,
                   config_hash = excluded.config_hash,
                   version = excluded.version,
                   updated_at = datetime('now')""",
                (role, yaml_content, config_hash, version),
            )

    def get_config_template(self, role: str) -> Optional[dict]:
        """Get a golden config template by role."""
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM config_templates WHERE role = ?", (role,)).fetchone()
            return dict(row) if row else None

    def prune_old_positions(self, retention_days: int = 30) -> int:
        """Delete position records older than retention_days. Returns count deleted."""
        with self.connection() as conn:
            cursor = conn.execute(
                """DELETE FROM positions
                   WHERE timestamp < datetime('now', ? || ' days')""",
                (f"-{retention_days}",),
            )
            return cursor.rowcount

    # --- Topology edge methods ---

    def upsert_topology_edge(
        self,
        from_node: str,
        to_node: str,
        *,
        snr: Optional[float] = None,
        rssi: Optional[int] = None,
    ) -> None:
        """Insert or update a directed topology edge."""
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO topology_edges (from_node, to_node, snr, rssi)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(from_node, to_node) DO UPDATE SET
                   snr = excluded.snr,
                   rssi = excluded.rssi,
                   last_updated = datetime('now')""",
                (from_node, to_node, snr, rssi),
            )

    def get_edges_for_node(self, node_id: str) -> list[dict]:
        """Get all topology edges involving a node (as source or destination)."""
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM topology_edges
                   WHERE from_node = ? OR to_node = ?
                   ORDER BY last_updated DESC""",
                (node_id, node_id),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_edges(self) -> list[dict]:
        """Get all topology edges in the mesh."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM topology_edges ORDER BY last_updated DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_edges_for_node(self, node_id: str) -> int:
        """Delete all outgoing edges from a node. Used before replacing with fresh neighbor data."""
        with self.connection() as conn:
            cursor = conn.execute("DELETE FROM topology_edges WHERE from_node = ?", (node_id,))
            return cursor.rowcount

    def prune_stale_edges(self, max_age_hours: int = 24) -> int:
        """Remove topology edges older than max_age_hours. Returns count deleted."""
        with self.connection() as conn:
            cursor = conn.execute(
                """DELETE FROM topology_edges
                   WHERE last_updated < datetime('now', ? || ' hours')""",
                (f"-{max_age_hours}",),
            )
            return cursor.rowcount
