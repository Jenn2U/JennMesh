"""SQLite WAL database for JennMesh device registry, positions, and alerts."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

SCHEMA_VERSION = 12

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
    associated_edge_node TEXT,
    last_mesh_heartbeat TEXT,
    mesh_status     TEXT NOT NULL DEFAULT 'unknown'
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

-- Telemetry history: raw samples for rolling baseline computation
CREATE TABLE IF NOT EXISTS telemetry_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id         TEXT NOT NULL,
    rssi            INTEGER,
    snr             REAL,
    battery_level   INTEGER,
    voltage         REAL,
    timestamp       TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (node_id) REFERENCES devices(node_id)
);
CREATE INDEX IF NOT EXISTS idx_telemetry_node_time ON telemetry_history(node_id, timestamp DESC);

-- Device baselines: precomputed rolling 7-day per-node performance stats
CREATE TABLE IF NOT EXISTS device_baselines (
    node_id         TEXT PRIMARY KEY,
    rssi_mean       REAL,
    rssi_stddev     REAL,
    snr_mean        REAL,
    snr_stddev      REAL,
    battery_drain_rate REAL,
    sample_count    INTEGER NOT NULL DEFAULT 0,
    window_start    TEXT,
    window_end      TEXT,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (node_id) REFERENCES devices(node_id)
);

-- Firmware compatibility matrix: hardware-firmware compatibility tracking
CREATE TABLE IF NOT EXISTS firmware_compat (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    hw_model        TEXT NOT NULL,
    firmware_version TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'UNTESTED',
    notes           TEXT,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(hw_model, firmware_version)
);
CREATE INDEX IF NOT EXISTS idx_compat_hw ON firmware_compat(hw_model);

-- Mesh heartbeats: edge node heartbeats received via LoRa radio text messages
CREATE TABLE IF NOT EXISTS mesh_heartbeats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id         TEXT NOT NULL,
    uptime_seconds  INTEGER NOT NULL,
    services        TEXT NOT NULL,
    battery         INTEGER NOT NULL DEFAULT -1,
    rssi            INTEGER,
    snr             REAL,
    timestamp       TEXT NOT NULL,
    received_at     TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (node_id) REFERENCES devices(node_id)
);
CREATE INDEX IF NOT EXISTS idx_heartbeat_node_time ON mesh_heartbeats(node_id, received_at DESC);

-- Emergency broadcasts: operator-initiated alerts sent over mesh radio
CREATE TABLE IF NOT EXISTS emergency_broadcasts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    broadcast_type  TEXT NOT NULL,
    message         TEXT NOT NULL,
    sender          TEXT NOT NULL DEFAULT 'dashboard',
    channel_index   INTEGER NOT NULL DEFAULT 3,
    status          TEXT NOT NULL DEFAULT 'pending',
    confirmed       INTEGER NOT NULL DEFAULT 0,
    mesh_received   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    sent_at         TEXT,
    delivered_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_emergency_status ON emergency_broadcasts(status, created_at DESC);

-- Recovery commands: remote recovery actions sent to edge nodes via LoRa mesh
CREATE TABLE IF NOT EXISTS recovery_commands (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target_node_id  TEXT NOT NULL,
    command_type    TEXT NOT NULL,
    args            TEXT NOT NULL DEFAULT '',
    nonce           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    confirmed       INTEGER NOT NULL DEFAULT 0,
    sender          TEXT NOT NULL DEFAULT 'dashboard',
    result_message  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    sent_at         TEXT,
    completed_at    TEXT,
    expires_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recovery_status ON recovery_commands(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recovery_node ON recovery_commands(target_node_id, created_at DESC);

-- Config queue: store-and-forward outbox for offline radio config pushes
CREATE TABLE IF NOT EXISTS config_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target_node_id  TEXT NOT NULL,
    template_role   TEXT NOT NULL,
    config_hash     TEXT NOT NULL,
    yaml_content    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    retry_count     INTEGER NOT NULL DEFAULT 0,
    max_retries     INTEGER NOT NULL DEFAULT 10,
    last_error      TEXT,
    source_push_id  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    next_retry_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_retry_at   TEXT,
    delivered_at    TEXT,
    escalated_at    TEXT,
    FOREIGN KEY (target_node_id) REFERENCES devices(node_id)
);
CREATE INDEX IF NOT EXISTS idx_config_queue_status
    ON config_queue(status, next_retry_at ASC);
CREATE INDEX IF NOT EXISTS idx_config_queue_node
    ON config_queue(target_node_id, created_at DESC);

-- Failover events: track each failover activation lifecycle
CREATE TABLE IF NOT EXISTS failover_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    failed_node_id  TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    dependent_nodes TEXT NOT NULL DEFAULT '[]',
    operator        TEXT NOT NULL DEFAULT 'dashboard',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    reverted_at     TEXT,
    cancelled_at    TEXT,
    FOREIGN KEY (failed_node_id) REFERENCES devices(node_id)
);
CREATE INDEX IF NOT EXISTS idx_failover_events_status
    ON failover_events(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_failover_events_node
    ON failover_events(failed_node_id);

-- Failover compensations: individual config changes applied to compensation nodes
CREATE TABLE IF NOT EXISTS failover_compensations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    failover_event_id INTEGER NOT NULL,
    comp_node_id    TEXT NOT NULL,
    comp_type       TEXT NOT NULL,
    config_key      TEXT NOT NULL,
    original_value  TEXT NOT NULL,
    new_value       TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    applied_at      TEXT,
    reverted_at     TEXT,
    error           TEXT,
    FOREIGN KEY (failover_event_id) REFERENCES failover_events(id),
    FOREIGN KEY (comp_node_id) REFERENCES devices(node_id)
);
CREATE INDEX IF NOT EXISTS idx_failover_comp_event
    ON failover_compensations(failover_event_id);
CREATE INDEX IF NOT EXISTS idx_failover_comp_node
    ON failover_compensations(comp_node_id);

-- Watchdog runs: audit trail for periodic health checks
CREATE TABLE IF NOT EXISTS watchdog_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    check_name      TEXT NOT NULL,
    started_at      TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at    TEXT,
    result_summary  TEXT,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_watchdog_runs_check
    ON watchdog_runs(check_name, started_at DESC);

-- Config snapshots: pre-push config capture for OTA rollback
CREATE TABLE IF NOT EXISTS config_snapshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id           TEXT NOT NULL,
    push_source       TEXT NOT NULL,
    yaml_before       TEXT,
    yaml_after        TEXT,
    status            TEXT NOT NULL DEFAULT 'active',
    monitoring_until  TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    push_completed_at TEXT,
    rolled_back_at    TEXT,
    confirmed_at      TEXT,
    error             TEXT,
    FOREIGN KEY (node_id) REFERENCES devices(node_id)
);
CREATE INDEX IF NOT EXISTS idx_config_snapshots_node
    ON config_snapshots(node_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_config_snapshots_status
    ON config_snapshots(status);

-- CRDT sync queue: pending sync payloads to fragment and send over LoRa
CREATE TABLE IF NOT EXISTS crdt_sync_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id         TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    direction       TEXT NOT NULL,
    priority        INTEGER NOT NULL DEFAULT 2,
    payload_json    TEXT NOT NULL,
    total_fragments INTEGER,
    acked_fragments INTEGER DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at    TEXT,
    error           TEXT,
    FOREIGN KEY (node_id) REFERENCES devices(node_id)
);
CREATE INDEX IF NOT EXISTS idx_sync_queue_status
    ON crdt_sync_queue(status, priority);
CREATE INDEX IF NOT EXISTS idx_sync_queue_node
    ON crdt_sync_queue(node_id, created_at DESC);

-- CRDT sync fragments: individual LoRa-sized chunks of sync payloads
CREATE TABLE IF NOT EXISTS crdt_sync_fragments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    total           INTEGER NOT NULL,
    direction       TEXT NOT NULL,
    payload_b64     TEXT NOT NULL,
    crc16           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    send_attempts   INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    acked_at        TEXT,
    UNIQUE(session_id, seq, direction)
);
CREATE INDEX IF NOT EXISTS idx_sync_fragments_session
    ON crdt_sync_fragments(session_id, seq);

-- CRDT sync log: audit trail for sync exchanges
CREATE TABLE IF NOT EXISTS crdt_sync_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id         TEXT NOT NULL,
    session_id      TEXT,
    direction       TEXT NOT NULL,
    items_synced    INTEGER DEFAULT 0,
    items_failed    INTEGER DEFAULT 0,
    bytes_sent      INTEGER DEFAULT 0,
    bytes_received  INTEGER DEFAULT 0,
    duration_ms     INTEGER,
    sv_hash_before  TEXT,
    sv_hash_after   TEXT,
    status          TEXT NOT NULL DEFAULT 'started',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at    TEXT,
    error           TEXT,
    FOREIGN KEY (node_id) REFERENCES devices(node_id)
);
CREATE INDEX IF NOT EXISTS idx_sync_log_node
    ON crdt_sync_log(node_id, created_at DESC);

-- Geofences: virtual boundary zones for mesh node tracking
CREATE TABLE IF NOT EXISTS geofences (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    fence_type  TEXT NOT NULL DEFAULT 'circle',
    center_lat  REAL,
    center_lon  REAL,
    radius_m    REAL,
    polygon_json TEXT,
    node_filter TEXT,
    trigger_on  TEXT NOT NULL DEFAULT 'exit',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT
);

-- Coverage samples: aggregated RSSI observations at geographic locations
CREATE TABLE IF NOT EXISTS coverage_samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node   TEXT NOT NULL,
    to_node     TEXT NOT NULL,
    latitude    REAL NOT NULL,
    longitude   REAL NOT NULL,
    rssi        REAL NOT NULL,
    snr         REAL,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (from_node) REFERENCES devices(node_id)
);
CREATE INDEX IF NOT EXISTS idx_coverage_location ON coverage_samples(latitude, longitude);
CREATE INDEX IF NOT EXISTS idx_coverage_time ON coverage_samples(timestamp DESC);

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
                    # Migrations are idempotent (CREATE TABLE IF NOT EXISTS)
                    # v1 → v2: topology_edges table
                    # v2 → v3: telemetry_history, device_baselines, firmware_compat
                    # v3 → v4: mesh_heartbeats table, devices.last_mesh_heartbeat,
                    #           devices.mesh_status
                    # v4 → v5: emergency_broadcasts table (CREATE IF NOT EXISTS only)
                    # v5 → v6: recovery_commands table (CREATE IF NOT EXISTS only)
                    # v6 → v7: config_queue table (CREATE IF NOT EXISTS only)
                    # v7 → v8: failover_events + failover_compensations (CREATE IF NOT EXISTS only)
                    # v8 → v9: watchdog_runs table (CREATE IF NOT EXISTS only)
                    # v9 → v10: config_snapshots table (CREATE IF NOT EXISTS only)
                    # v10 → v11: crdt_sync_queue, crdt_sync_fragments, crdt_sync_log
                    #            (CREATE IF NOT EXISTS only)
                    # v11 → v12: geofences, coverage_samples (CREATE IF NOT EXISTS only)
                    if current_version < 4:
                        # Add new columns (safe: ALTER TABLE ADD COLUMN is idempotent-ish,
                        # but we guard with version check to avoid "duplicate column" errors)
                        try:
                            conn.execute("ALTER TABLE devices ADD COLUMN last_mesh_heartbeat TEXT")
                        except sqlite3.OperationalError:
                            pass  # Column already exists
                        try:
                            conn.execute(
                                "ALTER TABLE devices ADD COLUMN mesh_status"
                                " TEXT NOT NULL DEFAULT 'unknown'"
                            )
                        except sqlite3.OperationalError:
                            pass  # Column already exists
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
        last_mesh_heartbeat: Optional[str] = None,
        mesh_status: Optional[str] = None,
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
                       last_seen, associated_edge_node,
                       last_mesh_heartbeat, mesh_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                        last_mesh_heartbeat,
                        mesh_status or "unknown",
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
                    "last_mesh_heartbeat": last_mesh_heartbeat,
                    "mesh_status": mesh_status,
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

    def get_provisioning_log_for_node(
        self,
        node_id: str,
        action_filter: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict]:
        """Get recent provisioning log entries for a device.

        Args:
            node_id: Device to query.
            action_filter: Optional action type filter (e.g. 'drift_remediation').
            limit: Max entries to return (default 10).

        Returns:
            List of log entry dicts, newest first.
        """
        with self.connection() as conn:
            if action_filter:
                rows = conn.execute(
                    """SELECT * FROM provisioning_log
                       WHERE node_id = ? AND action = ?
                       ORDER BY timestamp DESC LIMIT ?""",
                    (node_id, action_filter, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM provisioning_log
                       WHERE node_id = ?
                       ORDER BY timestamp DESC LIMIT ?""",
                    (node_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]

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

    def list_config_templates(self) -> list[dict]:
        """List all golden config templates."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT role, config_hash, version, updated_at FROM config_templates ORDER BY role"
            ).fetchall()
            return [dict(r) for r in rows]

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

    # --- Telemetry history methods ---

    def add_telemetry_sample(
        self,
        node_id: str,
        *,
        rssi: Optional[int] = None,
        snr: Optional[float] = None,
        battery_level: Optional[int] = None,
        voltage: Optional[float] = None,
        timestamp: Optional[str] = None,
    ) -> None:
        """Store a raw telemetry sample for baseline computation."""
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO telemetry_history
                   (node_id, rssi, snr, battery_level, voltage, timestamp)
                   VALUES (?, ?, ?, ?, ?, COALESCE(?, datetime('now')))""",
                (node_id, rssi, snr, battery_level, voltage, timestamp),
            )

    def get_telemetry_history(self, node_id: str, since: Optional[str] = None) -> list[dict]:
        """Get telemetry samples for a node, optionally since a timestamp."""
        with self.connection() as conn:
            if since:
                rows = conn.execute(
                    """SELECT * FROM telemetry_history
                       WHERE node_id = ? AND timestamp >= ?
                       ORDER BY timestamp ASC""",
                    (node_id, since),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM telemetry_history
                       WHERE node_id = ? ORDER BY timestamp ASC""",
                    (node_id,),
                ).fetchall()
            return [dict(r) for r in rows]

    def prune_old_telemetry(self, retention_days: int = 14) -> int:
        """Delete telemetry samples older than retention_days. Returns count deleted."""
        with self.connection() as conn:
            cursor = conn.execute(
                """DELETE FROM telemetry_history
                   WHERE timestamp < datetime('now', ? || ' days')""",
                (f"-{retention_days}",),
            )
            return cursor.rowcount

    # --- Device baseline methods ---

    def upsert_baseline(
        self,
        node_id: str,
        *,
        rssi_mean: Optional[float] = None,
        rssi_stddev: Optional[float] = None,
        snr_mean: Optional[float] = None,
        snr_stddev: Optional[float] = None,
        battery_drain_rate: Optional[float] = None,
        sample_count: int = 0,
        window_start: Optional[str] = None,
        window_end: Optional[str] = None,
    ) -> None:
        """Insert or update a device's precomputed baseline."""
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO device_baselines
                   (node_id, rssi_mean, rssi_stddev, snr_mean, snr_stddev,
                    battery_drain_rate, sample_count, window_start, window_end)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(node_id) DO UPDATE SET
                   rssi_mean = excluded.rssi_mean,
                   rssi_stddev = excluded.rssi_stddev,
                   snr_mean = excluded.snr_mean,
                   snr_stddev = excluded.snr_stddev,
                   battery_drain_rate = excluded.battery_drain_rate,
                   sample_count = excluded.sample_count,
                   window_start = excluded.window_start,
                   window_end = excluded.window_end,
                   updated_at = datetime('now')""",
                (
                    node_id,
                    rssi_mean,
                    rssi_stddev,
                    snr_mean,
                    snr_stddev,
                    battery_drain_rate,
                    sample_count,
                    window_start,
                    window_end,
                ),
            )

    def get_baseline(self, node_id: str) -> Optional[dict]:
        """Get the precomputed baseline for a device."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM device_baselines WHERE node_id = ?", (node_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_all_baselines(self) -> list[dict]:
        """Get baselines for all devices."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM device_baselines ORDER BY updated_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Firmware compatibility methods ---

    def upsert_firmware_compat(
        self,
        hw_model: str,
        firmware_version: str,
        status: str = "UNTESTED",
        notes: Optional[str] = None,
    ) -> None:
        """Insert or update a firmware-hardware compatibility entry."""
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO firmware_compat (hw_model, firmware_version, status, notes)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(hw_model, firmware_version) DO UPDATE SET
                   status = excluded.status,
                   notes = excluded.notes,
                   updated_at = datetime('now')""",
                (hw_model, firmware_version, status, notes),
            )

    def get_firmware_compat(self, hw_model: str) -> list[dict]:
        """Get all firmware compatibility entries for a hardware model."""
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM firmware_compat WHERE hw_model = ?
                   ORDER BY firmware_version DESC""",
                (hw_model,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_firmware_compat_entry(self, hw_model: str, firmware_version: str) -> Optional[dict]:
        """Get a specific firmware-hardware compatibility entry."""
        with self.connection() as conn:
            row = conn.execute(
                """SELECT * FROM firmware_compat
                   WHERE hw_model = ? AND firmware_version = ?""",
                (hw_model, firmware_version),
            ).fetchone()
            return dict(row) if row else None

    def get_all_firmware_compat(self) -> list[dict]:
        """Get the full firmware compatibility matrix."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM firmware_compat ORDER BY hw_model, firmware_version DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def seed_firmware_compat(self, entries: list[tuple[str, str, str]]) -> int:
        """Bulk-insert firmware compatibility entries. Returns count inserted."""
        count = 0
        with self.connection() as conn:
            for hw_model, firmware_version, status in entries:
                conn.execute(
                    """INSERT OR IGNORE INTO firmware_compat
                       (hw_model, firmware_version, status)
                       VALUES (?, ?, ?)""",
                    (hw_model, firmware_version, status),
                )
                count += 1
        return count

    # --- Mesh heartbeat methods ---

    def add_heartbeat(
        self,
        node_id: str,
        uptime_seconds: int,
        services_json: str,
        battery: int = -1,
        rssi: Optional[int] = None,
        snr: Optional[float] = None,
        timestamp: Optional[str] = None,
    ) -> None:
        """Store a mesh heartbeat and update the device's mesh status."""
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO mesh_heartbeats
                   (node_id, uptime_seconds, services, battery, rssi, snr, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))""",
                (node_id, uptime_seconds, services_json, battery, rssi, snr, timestamp),
            )
            # Update the device's mesh reachability
            conn.execute(
                """UPDATE devices
                   SET last_mesh_heartbeat = COALESCE(?, datetime('now')),
                       mesh_status = 'reachable'
                   WHERE node_id = ?""",
                (timestamp, node_id),
            )

    def get_latest_heartbeat(self, node_id: str) -> Optional[dict]:
        """Get the most recent heartbeat for a device."""
        with self.connection() as conn:
            row = conn.execute(
                """SELECT * FROM mesh_heartbeats WHERE node_id = ?
                   ORDER BY received_at DESC LIMIT 1""",
                (node_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_recent_heartbeats(self, minutes: int = 10) -> list[dict]:
        """Get all heartbeats received in the last N minutes."""
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM mesh_heartbeats
                   WHERE received_at >= datetime('now', ? || ' minutes')
                   ORDER BY received_at DESC""",
                (f"-{minutes}",),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_heartbeat_history(self, node_id: str, limit: int = 50) -> list[dict]:
        """Get heartbeat history for a device, most recent first."""
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM mesh_heartbeats WHERE node_id = ?
                   ORDER BY received_at DESC LIMIT ?""",
                (node_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def prune_old_heartbeats(self, retention_days: int = 7) -> int:
        """Delete heartbeat records older than retention_days. Returns count deleted."""
        with self.connection() as conn:
            cursor = conn.execute(
                """DELETE FROM mesh_heartbeats
                   WHERE received_at < datetime('now', ? || ' days')""",
                (f"-{retention_days}",),
            )
            return cursor.rowcount

    # --- Emergency broadcast methods ---

    def create_emergency_broadcast(
        self,
        broadcast_type: str,
        message: str,
        sender: str = "dashboard",
        channel_index: int = 3,
    ) -> int:
        """Create a new emergency broadcast record. Returns the broadcast ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO emergency_broadcasts
                   (broadcast_type, message, sender, channel_index, status, confirmed)
                   VALUES (?, ?, ?, ?, 'pending', 1)""",
                (broadcast_type, message, sender, channel_index),
            )
            return cursor.lastrowid or 0

    def update_broadcast_status(
        self,
        broadcast_id: int,
        status: str,
        *,
        sent_at: Optional[str] = None,
        delivered_at: Optional[str] = None,
        mesh_received: Optional[bool] = None,
    ) -> None:
        """Update the status of an emergency broadcast."""
        with self.connection() as conn:
            updates = ["status = ?"]
            values: list[object] = [status]

            if sent_at is not None:
                updates.append("sent_at = ?")
                values.append(sent_at)
            if delivered_at is not None:
                updates.append("delivered_at = ?")
                values.append(delivered_at)
            if mesh_received is not None:
                updates.append("mesh_received = ?")
                values.append(1 if mesh_received else 0)

            values.append(broadcast_id)
            conn.execute(
                f"UPDATE emergency_broadcasts SET {', '.join(updates)} WHERE id = ?",
                values,
            )

    def get_broadcast(self, broadcast_id: int) -> Optional[dict]:
        """Get a single emergency broadcast by ID."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM emergency_broadcasts WHERE id = ?",
                (broadcast_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_broadcasts(self, limit: int = 50) -> list[dict]:
        """List emergency broadcasts, most recent first."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM emergency_broadcasts ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_recent_broadcasts(self, minutes: int = 60) -> list[dict]:
        """Get emergency broadcasts from the last N minutes."""
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM emergency_broadcasts
                   WHERE created_at >= datetime('now', ? || ' minutes')
                   ORDER BY created_at DESC""",
                (f"-{minutes}",),
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Recovery command methods ---

    def create_recovery_command(
        self,
        target_node_id: str,
        command_type: str,
        args: str,
        nonce: str,
        sender: str = "dashboard",
        expires_at: str = "",
    ) -> int:
        """Create a new recovery command record. Returns the command ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO recovery_commands
                   (target_node_id, command_type, args, nonce, sender, status,
                    confirmed, expires_at)
                   VALUES (?, ?, ?, ?, ?, 'pending', 1, ?)""",
                (target_node_id, command_type, args, nonce, sender, expires_at),
            )
            return cursor.lastrowid or 0

    def update_recovery_status(
        self,
        command_id: int,
        status: str,
        *,
        result_message: Optional[str] = None,
        sent_at: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> None:
        """Update the status of a recovery command."""
        with self.connection() as conn:
            updates = ["status = ?"]
            values: list[object] = [status]

            if result_message is not None:
                updates.append("result_message = ?")
                values.append(result_message)
            if sent_at is not None:
                updates.append("sent_at = ?")
                values.append(sent_at)
            if completed_at is not None:
                updates.append("completed_at = ?")
                values.append(completed_at)

            values.append(command_id)
            conn.execute(
                f"UPDATE recovery_commands SET {', '.join(updates)} WHERE id = ?",
                values,
            )

    def get_recovery_command(self, command_id: int) -> Optional[dict]:
        """Get a single recovery command by ID."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM recovery_commands WHERE id = ?",
                (command_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_recovery_command_by_nonce(self, nonce: str) -> Optional[dict]:
        """Get a recovery command by its nonce (for ACK matching)."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM recovery_commands WHERE nonce = ? ORDER BY created_at DESC LIMIT 1",
                (nonce,),
            ).fetchone()
            return dict(row) if row else None

    def list_recovery_commands(
        self, target_node_id: Optional[str] = None, limit: int = 50
    ) -> list[dict]:
        """List recovery commands, optionally filtered by target node."""
        with self.connection() as conn:
            if target_node_id:
                rows = conn.execute(
                    """SELECT * FROM recovery_commands
                       WHERE target_node_id = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (target_node_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM recovery_commands ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_recent_recovery_commands(self, minutes: int = 60) -> list[dict]:
        """Get recovery commands from the last N minutes."""
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM recovery_commands
                   WHERE created_at >= datetime('now', ? || ' minutes')
                   ORDER BY created_at DESC""",
                (f"-{minutes}",),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Config Queue methods ──────────────────────────────────────────

    def create_config_queue_entry(
        self,
        target_node_id: str,
        template_role: str,
        config_hash: str,
        yaml_content: str,
        source_push_id: Optional[str] = None,
        max_retries: int = 10,
    ) -> int:
        """Create a config queue entry and return its ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO config_queue
                   (target_node_id, template_role, config_hash, yaml_content,
                    source_push_id, max_retries)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    target_node_id,
                    template_role,
                    config_hash,
                    yaml_content,
                    source_push_id,
                    max_retries,
                ),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def update_config_queue_status(
        self,
        entry_id: int,
        status: str,
        *,
        last_error: Optional[str] = None,
        next_retry_at: Optional[str] = None,
        last_retry_at: Optional[str] = None,
        delivered_at: Optional[str] = None,
        escalated_at: Optional[str] = None,
        retry_count: Optional[int] = None,
    ) -> None:
        """Update a config queue entry's status and optional fields."""
        updates: list[str] = ["status = ?"]
        params: list[object] = [status]
        if last_error is not None:
            updates.append("last_error = ?")
            params.append(last_error)
        if next_retry_at is not None:
            updates.append("next_retry_at = ?")
            params.append(next_retry_at)
        if last_retry_at is not None:
            updates.append("last_retry_at = ?")
            params.append(last_retry_at)
        if delivered_at is not None:
            updates.append("delivered_at = ?")
            params.append(delivered_at)
        if escalated_at is not None:
            updates.append("escalated_at = ?")
            params.append(escalated_at)
        if retry_count is not None:
            updates.append("retry_count = ?")
            params.append(retry_count)
        params.append(entry_id)
        with self.connection() as conn:
            conn.execute(
                f"UPDATE config_queue SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )

    def get_config_queue_entry(self, entry_id: int) -> Optional[dict]:
        """Get a config queue entry by ID."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM config_queue WHERE id = ?",
                (entry_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_config_queue(
        self,
        target_node_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """List config queue entries with optional filters."""
        conditions: list[str] = []
        params: list[object] = []
        if target_node_id is not None:
            conditions.append("target_node_id = ?")
            params.append(target_node_id)
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM config_queue {where} " f"ORDER BY created_at DESC LIMIT ?",
                tuple(params),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_pending_queue_entries(self, now_iso: str) -> list[dict]:
        """Get config queue entries due for retry.

        Returns entries where status is 'pending' or 'retrying'
        AND next_retry_at <= now_iso, ordered by next_retry_at ASC.
        """
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM config_queue
                   WHERE status IN ('pending', 'retrying')
                     AND next_retry_at <= ?
                   ORDER BY next_retry_at ASC""",
                (now_iso,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_config_queue_stats(self) -> dict:
        """Get aggregate config queue counts by status."""
        with self.connection() as conn:
            rows = conn.execute("""SELECT status, COUNT(*) as count
                   FROM config_queue
                   GROUP BY status""").fetchall()
            stats: dict[str, int] = {}
            for row in rows:
                stats[row["status"]] = row["count"]
            return stats

    def cancel_config_queue_entry(self, entry_id: int) -> bool:
        """Cancel a config queue entry. Returns True if entry existed."""
        with self.connection() as conn:
            cursor = conn.execute(
                """UPDATE config_queue SET status = 'cancelled'
                   WHERE id = ? AND status IN ('pending', 'retrying')""",
                (entry_id,),
            )
            return cursor.rowcount > 0

    # ── Failover events ──────────────────────────────────────────────

    def create_failover_event(
        self,
        failed_node_id: str,
        dependent_nodes: str,
        operator: str = "dashboard",
    ) -> int:
        """Create a failover event. *dependent_nodes* is a JSON array string."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO failover_events
                   (failed_node_id, dependent_nodes, operator)
                   VALUES (?, ?, ?)""",
                (failed_node_id, dependent_nodes, operator),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def get_failover_event(self, event_id: int) -> Optional[dict]:
        """Get a failover event by ID."""
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM failover_events WHERE id = ?", (event_id,)).fetchone()
            return dict(row) if row else None

    def get_active_failover_for_node(self, node_id: str) -> Optional[dict]:
        """Get the most recent active failover event for a node."""
        with self.connection() as conn:
            row = conn.execute(
                """SELECT * FROM failover_events
                   WHERE failed_node_id = ? AND status = 'active'
                   ORDER BY created_at DESC LIMIT 1""",
                (node_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_active_failover_events(self) -> list[dict]:
        """List all active failover events, newest first."""
        with self.connection() as conn:
            rows = conn.execute("""SELECT * FROM failover_events
                   WHERE status = 'active'
                   ORDER BY created_at DESC""").fetchall()
            return [dict(r) for r in rows]

    def update_failover_event_status(
        self, event_id: int, status: str, **timestamp_kwargs: Optional[str]
    ) -> None:
        """Update failover event status and optional timestamp columns.

        Example: ``update_failover_event_status(1, 'reverted', reverted_at=now_iso)``
        """
        sets = ["status = ?"]
        params: list = [status]
        for col, val in timestamp_kwargs.items():
            sets.append(f"{col} = ?")
            params.append(val)
        params.append(event_id)
        with self.connection() as conn:
            conn.execute(
                f"UPDATE failover_events SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )

    # ── Failover compensations ───────────────────────────────────────

    def create_failover_compensation(
        self,
        event_id: int,
        comp_node_id: str,
        comp_type: str,
        config_key: str,
        original_value: str,
        new_value: str,
    ) -> int:
        """Create a failover compensation record."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO failover_compensations
                   (failover_event_id, comp_node_id, comp_type, config_key,
                    original_value, new_value)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (event_id, comp_node_id, comp_type, config_key, original_value, new_value),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def get_compensations_for_event(self, event_id: int) -> list[dict]:
        """Get all compensations for a failover event."""
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM failover_compensations
                   WHERE failover_event_id = ?
                   ORDER BY id ASC""",
                (event_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_compensation_status(
        self,
        comp_id: int,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        """Update compensation status and optional error/timestamp."""
        now_iso = datetime.now(timezone.utc).isoformat()
        if status == "applied":
            with self.connection() as conn:
                conn.execute(
                    """UPDATE failover_compensations
                       SET status = ?, applied_at = ?, error = ?
                       WHERE id = ?""",
                    (status, now_iso, error, comp_id),
                )
        elif status == "reverted":
            with self.connection() as conn:
                conn.execute(
                    """UPDATE failover_compensations
                       SET status = ?, reverted_at = ?, error = ?
                       WHERE id = ?""",
                    (status, now_iso, error, comp_id),
                )
        else:
            with self.connection() as conn:
                conn.execute(
                    """UPDATE failover_compensations
                       SET status = ?, error = ?
                       WHERE id = ?""",
                    (status, error, comp_id),
                )

    # ── Watchdog runs ─────────────────────────────────────────────────

    def create_watchdog_run(self, check_name: str) -> int:
        """Record the start of a watchdog check. Returns the run ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                "INSERT INTO watchdog_runs (check_name) VALUES (?)",
                (check_name,),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def complete_watchdog_run(
        self,
        run_id: int,
        *,
        result_summary: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Mark a watchdog run as completed with optional result or error."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.connection() as conn:
            conn.execute(
                """UPDATE watchdog_runs
                   SET completed_at = ?, result_summary = ?, error = ?
                   WHERE id = ?""",
                (now_iso, result_summary, error, run_id),
            )

    def get_recent_watchdog_runs(
        self,
        check_name: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict]:
        """Fetch recent watchdog runs, optionally filtered by check name."""
        with self.connection() as conn:
            if check_name:
                rows = conn.execute(
                    """SELECT * FROM watchdog_runs
                       WHERE check_name = ?
                       ORDER BY started_at DESC LIMIT ?""",
                    (check_name, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM watchdog_runs
                       ORDER BY started_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    # ── Config snapshots (OTA rollback) ────────────────────────────────

    def create_config_snapshot(
        self,
        node_id: str,
        push_source: str,
        yaml_before: Optional[str] = None,
    ) -> int:
        """Create a config snapshot before a push. Returns the snapshot ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO config_snapshots (node_id, push_source, yaml_before)
                   VALUES (?, ?, ?)""",
                (node_id, push_source, yaml_before),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def update_config_snapshot(self, snapshot_id: int, **kwargs: object) -> None:
        """Update config snapshot fields by ID.

        Accepts any column name as keyword argument (status, yaml_after,
        push_completed_at, monitoring_until, rolled_back_at, confirmed_at, error).
        """
        if not kwargs:
            return
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [snapshot_id]
        with self.connection() as conn:
            conn.execute(
                f"UPDATE config_snapshots SET {set_clause} WHERE id = ?",
                values,
            )

    def get_config_snapshot(self, snapshot_id: int) -> Optional[dict]:
        """Fetch a single config snapshot by ID."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM config_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_snapshots_for_node(self, node_id: str, limit: int = 20) -> list[dict]:
        """Fetch recent config snapshots for a specific node."""
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM config_snapshots
                   WHERE node_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (node_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_monitoring_snapshots(self) -> list[dict]:
        """Fetch all snapshots in 'monitoring' status.

        Returns every snapshot where status='monitoring' regardless of
        whether the monitoring window has expired.  The caller
        (``ConfigRollbackManager._should_rollback``) decides whether to
        wait, confirm, or rollback based on the window.
        """
        with self.connection() as conn:
            rows = conn.execute("""SELECT * FROM config_snapshots
                   WHERE status = 'monitoring'
                   ORDER BY push_completed_at ASC""").fetchall()
            return [dict(r) for r in rows]

    def get_recent_snapshots(self, limit: int = 50) -> list[dict]:
        """Fetch the most recent config snapshots across all nodes."""
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM config_snapshots
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── CRDT sync queue ─────────────────────────────────────────────

    def create_sync_queue_entry(
        self,
        node_id: str,
        session_id: str,
        direction: str,
        payload_json: str,
        *,
        priority: int = 2,
        total_fragments: Optional[int] = None,
    ) -> int:
        """Create a sync queue entry for a pending sync payload. Returns the entry ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO crdt_sync_queue
                   (node_id, session_id, direction, priority, payload_json, total_fragments)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (node_id, session_id, direction, priority, payload_json, total_fragments),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def update_sync_queue_entry(self, entry_id: int, **kwargs: object) -> None:
        """Update sync queue entry fields by ID."""
        if not kwargs:
            return
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [entry_id]
        with self.connection() as conn:
            conn.execute(
                f"UPDATE crdt_sync_queue SET {set_clause} WHERE id = ?",
                values,
            )

    def get_sync_queue_entry(self, entry_id: int) -> Optional[dict]:
        """Fetch a single sync queue entry by ID."""
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM crdt_sync_queue WHERE id = ?", (entry_id,)).fetchone()
            return dict(row) if row else None

    def get_pending_sync_entries(self, node_id: Optional[str] = None) -> list[dict]:
        """Fetch sync queue entries with status 'pending' or 'sending', ordered by priority."""
        with self.connection() as conn:
            if node_id:
                rows = conn.execute(
                    """SELECT * FROM crdt_sync_queue
                       WHERE node_id = ? AND status IN ('pending', 'sending')
                       ORDER BY priority ASC, created_at ASC""",
                    (node_id,),
                ).fetchall()
            else:
                rows = conn.execute("""SELECT * FROM crdt_sync_queue
                       WHERE status IN ('pending', 'sending')
                       ORDER BY priority ASC, created_at ASC""").fetchall()
            return [dict(r) for r in rows]

    # ── CRDT sync fragments ─────────────────────────────────────────

    def create_sync_fragment(
        self,
        session_id: str,
        seq: int,
        total: int,
        direction: str,
        payload_b64: str,
        crc16: str,
    ) -> int:
        """Create a sync fragment record. Returns the fragment ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO crdt_sync_fragments
                   (session_id, seq, total, direction, payload_b64, crc16)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, seq, total, direction, payload_b64, crc16),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def update_sync_fragment(self, fragment_id: int, **kwargs: object) -> None:
        """Update sync fragment fields by ID."""
        if not kwargs:
            return
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [fragment_id]
        with self.connection() as conn:
            conn.execute(
                f"UPDATE crdt_sync_fragments SET {set_clause} WHERE id = ?",
                values,
            )

    def get_fragments_for_session(
        self, session_id: str, direction: Optional[str] = None
    ) -> list[dict]:
        """Fetch all fragments for a session, ordered by sequence number."""
        with self.connection() as conn:
            if direction:
                rows = conn.execute(
                    """SELECT * FROM crdt_sync_fragments
                       WHERE session_id = ? AND direction = ?
                       ORDER BY seq ASC""",
                    (session_id, direction),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM crdt_sync_fragments
                       WHERE session_id = ?
                       ORDER BY seq ASC""",
                    (session_id,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_pending_fragments(self, session_id: str) -> list[dict]:
        """Fetch unsent/nacked fragments for a session, ordered by sequence."""
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM crdt_sync_fragments
                   WHERE session_id = ? AND status IN ('pending', 'nacked')
                   ORDER BY seq ASC""",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── CRDT sync log ───────────────────────────────────────────────

    def create_sync_log(
        self,
        node_id: str,
        direction: str,
        *,
        session_id: Optional[str] = None,
        sv_hash_before: Optional[str] = None,
    ) -> int:
        """Create a sync log entry. Returns the log entry ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO crdt_sync_log
                   (node_id, direction, session_id, sv_hash_before)
                   VALUES (?, ?, ?, ?)""",
                (node_id, direction, session_id, sv_hash_before),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def update_sync_log(self, log_id: int, **kwargs: object) -> None:
        """Update sync log entry fields by ID."""
        if not kwargs:
            return
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [log_id]
        with self.connection() as conn:
            conn.execute(
                f"UPDATE crdt_sync_log SET {set_clause} WHERE id = ?",
                values,
            )

    def get_sync_log(self, log_id: int) -> Optional[dict]:
        """Fetch a single sync log entry by ID."""
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM crdt_sync_log WHERE id = ?", (log_id,)).fetchone()
            return dict(row) if row else None

    def get_sync_log_for_node(self, node_id: str, limit: int = 20) -> list[dict]:
        """Fetch recent sync log entries for a specific node."""
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM crdt_sync_log
                   WHERE node_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (node_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Geofences ────────────────────────────────────────────────────

    def create_geofence(
        self,
        name: str,
        fence_type: str = "circle",
        center_lat: Optional[float] = None,
        center_lon: Optional[float] = None,
        radius_m: Optional[float] = None,
        polygon_json: Optional[str] = None,
        node_filter: Optional[str] = None,
        trigger_on: str = "exit",
        enabled: bool = True,
    ) -> int:
        """Create a geofence zone. Returns the fence ID.

        For circles: provide center_lat, center_lon, radius_m.
        For polygons: provide polygon_json (JSON array of [lat, lon] pairs).
        node_filter: JSON array of node_ids, or None for all nodes.
        """
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO geofences
                   (name, fence_type, center_lat, center_lon, radius_m,
                    polygon_json, node_filter, trigger_on, enabled)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    name,
                    fence_type,
                    center_lat,
                    center_lon,
                    radius_m,
                    polygon_json,
                    node_filter,
                    trigger_on,
                    1 if enabled else 0,
                ),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def update_geofence(self, fence_id: int, **kwargs: object) -> bool:
        """Update geofence fields by ID. Returns True if fence existed."""
        if not kwargs:
            return False
        # Always set updated_at
        kwargs["updated_at"] = datetime.now(timezone.utc).isoformat()
        # Convert boolean 'enabled' to int for SQLite
        if "enabled" in kwargs:
            kwargs["enabled"] = 1 if kwargs["enabled"] else 0
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [fence_id]
        with self.connection() as conn:
            cursor = conn.execute(
                f"UPDATE geofences SET {set_clause} WHERE id = ?",
                values,
            )
            return cursor.rowcount > 0

    def get_geofence(self, fence_id: int) -> Optional[dict]:
        """Get a single geofence by ID."""
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM geofences WHERE id = ?", (fence_id,)).fetchone()
            return dict(row) if row else None

    def list_geofences(self, enabled_only: bool = False) -> list[dict]:
        """List all geofences, optionally filtered to enabled only."""
        with self.connection() as conn:
            if enabled_only:
                rows = conn.execute(
                    "SELECT * FROM geofences WHERE enabled = 1 ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM geofences ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

    def delete_geofence(self, fence_id: int) -> bool:
        """Delete a geofence by ID. Returns True if fence existed."""
        with self.connection() as conn:
            cursor = conn.execute("DELETE FROM geofences WHERE id = ?", (fence_id,))
            return cursor.rowcount > 0

    # ── Coverage samples ─────────────────────────────────────────────

    def add_coverage_sample(
        self,
        from_node: str,
        to_node: str,
        latitude: float,
        longitude: float,
        rssi: float,
        snr: Optional[float] = None,
        timestamp: Optional[str] = None,
    ) -> int:
        """Record a signal observation at a location. Returns the sample ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO coverage_samples
                   (from_node, to_node, latitude, longitude, rssi, snr, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))""",
                (from_node, to_node, latitude, longitude, rssi, snr, timestamp),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def get_coverage_in_bounds(
        self,
        min_lat: float,
        max_lat: float,
        min_lon: float,
        max_lon: float,
        limit: int = 10000,
    ) -> list[dict]:
        """Get coverage samples within a geographic bounding box."""
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM coverage_samples
                   WHERE latitude BETWEEN ? AND ?
                   AND longitude BETWEEN ? AND ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (min_lat, max_lat, min_lon, max_lon, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_coverage_stats(self) -> dict:
        """Get fleet-wide coverage statistics."""
        with self.connection() as conn:
            row = conn.execute("""SELECT COUNT(*) as total_samples,
                          AVG(rssi) as avg_rssi,
                          MIN(rssi) as min_rssi,
                          MAX(rssi) as max_rssi,
                          MAX(timestamp) as last_sample_at
                   FROM coverage_samples""").fetchone()
            return dict(row) if row else {}

    def get_coverage_for_node(self, node_id: str, limit: int = 500) -> list[dict]:
        """Get coverage samples involving a specific node (as sender or receiver)."""
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM coverage_samples
                   WHERE from_node = ? OR to_node = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (node_id, node_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def prune_old_coverage(self, days: int = 30) -> int:
        """Delete coverage samples older than N days. Returns count deleted."""
        with self.connection() as conn:
            cursor = conn.execute(
                """DELETE FROM coverage_samples
                   WHERE timestamp < datetime('now', ?)""",
                (f"-{days} days",),
            )
            return cursor.rowcount
