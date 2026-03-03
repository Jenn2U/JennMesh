"""Tests for HeartbeatReceiver — parse, process, stale detection."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from jenn_mesh.core.heartbeat_receiver import HeartbeatReceiver
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.heartbeat import MeshHeartbeat


@pytest.fixture
def db(tmp_path: Path) -> MeshDatabase:
    return MeshDatabase(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def receiver(db: MeshDatabase) -> HeartbeatReceiver:
    return HeartbeatReceiver(db)


# ── Parse ────────────────────────────────────────────────────────────


class TestParseHeartbeat:
    def test_valid_heartbeat(self, receiver: HeartbeatReceiver):
        text = "HEARTBEAT|!aaa11111|3600|edge:ok,mqtt:down|85|2026-03-02T15:30:00"
        hb = receiver.parse_heartbeat(text)
        assert hb is not None
        assert hb.node_id == "!aaa11111"
        assert hb.uptime_seconds == 3600
        assert len(hb.services) == 2
        assert hb.battery == 85

    def test_with_signal_info(self, receiver: HeartbeatReceiver):
        text = "HEARTBEAT|!aaa11111|100|edge:ok|42|2026-03-02T15:30:00"
        hb = receiver.parse_heartbeat(text, rssi=-85, snr=10.5)
        assert hb is not None
        assert hb.rssi == -85
        assert hb.snr == 10.5

    def test_not_heartbeat(self, receiver: HeartbeatReceiver):
        assert receiver.parse_heartbeat("Hello world") is None

    def test_too_few_fields(self, receiver: HeartbeatReceiver):
        assert receiver.parse_heartbeat("HEARTBEAT|!aaa|100") is None

    def test_invalid_uptime(self, receiver: HeartbeatReceiver):
        text = "HEARTBEAT|!aaa11111|notanumber|edge:ok|85|2026-03-02T15:30:00"
        assert receiver.parse_heartbeat(text) is None

    def test_invalid_timestamp(self, receiver: HeartbeatReceiver):
        text = "HEARTBEAT|!aaa11111|100|edge:ok|85|not-a-timestamp"
        assert receiver.parse_heartbeat(text) is None

    def test_empty_services(self, receiver: HeartbeatReceiver):
        text = "HEARTBEAT|!aaa11111|100||42|2026-03-02T15:30:00"
        hb = receiver.parse_heartbeat(text)
        assert hb is not None
        assert hb.services == []


# ── Process ──────────────────────────────────────────────────────────


class TestProcessHeartbeat:
    def test_stores_heartbeat_in_db(self, receiver: HeartbeatReceiver, db: MeshDatabase):
        # Register device first
        db.upsert_device("!aaa11111", long_name="Test")
        hb = MeshHeartbeat(
            node_id="!aaa11111",
            uptime_seconds=3600,
            battery=85,
            timestamp=datetime(2026, 3, 2, 15, 30, 0),
        )
        receiver.process_heartbeat(hb)
        latest = db.get_latest_heartbeat("!aaa11111")
        assert latest is not None
        assert latest["uptime_seconds"] == 3600
        assert latest["battery"] == 85

    def test_updates_device_mesh_status(self, receiver: HeartbeatReceiver, db: MeshDatabase):
        db.upsert_device("!aaa11111", long_name="Test")
        hb = MeshHeartbeat(
            node_id="!aaa11111",
            uptime_seconds=100,
            battery=50,
            timestamp=datetime(2026, 3, 2, 15, 0, 0),
        )
        receiver.process_heartbeat(hb)
        device = db.get_device("!aaa11111")
        assert device is not None
        assert device["mesh_status"] == "reachable"
        assert device["last_mesh_heartbeat"] is not None


# ── Handle text message (convenience) ────────────────────────────────


class TestHandleTextMessage:
    def test_handles_valid_heartbeat(self, receiver: HeartbeatReceiver, db: MeshDatabase):
        db.upsert_device("!bbb22222", long_name="GW")
        text = "HEARTBEAT|!bbb22222|200|edge:ok,mqtt:ok|70|2026-03-02T15:30:00"
        assert receiver.handle_text_message(text) is True
        assert db.get_latest_heartbeat("!bbb22222") is not None

    def test_returns_false_for_non_heartbeat(self, receiver: HeartbeatReceiver):
        assert receiver.handle_text_message("Hello world") is False

    def test_returns_false_for_malformed(self, receiver: HeartbeatReceiver):
        assert receiver.handle_text_message("HEARTBEAT|bad") is False


# ── Stale detection ──────────────────────────────────────────────────


class TestStaleDetection:
    def test_marks_stale_nodes_unreachable(self, db: MeshDatabase):
        receiver = HeartbeatReceiver(db, stale_threshold_seconds=300)
        old_ts = (datetime.utcnow() - timedelta(minutes=10)).isoformat()
        db.upsert_device(
            "!aaa11111",
            long_name="Stale",
            last_mesh_heartbeat=old_ts,
            mesh_status="reachable",
        )
        stale = receiver.check_stale_heartbeats()
        assert "!aaa11111" in stale
        device = db.get_device("!aaa11111")
        assert device["mesh_status"] == "unreachable"

    def test_keeps_recent_nodes_reachable(self, db: MeshDatabase):
        receiver = HeartbeatReceiver(db, stale_threshold_seconds=300)
        fresh_ts = datetime.utcnow().isoformat()
        db.upsert_device(
            "!bbb22222",
            long_name="Fresh",
            last_mesh_heartbeat=fresh_ts,
            mesh_status="reachable",
        )
        stale = receiver.check_stale_heartbeats()
        assert "!bbb22222" not in stale

    def test_ignores_unknown_status_nodes(self, db: MeshDatabase):
        receiver = HeartbeatReceiver(db, stale_threshold_seconds=300)
        db.upsert_device("!ccc33333", long_name="Unknown")
        stale = receiver.check_stale_heartbeats()
        assert "!ccc33333" not in stale

    def test_fixes_reachable_without_heartbeat(self, db: MeshDatabase):
        """A device marked reachable but with no heartbeat timestamp gets fixed."""
        receiver = HeartbeatReceiver(db, stale_threshold_seconds=300)
        db.upsert_device("!ddd44444", long_name="Broken", mesh_status="reachable")
        stale = receiver.check_stale_heartbeats()
        assert "!ddd44444" in stale
