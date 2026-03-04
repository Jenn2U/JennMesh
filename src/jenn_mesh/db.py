"""SQLite WAL database for JennMesh device registry, positions, and alerts."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

SCHEMA_VERSION = 16

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

-- Environmental telemetry: temperature, humidity, pressure, air quality
CREATE TABLE IF NOT EXISTS env_telemetry (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     TEXT NOT NULL,
    temperature REAL,
    humidity    REAL,
    pressure    REAL,
    air_quality INTEGER,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (node_id) REFERENCES devices(node_id)
);
CREATE INDEX IF NOT EXISTS idx_env_node_time ON env_telemetry(node_id, timestamp DESC);

-- Webhooks: external HTTP POST targets for fleet event notifications
CREATE TABLE IF NOT EXISTS webhooks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    url             TEXT NOT NULL,
    secret          TEXT NOT NULL DEFAULT '',
    event_types     TEXT NOT NULL DEFAULT '[]',
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT
);

-- Webhook deliveries: delivery attempt log with retry tracking
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    webhook_id      INTEGER NOT NULL,
    event_type      TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    http_status     INTEGER,
    response_body   TEXT,
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 5,
    next_retry_at   TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    delivered_at    TEXT,
    last_error      TEXT,
    FOREIGN KEY (webhook_id) REFERENCES webhooks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_status
    ON webhook_deliveries(status, next_retry_at ASC);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_webhook
    ON webhook_deliveries(webhook_id, created_at DESC);

-- Notification channels: Slack, Teams, Email, Webhook channel configs
CREATE TABLE IF NOT EXISTS notification_channels (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    channel_type    TEXT NOT NULL,
    config_json     TEXT NOT NULL DEFAULT '{}',
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT
);

-- Notification rules: alert type/severity → channel routing
CREATE TABLE IF NOT EXISTS notification_rules (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    alert_types     TEXT NOT NULL DEFAULT '[]',
    severities      TEXT NOT NULL DEFAULT '[]',
    channel_ids     TEXT NOT NULL DEFAULT '[]',
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT
);

-- Partition events: network split/merge history with topology snapshots
CREATE TABLE IF NOT EXISTS partition_events (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type               TEXT NOT NULL,
    component_count          INTEGER NOT NULL DEFAULT 0,
    components_json          TEXT NOT NULL DEFAULT '[]',
    previous_component_count INTEGER,
    relay_recommendation     TEXT,
    topology_before          TEXT,
    topology_after           TEXT,
    created_at               TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at              TEXT
);
CREATE INDEX IF NOT EXISTS idx_partition_events_time
    ON partition_events(created_at DESC);

-- Bulk operations: batch fleet actions with progress tracking
CREATE TABLE IF NOT EXISTS bulk_operations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_type  TEXT NOT NULL,
    target_filter   TEXT NOT NULL DEFAULT '{}',
    target_node_ids TEXT NOT NULL DEFAULT '[]',
    parameters      TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'pending',
    total_targets   INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    failed_count    INTEGER NOT NULL DEFAULT 0,
    skipped_count   INTEGER NOT NULL DEFAULT 0,
    result_json     TEXT,
    operator        TEXT NOT NULL DEFAULT 'dashboard',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    started_at      TEXT,
    completed_at    TEXT,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_bulk_ops_status
    ON bulk_operations(status, created_at DESC);

-- v0.7.0: Team communication messages
CREATE TABLE IF NOT EXISTS team_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel         TEXT NOT NULL DEFAULT 'broadcast',
    sender          TEXT NOT NULL,
    recipient       TEXT,
    message         TEXT NOT NULL,
    mesh_channel_index INTEGER NOT NULL DEFAULT 2,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    sent_at         TEXT,
    delivered_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_messages_channel_time
    ON team_messages(channel, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_team_messages_status
    ON team_messages(status, created_at DESC);

-- v0.7.0: TAK gateway configuration and event log
CREATE TABLE IF NOT EXISTS tak_config (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    host            TEXT NOT NULL,
    port            INTEGER NOT NULL DEFAULT 8087,
    use_tls         INTEGER NOT NULL DEFAULT 0,
    callsign_prefix TEXT NOT NULL DEFAULT 'JENN-',
    stale_timeout_seconds INTEGER NOT NULL DEFAULT 600,
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tak_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    uid             TEXT NOT NULL,
    cot_type        TEXT NOT NULL DEFAULT 'a-f-G',
    callsign        TEXT NOT NULL,
    node_id         TEXT NOT NULL,
    direction       TEXT NOT NULL DEFAULT 'outbound',
    latitude        REAL,
    longitude       REAL,
    altitude        REAL,
    raw_xml         TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (node_id) REFERENCES devices(node_id)
);
CREATE INDEX IF NOT EXISTS idx_tak_events_node_time
    ON tak_events(node_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tak_events_direction
    ON tak_events(direction, created_at DESC);

-- v0.7.0: Asset tracking
CREATE TABLE IF NOT EXISTS assets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    asset_type      TEXT NOT NULL DEFAULT 'equipment',
    node_id         TEXT NOT NULL,
    zone            TEXT,
    team            TEXT,
    project         TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    metadata_json   TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (node_id) REFERENCES devices(node_id)
);
CREATE INDEX IF NOT EXISTS idx_assets_node ON assets(node_id);
CREATE INDEX IF NOT EXISTS idx_assets_type ON assets(asset_type);
CREATE INDEX IF NOT EXISTS idx_assets_zone ON assets(zone);

-- v0.7.0: JennEdge cross-reference associations
CREATE TABLE IF NOT EXISTS edge_associations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    edge_device_id  TEXT NOT NULL UNIQUE,
    node_id         TEXT NOT NULL,
    edge_hostname   TEXT,
    edge_ip         TEXT,
    association_type TEXT NOT NULL DEFAULT 'co-located',
    status          TEXT NOT NULL DEFAULT 'active',
    last_verified   TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (node_id) REFERENCES devices(node_id)
);
CREATE INDEX IF NOT EXISTS idx_edge_assoc_node ON edge_associations(node_id);
CREATE INDEX IF NOT EXISTS idx_edge_assoc_status ON edge_associations(status);

-- v0.8.0: Natural language fleet query log (MESH-046)
CREATE TABLE IF NOT EXISTS nl_query_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    question        TEXT NOT NULL,
    query_plan_json TEXT,
    result_summary  TEXT,
    source          TEXT NOT NULL DEFAULT 'unknown',
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    ollama_available INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_nl_query_log_time ON nl_query_log(created_at DESC);

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
                    # v12 → v13: env_telemetry (CREATE IF NOT EXISTS only)
                    # v13 → v14: webhooks, webhook_deliveries, notification_channels,
                    #            notification_rules, partition_events, bulk_operations
                    # v14 → v15: team_messages, tak_config, tak_events, assets,
                    #            edge_associations (v0.7.0 Field Operations & Interop)
                    #            (CREATE IF NOT EXISTS only)
                    # v15 → v16: nl_query_log (v0.8.0 MESH-046 NL Fleet Queries)
                    #            (CREATE IF NOT EXISTS only)
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
                       ORDER BY timestamp DESC, id DESC LIMIT ?""",
                    (node_id, action_filter, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM provisioning_log
                       WHERE node_id = ?
                       ORDER BY timestamp DESC, id DESC LIMIT ?""",
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
                   ORDER BY timestamp DESC, id DESC LIMIT ?""",
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
                   ORDER BY timestamp DESC, id DESC LIMIT ?""",
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

    # ── Environmental Telemetry (v0.5.0 / Schema v13) ─────────────

    def add_env_reading(
        self,
        node_id: str,
        temperature: Optional[float] = None,
        humidity: Optional[float] = None,
        pressure: Optional[float] = None,
        air_quality: Optional[int] = None,
        timestamp: Optional[str] = None,
    ) -> int:
        """Record an environmental sensor reading. Returns the reading ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO env_telemetry
                   (node_id, temperature, humidity, pressure, air_quality, timestamp)
                   VALUES (?, ?, ?, ?, ?, COALESCE(?, datetime('now')))""",
                (node_id, temperature, humidity, pressure, air_quality, timestamp),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def get_env_readings(
        self,
        node_id: str,
        since: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Get environmental readings for a node, optionally filtered by time."""
        with self.connection() as conn:
            if since:
                rows = conn.execute(
                    """SELECT * FROM env_telemetry
                       WHERE node_id = ? AND timestamp >= ?
                       ORDER BY timestamp DESC, id DESC LIMIT ?""",
                    (node_id, since, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM env_telemetry
                       WHERE node_id = ?
                       ORDER BY timestamp DESC, id DESC LIMIT ?""",
                    (node_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_fleet_env_summary(self) -> dict:
        """Get fleet-wide environmental summary (latest reading per node)."""
        with self.connection() as conn:
            rows = conn.execute("""SELECT e.*
                   FROM env_telemetry e
                   INNER JOIN (
                       SELECT node_id, MAX(timestamp) as max_ts
                       FROM env_telemetry
                       GROUP BY node_id
                   ) latest ON e.node_id = latest.node_id
                   AND e.timestamp = latest.max_ts""").fetchall()
            readings = [dict(r) for r in rows]

            # Compute fleet-wide aggregates
            temps = [r["temperature"] for r in readings if r.get("temperature") is not None]
            humidities = [r["humidity"] for r in readings if r.get("humidity") is not None]
            pressures = [r["pressure"] for r in readings if r.get("pressure") is not None]

            return {
                "node_count": len(readings),
                "readings": readings,
                "avg_temperature": round(sum(temps) / len(temps), 1) if temps else None,
                "avg_humidity": round(sum(humidities) / len(humidities), 1) if humidities else None,
                "avg_pressure": round(sum(pressures) / len(pressures), 1) if pressures else None,
            }

    def get_env_alerts(self, limit: int = 50) -> list[dict]:
        """Get recent environmental threshold alerts."""
        alerts = self.get_active_alerts()
        env_alerts = [a for a in alerts if a.get("alert_type") == "env_threshold_exceeded"]
        return env_alerts[:limit]

    def prune_old_env_readings(self, days: int = 30) -> int:
        """Delete env telemetry readings older than N days. Returns count deleted."""
        with self.connection() as conn:
            cursor = conn.execute(
                """DELETE FROM env_telemetry
                   WHERE timestamp < datetime('now', ?)""",
                (f"-{days} days",),
            )
            return cursor.rowcount

    # ── Webhook CRUD ──────────────────────────────────────────────────

    def create_webhook(
        self,
        name: str,
        url: str,
        secret: str = "",
        event_types: Optional[str] = None,
    ) -> int:
        """Create a webhook target. Returns the new webhook ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO webhooks (name, url, secret, event_types)
                   VALUES (?, ?, ?, ?)""",
                (name, url, secret, event_types or "[]"),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def get_webhook(self, webhook_id: int) -> Optional[dict]:
        """Get a single webhook by ID."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM webhooks WHERE id = ?", (webhook_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_webhooks(self, active_only: bool = False) -> list[dict]:
        """List all webhooks, optionally filtering to active only."""
        with self.connection() as conn:
            if active_only:
                rows = conn.execute(
                    "SELECT * FROM webhooks WHERE is_active = 1 ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM webhooks ORDER BY created_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def update_webhook(self, webhook_id: int, **kwargs: object) -> bool:
        """Update webhook fields. Returns True if updated."""
        allowed = {"name", "url", "secret", "event_types", "is_active"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return False
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [webhook_id]
        with self.connection() as conn:
            cursor = conn.execute(
                f"UPDATE webhooks SET {set_clause} WHERE id = ?", values
            )
            return cursor.rowcount > 0

    def delete_webhook(self, webhook_id: int) -> bool:
        """Delete a webhook and its deliveries (CASCADE). Returns True if deleted."""
        with self.connection() as conn:
            cursor = conn.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,))
            return cursor.rowcount > 0

    # ── Webhook Delivery CRUD ─────────────────────────────────────────

    def create_webhook_delivery(
        self,
        webhook_id: int,
        event_type: str,
        payload_json: str,
        max_attempts: int = 5,
    ) -> int:
        """Create a pending webhook delivery. Returns the delivery ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO webhook_deliveries
                   (webhook_id, event_type, payload_json, max_attempts)
                   VALUES (?, ?, ?, ?)""",
                (webhook_id, event_type, payload_json, max_attempts),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def get_pending_webhook_deliveries(self, limit: int = 50) -> list[dict]:
        """Get pending deliveries ready for retry."""
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT wd.*, w.url, w.secret
                   FROM webhook_deliveries wd
                   JOIN webhooks w ON wd.webhook_id = w.id
                   WHERE wd.status IN ('pending', 'retrying')
                     AND (wd.next_retry_at IS NULL
                          OR wd.next_retry_at <= datetime('now'))
                     AND wd.attempt_count < wd.max_attempts
                   ORDER BY wd.created_at ASC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_webhook_delivery(
        self,
        delivery_id: int,
        *,
        status: Optional[str] = None,
        http_status: Optional[int] = None,
        response_body: Optional[str] = None,
        next_retry_at: Optional[str] = None,
        last_error: Optional[str] = None,
        delivered_at: Optional[str] = None,
        increment_attempt: bool = False,
    ) -> bool:
        """Update a delivery record after an attempt."""
        updates: list[str] = []
        values: list[object] = []
        if status is not None:
            updates.append("status = ?")
            values.append(status)
        if http_status is not None:
            updates.append("http_status = ?")
            values.append(http_status)
        if response_body is not None:
            updates.append("response_body = ?")
            values.append(response_body[:2000])
        if next_retry_at is not None:
            updates.append("next_retry_at = ?")
            values.append(next_retry_at)
        if last_error is not None:
            updates.append("last_error = ?")
            values.append(last_error[:1000])
        if delivered_at is not None:
            updates.append("delivered_at = ?")
            values.append(delivered_at)
        if increment_attempt:
            updates.append("attempt_count = attempt_count + 1")
        if not updates:
            return False
        values.append(delivery_id)
        with self.connection() as conn:
            cursor = conn.execute(
                f"UPDATE webhook_deliveries SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            return cursor.rowcount > 0

    def list_webhook_deliveries(
        self, webhook_id: int, limit: int = 50
    ) -> list[dict]:
        """List deliveries for a specific webhook."""
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM webhook_deliveries
                   WHERE webhook_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (webhook_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def prune_old_webhook_deliveries(self, days: int = 30) -> int:
        """Delete webhook deliveries older than N days."""
        with self.connection() as conn:
            cursor = conn.execute(
                """DELETE FROM webhook_deliveries
                   WHERE created_at < datetime('now', ?)""",
                (f"-{days} days",),
            )
            return cursor.rowcount

    # ── Notification Channel CRUD ─────────────────────────────────────

    def create_notification_channel(
        self,
        name: str,
        channel_type: str,
        config_json: str = "{}",
    ) -> int:
        """Create a notification channel. Returns the channel ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO notification_channels (name, channel_type, config_json)
                   VALUES (?, ?, ?)""",
                (name, channel_type, config_json),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def get_notification_channel(self, channel_id: int) -> Optional[dict]:
        """Get a single notification channel by ID."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM notification_channels WHERE id = ?", (channel_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_notification_channels(self, active_only: bool = False) -> list[dict]:
        """List all notification channels."""
        with self.connection() as conn:
            if active_only:
                rows = conn.execute(
                    "SELECT * FROM notification_channels WHERE is_active = 1 ORDER BY name"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM notification_channels ORDER BY name"
                ).fetchall()
            return [dict(r) for r in rows]

    def update_notification_channel(self, channel_id: int, **kwargs: object) -> bool:
        """Update notification channel fields."""
        allowed = {"name", "channel_type", "config_json", "is_active"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return False
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [channel_id]
        with self.connection() as conn:
            cursor = conn.execute(
                f"UPDATE notification_channels SET {set_clause} WHERE id = ?", values
            )
            return cursor.rowcount > 0

    def delete_notification_channel(self, channel_id: int) -> bool:
        """Delete a notification channel. Returns True if deleted."""
        with self.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM notification_channels WHERE id = ?", (channel_id,)
            )
            return cursor.rowcount > 0

    # ── Notification Rule CRUD ────────────────────────────────────────

    def create_notification_rule(
        self,
        name: str,
        alert_types: str = "[]",
        severities: str = "[]",
        channel_ids: str = "[]",
    ) -> int:
        """Create a notification rule. Returns the rule ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO notification_rules
                   (name, alert_types, severities, channel_ids)
                   VALUES (?, ?, ?, ?)""",
                (name, alert_types, severities, channel_ids),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def get_notification_rule(self, rule_id: int) -> Optional[dict]:
        """Get a single notification rule by ID."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM notification_rules WHERE id = ?", (rule_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_notification_rules(self, active_only: bool = False) -> list[dict]:
        """List all notification rules."""
        with self.connection() as conn:
            if active_only:
                rows = conn.execute(
                    "SELECT * FROM notification_rules WHERE is_active = 1 ORDER BY name"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM notification_rules ORDER BY name"
                ).fetchall()
            return [dict(r) for r in rows]

    def update_notification_rule(self, rule_id: int, **kwargs: object) -> bool:
        """Update notification rule fields."""
        allowed = {"name", "alert_types", "severities", "channel_ids", "is_active"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return False
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [rule_id]
        with self.connection() as conn:
            cursor = conn.execute(
                f"UPDATE notification_rules SET {set_clause} WHERE id = ?", values
            )
            return cursor.rowcount > 0

    def delete_notification_rule(self, rule_id: int) -> bool:
        """Delete a notification rule. Returns True if deleted."""
        with self.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM notification_rules WHERE id = ?", (rule_id,)
            )
            return cursor.rowcount > 0

    def get_channels_for_alert(
        self, alert_type: str, severity: str
    ) -> list[dict]:
        """Find notification channels matching an alert type + severity.

        Scans active rules for matching alert_type and severity, collects
        referenced channel_ids, and returns the active channel records.
        """
        import json as _json

        rules = self.list_notification_rules(active_only=True)
        matched_channel_ids: set[int] = set()
        for rule in rules:
            types = _json.loads(rule.get("alert_types", "[]"))
            sevs = _json.loads(rule.get("severities", "[]"))
            type_match = not types or alert_type in types
            sev_match = not sevs or severity in sevs
            if type_match and sev_match:
                cids = _json.loads(rule.get("channel_ids", "[]"))
                matched_channel_ids.update(cids)
        if not matched_channel_ids:
            return []
        channels = self.list_notification_channels(active_only=True)
        return [ch for ch in channels if ch["id"] in matched_channel_ids]

    # ── Partition Event CRUD ──────────────────────────────────────────

    def create_partition_event(
        self,
        event_type: str,
        component_count: int,
        components_json: str = "[]",
        previous_component_count: Optional[int] = None,
        relay_recommendation: Optional[str] = None,
        topology_before: Optional[str] = None,
        topology_after: Optional[str] = None,
    ) -> int:
        """Create a partition event. Returns the event ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO partition_events
                   (event_type, component_count, components_json,
                    previous_component_count, relay_recommendation,
                    topology_before, topology_after)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (event_type, component_count, components_json,
                 previous_component_count, relay_recommendation,
                 topology_before, topology_after),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def get_partition_event(self, event_id: int) -> Optional[dict]:
        """Get a single partition event by ID."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM partition_events WHERE id = ?", (event_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_partition_events(
        self, limit: int = 50, event_type: Optional[str] = None
    ) -> list[dict]:
        """List partition events, most recent first. Optionally filter by event_type."""
        with self.connection() as conn:
            if event_type:
                rows = conn.execute(
                    "SELECT * FROM partition_events WHERE event_type = ? ORDER BY created_at DESC LIMIT ?",
                    (event_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM partition_events ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def resolve_partition_event(self, event_id: int) -> bool:
        """Mark a partition event as resolved."""
        with self.connection() as conn:
            cursor = conn.execute(
                """UPDATE partition_events
                   SET resolved_at = datetime('now')
                   WHERE id = ? AND resolved_at IS NULL""",
                (event_id,),
            )
            return cursor.rowcount > 0

    def get_latest_partition_event(self) -> Optional[dict]:
        """Get the most recent unresolved partition event, if any."""
        with self.connection() as conn:
            row = conn.execute(
                """SELECT * FROM partition_events
                   WHERE resolved_at IS NULL
                   ORDER BY created_at DESC LIMIT 1"""
            ).fetchone()
            return dict(row) if row else None

    # ── Bulk Operation CRUD ───────────────────────────────────────────

    def create_bulk_operation(
        self,
        operation_type: str,
        target_filter: str = "{}",
        target_node_ids: str = "[]",
        parameters: str = "{}",
        total_targets: int = 0,
        operator: str = "dashboard",
        status: str = "pending",
    ) -> int:
        """Create a bulk operation. Returns the operation ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO bulk_operations
                   (operation_type, target_filter, target_node_ids, parameters,
                    total_targets, operator, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (operation_type, target_filter, target_node_ids, parameters,
                 total_targets, operator, status),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def get_bulk_operation(self, op_id: int) -> Optional[dict]:
        """Get a single bulk operation by ID."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM bulk_operations WHERE id = ?", (op_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_bulk_operations(
        self, limit: int = 50, status: Optional[str] = None
    ) -> list[dict]:
        """List bulk operations, most recent first. Optionally filter by status."""
        with self.connection() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM bulk_operations WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM bulk_operations ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def update_bulk_operation(
        self,
        op_id: int,
        *,
        status: Optional[str] = None,
        completed_count: Optional[int] = None,
        failed_count: Optional[int] = None,
        skipped_count: Optional[int] = None,
        result_json: Optional[str] = None,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        error: Optional[str] = None,
    ) -> bool:
        """Update a bulk operation's progress or status."""
        updates: list[str] = []
        values: list[object] = []
        if status is not None:
            updates.append("status = ?")
            values.append(status)
        if completed_count is not None:
            updates.append("completed_count = ?")
            values.append(completed_count)
        if failed_count is not None:
            updates.append("failed_count = ?")
            values.append(failed_count)
        if skipped_count is not None:
            updates.append("skipped_count = ?")
            values.append(skipped_count)
        if result_json is not None:
            updates.append("result_json = ?")
            values.append(result_json)
        if started_at is not None:
            updates.append("started_at = ?")
            values.append(started_at)
        if completed_at is not None:
            updates.append("completed_at = ?")
            values.append(completed_at)
        if error is not None:
            updates.append("error = ?")
            values.append(error)
        if not updates:
            return False
        values.append(op_id)
        with self.connection() as conn:
            cursor = conn.execute(
                f"UPDATE bulk_operations SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            return cursor.rowcount > 0

    def cancel_bulk_operation(self, op_id: int) -> bool:
        """Cancel a pending or running bulk operation."""
        with self.connection() as conn:
            cursor = conn.execute(
                """UPDATE bulk_operations
                   SET status = 'cancelled', completed_at = datetime('now')
                   WHERE id = ? AND status IN ('pending', 'running')""",
                (op_id,),
            )
            return cursor.rowcount > 0

    # ── Team Communication (v0.7.0) ──────────────────────────────────────

    def create_team_message(
        self,
        channel: str,
        sender: str,
        message: str,
        recipient: str | None = None,
        mesh_channel_index: int = 2,
    ) -> int:
        """Create a team communication message. Returns message ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO team_messages
                   (channel, sender, recipient, message, mesh_channel_index, status)
                   VALUES (?, ?, ?, ?, ?, 'pending')""",
                (channel, sender, recipient, message, mesh_channel_index),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def get_team_message(self, msg_id: int) -> Optional[dict]:
        """Get a single team message by ID."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM team_messages WHERE id = ?", (msg_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_team_messages(
        self,
        channel: str | None = None,
        limit: int = 50,
        hours: int | None = None,
    ) -> list[dict]:
        """List team messages, optionally filtered by channel and time window."""
        clauses, params = [], []
        if channel:
            clauses.append("channel = ?")
            params.append(channel)
        if hours:
            clauses.append(
                "created_at >= datetime('now', ?)"
            )
            params.append(f"-{hours} hours")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM team_messages {where} ORDER BY created_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_team_message_status(
        self, msg_id: int, status: str, **kwargs: object
    ) -> bool:
        """Update team message status and optional timestamp fields."""
        sets = ["status = ?"]
        params: list[object] = [status]
        for field in ("sent_at", "delivered_at"):
            if field in kwargs:
                sets.append(f"{field} = ?")
                params.append(kwargs[field])
        params.append(msg_id)
        with self.connection() as conn:
            cursor = conn.execute(
                f"UPDATE team_messages SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            return cursor.rowcount > 0

    # ── TAK Gateway (v0.7.0) ─────────────────────────────────────────────

    def upsert_tak_config(
        self,
        host: str,
        port: int = 8087,
        use_tls: bool = False,
        callsign_prefix: str = "JENN-",
        stale_timeout_seconds: int = 600,
        enabled: bool = True,
    ) -> int:
        """Create or update TAK server configuration. Returns config ID."""
        with self.connection() as conn:
            existing = conn.execute(
                "SELECT id FROM tak_config LIMIT 1"
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE tak_config
                       SET host = ?, port = ?, use_tls = ?, callsign_prefix = ?,
                           stale_timeout_seconds = ?, enabled = ?,
                           updated_at = datetime('now')
                       WHERE id = ?""",
                    (host, port, int(use_tls), callsign_prefix,
                     stale_timeout_seconds, int(enabled), existing["id"]),
                )
                return existing["id"]
            cursor = conn.execute(
                """INSERT INTO tak_config
                   (host, port, use_tls, callsign_prefix,
                    stale_timeout_seconds, enabled)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (host, port, int(use_tls), callsign_prefix,
                 stale_timeout_seconds, int(enabled)),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def get_tak_config(self) -> Optional[dict]:
        """Get current TAK server configuration."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM tak_config ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def log_tak_event(
        self,
        uid: str,
        cot_type: str,
        callsign: str,
        node_id: str,
        direction: str = "outbound",
        latitude: float | None = None,
        longitude: float | None = None,
        altitude: float | None = None,
        raw_xml: str | None = None,
    ) -> int:
        """Log a CoT event sent/received through the TAK gateway."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO tak_events
                   (uid, cot_type, callsign, node_id, direction,
                    latitude, longitude, altitude, raw_xml)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (uid, cot_type, callsign, node_id, direction,
                 latitude, longitude, altitude, raw_xml),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def list_tak_events(
        self,
        direction: str | None = None,
        node_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List TAK CoT events, optionally filtered."""
        clauses, params = [], []
        if direction:
            clauses.append("direction = ?")
            params.append(direction)
        if node_id:
            clauses.append("node_id = ?")
            params.append(node_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM tak_events {where} ORDER BY created_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_tak_event_counts(self) -> dict:
        """Get event counts by direction."""
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT direction, COUNT(*) as count
                   FROM tak_events GROUP BY direction"""
            ).fetchall()
            return {r["direction"]: r["count"] for r in rows}

    # ── Asset Tracking (v0.7.0) ──────────────────────────────────────────

    def create_asset(
        self,
        name: str,
        asset_type: str,
        node_id: str,
        zone: str | None = None,
        team: str | None = None,
        project: str | None = None,
        metadata_json: str | None = None,
    ) -> int:
        """Register a trackable asset. Returns asset ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO assets
                   (name, asset_type, node_id, zone, team, project, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (name, asset_type, node_id, zone, team, project, metadata_json),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def get_asset(self, asset_id: int) -> Optional[dict]:
        """Get a single asset by ID."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM assets WHERE id = ?", (asset_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_asset_by_node(self, node_id: str) -> Optional[dict]:
        """Get asset associated with a mesh node."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM assets WHERE node_id = ?", (node_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_assets(
        self,
        asset_type: str | None = None,
        zone: str | None = None,
        team: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """List assets with optional filters."""
        clauses, params = [], []
        if asset_type:
            clauses.append("asset_type = ?")
            params.append(asset_type)
        if zone:
            clauses.append("zone = ?")
            params.append(zone)
        if team:
            clauses.append("team = ?")
            params.append(team)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM assets {where} ORDER BY name", params
            ).fetchall()
            return [dict(r) for r in rows]

    def update_asset(self, asset_id: int, **kwargs: object) -> bool:
        """Update asset fields."""
        allowed = {"name", "asset_type", "node_id", "zone", "team",
                    "project", "status", "metadata_json"}
        sets, params = ["updated_at = datetime('now')"], []
        for k, v in kwargs.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                params.append(v)
        if len(sets) == 1:
            return False
        params.append(asset_id)
        with self.connection() as conn:
            cursor = conn.execute(
                f"UPDATE assets SET {', '.join(sets)} WHERE id = ?", params
            )
            return cursor.rowcount > 0

    def delete_asset(self, asset_id: int) -> bool:
        """Delete an asset."""
        with self.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM assets WHERE id = ?", (asset_id,)
            )
            return cursor.rowcount > 0

    def get_asset_position_trail(
        self,
        node_id: str,
        hours: int = 24,
        limit: int = 500,
    ) -> list[dict]:
        """Get position trail for an asset's node_id from positions table."""
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM positions
                   WHERE node_id = ?
                     AND timestamp >= datetime('now', ?)
                   ORDER BY timestamp DESC LIMIT ?""",
                (node_id, f"-{hours} hours", limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── Edge Associations (v0.7.0) ───────────────────────────────────────

    def create_edge_association(
        self,
        edge_device_id: str,
        node_id: str,
        edge_hostname: str | None = None,
        edge_ip: str | None = None,
        association_type: str = "co-located",
    ) -> int:
        """Create an edge-to-radio association. Returns association ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO edge_associations
                   (edge_device_id, node_id, edge_hostname, edge_ip,
                    association_type, last_verified)
                   VALUES (?, ?, ?, ?, ?, datetime('now'))""",
                (edge_device_id, node_id, edge_hostname, edge_ip,
                 association_type),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def get_edge_association_by_edge(
        self, edge_device_id: str
    ) -> Optional[dict]:
        """Get association for a JennEdge device."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM edge_associations WHERE edge_device_id = ?",
                (edge_device_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_edge_association_by_node(self, node_id: str) -> Optional[dict]:
        """Get association for a mesh radio node."""
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM edge_associations WHERE node_id = ?",
                (node_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_edge_associations(
        self, status: str | None = None
    ) -> list[dict]:
        """List all edge-radio associations."""
        if status:
            query = "SELECT * FROM edge_associations WHERE status = ? ORDER BY edge_device_id"
            params: tuple = (status,)
        else:
            query = "SELECT * FROM edge_associations ORDER BY edge_device_id"
            params = ()
        with self.connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def update_edge_association(
        self, edge_device_id: str, **kwargs: object
    ) -> bool:
        """Update edge association fields."""
        allowed = {"node_id", "edge_hostname", "edge_ip",
                    "association_type", "status"}
        sets = ["updated_at = datetime('now')", "last_verified = datetime('now')"]
        params: list[object] = []
        for k, v in kwargs.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                params.append(v)
        if len(sets) == 2:
            return False
        params.append(edge_device_id)
        with self.connection() as conn:
            cursor = conn.execute(
                f"UPDATE edge_associations SET {', '.join(sets)} WHERE edge_device_id = ?",
                params,
            )
            return cursor.rowcount > 0

    def delete_edge_association(self, edge_device_id: str) -> bool:
        """Delete an edge-radio association."""
        with self.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM edge_associations WHERE edge_device_id = ?",
                (edge_device_id,),
            )
            return cursor.rowcount > 0

    def get_edge_radio_status(self, edge_device_id: str) -> Optional[dict]:
        """Get combined edge + radio status for cross-reference display."""
        with self.connection() as conn:
            row = conn.execute(
                """SELECT ea.*, d.battery_level, d.signal_rssi, d.signal_snr,
                          d.latitude, d.longitude, d.last_seen, d.mesh_status
                   FROM edge_associations ea
                   LEFT JOIN devices d ON ea.node_id = d.node_id
                   WHERE ea.edge_device_id = ?""",
                (edge_device_id,),
            ).fetchone()
            return dict(row) if row else None

    # --- Natural language query log methods (MESH-046) ---

    def log_nl_query(
        self,
        question: str,
        *,
        query_plan_json: Optional[str] = None,
        result_summary: Optional[str] = None,
        source: str = "unknown",
        duration_ms: int = 0,
        ollama_available: bool = False,
    ) -> int:
        """Log a natural language fleet query. Returns the new row id."""
        with self.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO nl_query_log
                   (question, query_plan_json, result_summary, source,
                    duration_ms, ollama_available)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    question,
                    query_plan_json,
                    result_summary,
                    source,
                    duration_ms,
                    1 if ollama_available else 0,
                ),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def get_nl_query_history(self, limit: int = 20) -> list[dict]:
        """Get recent NL query history, most recent first."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM nl_query_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
