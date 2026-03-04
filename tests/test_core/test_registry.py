"""Tests for device registry — fleet management operations."""

from datetime import datetime

from jenn_mesh.core.registry import DeviceRegistry
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.device import DeviceRole, FirmwareInfo, MeshDevice


class TestDeviceRegistry:
    def test_register_and_retrieve(self, db: MeshDatabase):
        registry = DeviceRegistry(db)
        device = MeshDevice(
            node_id="!test01",
            long_name="Test Relay",
            role=DeviceRole.RELAY,
            firmware=FirmwareInfo(version="2.5.6", hw_model="heltec_v3"),
            battery_level=90,
            last_seen=datetime.utcnow(),
        )
        registry.register_device(device)
        retrieved = registry.get_device("!test01")
        assert retrieved is not None
        assert retrieved.long_name == "Test Relay"
        assert retrieved.role == DeviceRole.RELAY

    def test_get_nonexistent_returns_none(self, db: MeshDatabase):
        registry = DeviceRegistry(db)
        assert registry.get_device("!nonexistent") is None

    def test_list_devices(self, populated_db: MeshDatabase):
        registry = DeviceRegistry(populated_db)
        devices = registry.list_devices()
        assert len(devices) == 4

    def test_online_detection(self, populated_db: MeshDatabase):
        registry = DeviceRegistry(populated_db, offline_threshold_seconds=600)
        devices = registry.list_devices()
        by_id = {d.node_id: d for d in devices}

        # Recent last_seen → online
        assert by_id["!aaa11111"].is_online is True
        assert by_id["!bbb22222"].is_online is True
        # 2 hours ago → offline
        assert by_id["!ccc33333"].is_online is False
        # Never seen → offline
        assert by_id["!ddd44444"].is_online is False


class TestFleetHealth:
    def test_fleet_health_counts(self, populated_db: MeshDatabase):
        registry = DeviceRegistry(populated_db)
        health = registry.get_fleet_health()
        assert health.total_devices == 4
        assert health.online_count == 2
        # ccc33333 is offline (has last_seen), ddd44444 is unknown (no last_seen)
        assert health.online_count + health.offline_count + health.degraded_count == 4

    def test_health_score(self, populated_db: MeshDatabase):
        registry = DeviceRegistry(populated_db)
        health = registry.get_fleet_health()
        assert health.health_score == 50.0  # 2 of 4 online


class TestOfflineDetection:
    def test_check_offline_nodes_creates_alerts(self, populated_db: MeshDatabase):
        registry = DeviceRegistry(populated_db, offline_threshold_seconds=600)
        alerts = registry.check_offline_nodes()
        # ccc33333 was seen 2 hours ago → should fire
        offline_ids = {a.node_id for a in alerts}
        assert "!ccc33333" in offline_ids

    def test_no_duplicate_alerts(self, populated_db: MeshDatabase):
        registry = DeviceRegistry(populated_db, offline_threshold_seconds=600)
        registry.check_offline_nodes()
        alerts2 = registry.check_offline_nodes()
        # Second call should not create duplicates
        assert len(alerts2) == 0


class TestLowBatteryDetection:
    def test_low_battery_alert(self, populated_db: MeshDatabase):
        registry = DeviceRegistry(populated_db)
        alerts = registry.check_low_battery(threshold_percent=20)
        low_ids = {a.node_id for a in alerts}
        assert "!ccc33333" in low_ids  # 15% battery

    def test_healthy_battery_no_alert(self, populated_db: MeshDatabase):
        registry = DeviceRegistry(populated_db)
        alerts = registry.check_low_battery(threshold_percent=10)
        # 15% > 10% threshold, should not fire
        low_ids = {a.node_id for a in alerts}
        assert "!ccc33333" not in low_ids
