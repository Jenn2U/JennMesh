"""Tests for SQLite WAL database operations."""

import pytest

from jenn_mesh.db import MeshDatabase


class TestMeshDatabase:
    def test_creates_schema(self, db: MeshDatabase):
        """Schema initializes on first connection."""
        with db.connection() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        table_names = {r["name"] for r in tables}
        assert "devices" in table_names
        assert "positions" in table_names
        assert "alerts" in table_names
        assert "config_templates" in table_names
        assert "provisioning_log" in table_names
        assert "channels" in table_names
        assert "schema_version" in table_names

    def test_wal_mode_enabled(self, db: MeshDatabase):
        with db.connection() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()
        assert mode[0] == "wal"


class TestDeviceOperations:
    def test_upsert_and_get(self, db: MeshDatabase):
        db.upsert_device("!test01", long_name="Test Node", role="ROUTER")
        device = db.get_device("!test01")
        assert device is not None
        assert device["long_name"] == "Test Node"
        assert device["role"] == "ROUTER"

    def test_upsert_updates_existing(self, db: MeshDatabase):
        db.upsert_device("!test01", long_name="Original")
        db.upsert_device("!test01", long_name="Updated")
        device = db.get_device("!test01")
        assert device["long_name"] == "Updated"

    def test_upsert_partial_update(self, db: MeshDatabase):
        db.upsert_device("!test01", long_name="Name", battery_level=80)
        db.upsert_device("!test01", battery_level=60)  # Only update battery
        device = db.get_device("!test01")
        assert device["long_name"] == "Name"  # Unchanged
        assert device["battery_level"] == 60  # Updated

    def test_get_nonexistent_returns_none(self, db: MeshDatabase):
        assert db.get_device("!nope") is None

    def test_list_devices_empty(self, db: MeshDatabase):
        assert db.list_devices() == []

    def test_list_devices_populated(self, populated_db: MeshDatabase):
        devices = populated_db.list_devices()
        assert len(devices) == 4


class TestPositionOperations:
    def test_add_and_get_position(self, db: MeshDatabase):
        db.upsert_device("!test01")
        db.add_position("!test01", 30.0, -97.0, altitude=150.0)
        pos = db.get_latest_position("!test01")
        assert pos is not None
        assert pos["latitude"] == 30.0
        assert pos["longitude"] == -97.0
        assert pos["altitude"] == 150.0

    def test_latest_position_returns_most_recent(self, db: MeshDatabase):
        db.upsert_device("!test01")
        db.add_position("!test01", 30.0, -97.0, timestamp="2024-01-01T00:00:00")
        db.add_position("!test01", 31.0, -96.0, timestamp="2024-06-01T00:00:00")
        pos = db.get_latest_position("!test01")
        assert pos["latitude"] == 31.0

    def test_no_position_returns_none(self, db: MeshDatabase):
        assert db.get_latest_position("!nope") is None

    def test_positions_in_radius(self, populated_db: MeshDatabase):
        # Austin area: relay and gateway are within 0.1 degrees
        results = populated_db.get_positions_in_radius(30.27, -97.74, 0.1)
        node_ids = {r["node_id"] for r in results}
        assert "!aaa11111" in node_ids
        assert "!bbb22222" in node_ids
        assert "!ccc33333" not in node_ids  # Dallas is far away


class TestAlertOperations:
    def test_create_and_get_alert(self, db: MeshDatabase):
        db.upsert_device("!test01")
        alert_id = db.create_alert("!test01", "node_offline", "critical", "Node offline")
        assert alert_id > 0
        alerts = db.get_active_alerts()
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == "node_offline"

    def test_resolve_alert(self, db: MeshDatabase):
        db.upsert_device("!test01")
        alert_id = db.create_alert("!test01", "low_battery", "warning", "Low battery")
        db.resolve_alert(alert_id)
        assert db.get_active_alerts() == []

    def test_has_active_alert(self, db: MeshDatabase):
        db.upsert_device("!test01")
        db.create_alert("!test01", "low_battery", "warning", "Low")
        assert db.has_active_alert("!test01", "low_battery") is True
        assert db.has_active_alert("!test01", "node_offline") is False

    def test_filter_alerts_by_node(self, db: MeshDatabase):
        db.upsert_device("!a")
        db.upsert_device("!b")
        db.create_alert("!a", "low_battery", "warning", "A low")
        db.create_alert("!b", "node_offline", "critical", "B offline")
        alerts_a = db.get_active_alerts(node_id="!a")
        assert len(alerts_a) == 1
        assert alerts_a[0]["node_id"] == "!a"


class TestConfigTemplateOperations:
    def test_save_and_get_template(self, db: MeshDatabase):
        db.save_config_template("relay-node", "yaml: content", "abc123")
        tpl = db.get_config_template("relay-node")
        assert tpl is not None
        assert tpl["yaml_content"] == "yaml: content"
        assert tpl["config_hash"] == "abc123"

    def test_upsert_template(self, db: MeshDatabase):
        db.save_config_template("relay-node", "v1", "hash1")
        db.save_config_template("relay-node", "v2", "hash2")
        tpl = db.get_config_template("relay-node")
        assert tpl["yaml_content"] == "v2"

    def test_get_nonexistent_template(self, db: MeshDatabase):
        assert db.get_config_template("nonexistent") is None


class TestProvisioningLog:
    def test_log_and_retrieve(self, db: MeshDatabase):
        db.upsert_device("!test01")
        db.log_provisioning("!test01", "flash", role="relay", operator="test")
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM provisioning_log WHERE node_id = '!test01'"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["action"] == "flash"


class TestPrunePositions:
    def test_prune_old_data(self, db: MeshDatabase):
        db.upsert_device("!test01")
        db.add_position("!test01", 30.0, -97.0, timestamp="2020-01-01T00:00:00")
        db.add_position("!test01", 31.0, -96.0)  # Now
        deleted = db.prune_old_positions(retention_days=30)
        assert deleted == 1
        # Recent position should remain
        pos = db.get_latest_position("!test01")
        assert pos is not None
