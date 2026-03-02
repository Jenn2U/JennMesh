"""Tests for firmware version tracking."""

from jenn_mesh.db import MeshDatabase
from jenn_mesh.provisioning.firmware import FirmwareTracker


class TestFirmwareTracker:
    def test_check_device_uptodate(self, populated_db: MeshDatabase):
        tracker = FirmwareTracker(populated_db)
        result = tracker.check_device_firmware("!aaa11111")
        assert result is not None
        assert result["current_version"] == "2.5.6"
        # 2.5.6 is latest in defaults → should not need update
        assert result["needs_update"] is False

    def test_check_device_outdated(self, populated_db: MeshDatabase):
        tracker = FirmwareTracker(populated_db)
        result = tracker.check_device_firmware("!ccc33333")
        assert result is not None
        assert result["current_version"] == "2.4.2"
        assert result["needs_update"] is True

    def test_check_unknown_device(self, populated_db: MeshDatabase):
        tracker = FirmwareTracker(populated_db)
        assert tracker.check_device_firmware("!zzz99999") is None

    def test_fleet_firmware_report(self, populated_db: MeshDatabase):
        tracker = FirmwareTracker(populated_db)
        report = tracker.get_fleet_firmware_report()
        assert len(report) == 4  # All 4 devices

    def test_outdated_devices(self, populated_db: MeshDatabase):
        tracker = FirmwareTracker(populated_db)
        outdated = tracker.get_outdated_devices()
        outdated_ids = {d["node_id"] for d in outdated}
        assert "!ccc33333" in outdated_ids  # firmware 2.4.2

    def test_pkc_incompatible(self, populated_db: MeshDatabase):
        tracker = FirmwareTracker(populated_db)
        incompatible = tracker.get_pkc_incompatible_devices()
        # 2.4.2 < 2.5.0 MIN_PKC_VERSION → should be incompatible
        incomp_ids = {d["node_id"] for d in incompatible}
        assert "!ccc33333" in incomp_ids
        # 2.5.6 and 2.5.0 are >= 2.5.0 → should be compatible
        assert "!aaa11111" not in incomp_ids
