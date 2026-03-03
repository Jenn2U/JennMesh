"""Integration tests — full heartbeat flow from sender to receiver to registry."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jenn_mesh.agent.heartbeat_sender import HeartbeatSender
from jenn_mesh.core.heartbeat_receiver import HeartbeatReceiver
from jenn_mesh.core.registry import DeviceRegistry
from jenn_mesh.db import MeshDatabase


@pytest.fixture
def db(tmp_path: Path) -> MeshDatabase:
    db_path = str(tmp_path / "integration_mesh.db")
    return MeshDatabase(db_path=db_path)


@pytest.fixture
def receiver(db: MeshDatabase) -> HeartbeatReceiver:
    return HeartbeatReceiver(db, stale_threshold_seconds=300)


# ── End-to-end: build → parse → store ──────────────────────────────────


class TestSenderToReceiverFlow:
    """Verify that a message built by HeartbeatSender can be fully processed
    by HeartbeatReceiver and the data lands correctly in the DB."""

    def test_full_round_trip(self, db: MeshDatabase, receiver: HeartbeatReceiver):
        """HeartbeatSender builds a message → HeartbeatReceiver parses + stores it."""
        bridge = MagicMock()
        bridge.send_text = MagicMock(return_value=True)

        sender = HeartbeatSender(node_id="!aaa11111", bridge=bridge, interval=120)

        # Register the device first
        db.upsert_device("!aaa11111", long_name="Relay-HQ", role="ROUTER")

        # Sender builds + "sends" a message
        msg = sender.build_message(uptime_seconds=3600, services="edge:ok,mqtt:down", battery=80)

        # Receiver parses + stores the message
        handled = receiver.handle_text_message(msg, rssi=-85, snr=10.5)
        assert handled is True

        # Verify DB has the heartbeat
        latest = db.get_latest_heartbeat("!aaa11111")
        assert latest is not None
        assert latest["uptime_seconds"] == 3600
        assert latest["battery"] == 80
        assert latest["rssi"] == -85
        assert latest["snr"] == 10.5

        # Verify device mesh_status was updated
        device = db.get_device("!aaa11111")
        assert device["mesh_status"] == "reachable"
        assert device["last_mesh_heartbeat"] is not None

    def test_multiple_heartbeats_accumulate(self, db: MeshDatabase, receiver: HeartbeatReceiver):
        """Multiple heartbeats from same node build up history."""
        db.upsert_device("!aaa11111", long_name="Relay-HQ")
        bridge = MagicMock()
        bridge.send_text = MagicMock(return_value=True)
        sender = HeartbeatSender(node_id="!aaa11111", bridge=bridge)

        for i in range(3):
            msg = sender.build_message(uptime_seconds=100 + i * 120, battery=80 - i)
            receiver.handle_text_message(msg)

        history = db.get_heartbeat_history("!aaa11111", limit=10)
        assert len(history) == 3


# ── Registry differentiation: INTERNET_DOWN vs NODE_OFFLINE ─────────────


class TestRegistryStatusDifferentiation:
    """Verify that DeviceRegistry correctly differentiates alert types
    based on mesh heartbeat status."""

    def test_mesh_reachable_gets_internet_down_alert(self, db: MeshDatabase):
        """Node offline (no HTTP) but reachable via mesh → INTERNET_DOWN (warning)."""
        old_time = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        db.upsert_device(
            "!aaa11111",
            long_name="Relay-HQ",
            role="ROUTER",
            last_seen=old_time,
            mesh_status="reachable",
            last_mesh_heartbeat=datetime.utcnow().isoformat(),
        )

        registry = DeviceRegistry(db, offline_threshold_seconds=600)
        alerts = registry.check_offline_nodes()

        assert len(alerts) == 1
        assert alerts[0].alert_type.value == "internet_down"
        assert alerts[0].severity.value == "warning"
        assert "reachable via mesh" in alerts[0].message

    def test_mesh_unreachable_gets_node_offline_alert(self, db: MeshDatabase):
        """Node offline AND no mesh heartbeat → NODE_OFFLINE (critical)."""
        old_time = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        db.upsert_device(
            "!aaa11111",
            long_name="Relay-HQ",
            role="ROUTER",
            last_seen=old_time,
            mesh_status="unreachable",
        )

        registry = DeviceRegistry(db, offline_threshold_seconds=600)
        alerts = registry.check_offline_nodes()

        assert len(alerts) == 1
        assert alerts[0].alert_type.value == "node_offline"
        assert alerts[0].severity.value == "critical"

    def test_fleet_health_includes_mesh_count(self, db: MeshDatabase):
        """FleetHealth.mesh_reachable_count reflects devices with mesh_status='reachable'."""
        now = datetime.utcnow()
        recent = (now - timedelta(minutes=2)).isoformat()

        db.upsert_device("!aaa11111", last_seen=recent, mesh_status="reachable")
        db.upsert_device("!bbb22222", last_seen=recent, mesh_status="reachable")
        db.upsert_device("!ccc33333", last_seen=recent, mesh_status="unreachable")
        db.upsert_device("!ddd44444", last_seen=recent, mesh_status="unknown")

        registry = DeviceRegistry(db)
        health = registry.get_fleet_health()

        assert health.mesh_reachable_count == 2
