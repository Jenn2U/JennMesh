"""Shared test fixtures for JennMesh test suite."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from jenn_mesh.db import MeshDatabase


@pytest.fixture
def db(tmp_path: Path) -> MeshDatabase:
    """Fresh SQLite database for each test — isolated, no filesystem leaks."""
    db_path = str(tmp_path / "test_mesh.db")
    return MeshDatabase(db_path=db_path)


@pytest.fixture
def populated_db(db: MeshDatabase) -> MeshDatabase:
    """Database pre-loaded with a small test fleet.

    Fleet:
      - !aaa11111 — relay, online, 80% battery, GPS in Austin TX
      - !bbb22222 — gateway, online, 45% battery, GPS in Austin TX (near relay)
      - !ccc33333 — mobile, offline (2 hours ago), 15% battery, GPS in Dallas TX
      - !ddd44444 — sensor, never seen (no last_seen)
    """
    now = datetime.utcnow()
    recent = (now - timedelta(minutes=2)).isoformat()
    old = (now - timedelta(hours=2)).isoformat()

    # Online relay in Austin
    db.upsert_device(
        "!aaa11111",
        long_name="Relay-HQ",
        short_name="RLYQ",
        role="ROUTER",
        hw_model="heltec_v3",
        firmware_version="2.5.6",
        battery_level=80,
        voltage=4.1,
        signal_snr=10.5,
        signal_rssi=-85,
        latitude=30.2672,
        longitude=-97.7431,
        altitude=150.0,
        last_seen=recent,
    )

    # Online gateway in Austin (nearby)
    db.upsert_device(
        "!bbb22222",
        long_name="Gateway-Edge1",
        short_name="GW01",
        role="CLIENT_MUTE",
        hw_model="tbeam",
        firmware_version="2.5.6",
        battery_level=45,
        voltage=3.7,
        signal_snr=8.2,
        signal_rssi=-92,
        latitude=30.2700,
        longitude=-97.7400,
        altitude=145.0,
        last_seen=recent,
        associated_edge_node="edge-node-pi4-01",
    )

    # Offline mobile in Dallas
    db.upsert_device(
        "!ccc33333",
        long_name="Mobile-Field",
        short_name="MOB1",
        role="CLIENT",
        hw_model="tbeam_s3",
        firmware_version="2.4.2",
        battery_level=15,
        voltage=3.3,
        latitude=32.7767,
        longitude=-96.7970,
        last_seen=old,
    )

    # Sensor never seen
    db.upsert_device(
        "!ddd44444",
        long_name="Sensor-Env",
        short_name="SNS1",
        role="SENSOR",
        hw_model="rak4631",
        firmware_version="2.5.0",
    )

    # Add position history for devices with GPS
    db.add_position("!aaa11111", 30.2672, -97.7431, altitude=150.0, source="gps")
    db.add_position("!bbb22222", 30.2700, -97.7400, altitude=145.0, source="gps")
    db.add_position("!ccc33333", 32.7767, -96.7970, source="gps", timestamp=old)

    # Topology edges: relay↔gateway (bidirectional), gateway→mobile (unidirectional)
    # !ddd44444 (sensor) has no edges — isolated
    db.upsert_topology_edge("!aaa11111", "!bbb22222", snr=10.5, rssi=-85)
    db.upsert_topology_edge("!bbb22222", "!aaa11111", snr=8.0, rssi=-92)
    db.upsert_topology_edge("!bbb22222", "!ccc33333", snr=-2.0, rssi=-110)

    # Telemetry history for baseline computation (20 samples per active node)
    for i in range(20):
        ts = (now - timedelta(days=6, hours=i)).isoformat()
        db.add_telemetry_sample(
            "!aaa11111",
            rssi=-85 + (i % 3),
            snr=10.5 + (i % 4) * 0.5,
            battery_level=80 - i,
            voltage=4.1 - i * 0.01,
            timestamp=ts,
        )
        db.add_telemetry_sample(
            "!bbb22222",
            rssi=-92 + (i % 2),
            snr=8.2 + (i % 3) * 0.3,
            battery_level=45 - i // 2,
            voltage=3.7 - i * 0.005,
            timestamp=ts,
        )

    # Mesh heartbeat data for !bbb22222 (reachable via mesh) and !ccc33333 (stale)
    hb_recent_ts = (now - timedelta(minutes=1)).isoformat()
    hb_stale_ts = (now - timedelta(minutes=15)).isoformat()

    db.add_heartbeat(
        node_id="!bbb22222",
        uptime_seconds=3600,
        services_json='[{"name":"edge","status":"ok"},{"name":"radio","status":"ok"}]',
        battery=45,
        rssi=-92,
        snr=8.2,
        timestamp=hb_recent_ts,
    )
    db.upsert_device("!bbb22222", mesh_status="reachable", last_mesh_heartbeat=hb_recent_ts)

    db.add_heartbeat(
        node_id="!ccc33333",
        uptime_seconds=7200,
        services_json='[{"name":"edge","status":"ok"},{"name":"mqtt","status":"down"}]',
        battery=15,
        timestamp=hb_stale_ts,
    )
    db.upsert_device("!ccc33333", mesh_status="reachable", last_mesh_heartbeat=hb_stale_ts)

    # Firmware compatibility matrix seed data
    db.upsert_firmware_compat("heltec_v3", "2.5.6", "COMPATIBLE")
    db.upsert_firmware_compat("heltec_v3", "2.5.0", "COMPATIBLE")
    db.upsert_firmware_compat("tbeam", "2.5.6", "COMPATIBLE")
    db.upsert_firmware_compat("tbeam_s3", "2.5.6", "COMPATIBLE")
    db.upsert_firmware_compat("tbeam_s3", "2.4.2", "COMPATIBLE")
    db.upsert_firmware_compat("rak4631", "2.5.6", "COMPATIBLE")
    db.upsert_firmware_compat("rak4631", "2.5.0", "COMPATIBLE")
    db.upsert_firmware_compat("t_echo", "2.4.0", "INCOMPATIBLE", "Known display issue")

    return db
