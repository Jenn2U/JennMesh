"""Tests for firmware-hardware compatibility matrix (MESH-021)."""

from jenn_mesh.db import MeshDatabase
from jenn_mesh.provisioning.firmware import (
    COMPATIBLE,
    INCOMPATIBLE,
    UNTESTED,
    FirmwareTracker,
)


class TestFirmwareCompatibility:
    def test_check_compatible_combination(self, populated_db: MeshDatabase):
        tracker = FirmwareTracker(populated_db)
        status = tracker.check_compatibility("heltec_v3", "2.5.6")
        assert status == COMPATIBLE

    def test_check_incompatible_combination(self, populated_db: MeshDatabase):
        tracker = FirmwareTracker(populated_db)
        status = tracker.check_compatibility("t_echo", "2.4.0")
        assert status == INCOMPATIBLE

    def test_check_untested_combination(self, populated_db: MeshDatabase):
        tracker = FirmwareTracker(populated_db)
        status = tracker.check_compatibility("heltec_v3", "9.9.9")
        assert status == UNTESTED

    def test_get_compatible_versions(self, populated_db: MeshDatabase):
        tracker = FirmwareTracker(populated_db)
        versions = tracker.get_compatible_versions("heltec_v3")
        assert len(versions) >= 2
        assert all(v["status"] == COMPATIBLE for v in versions)
        fw_versions = {v["firmware_version"] for v in versions}
        assert "2.5.6" in fw_versions
        assert "2.5.0" in fw_versions

    def test_get_compatible_versions_empty(self, populated_db: MeshDatabase):
        tracker = FirmwareTracker(populated_db)
        versions = tracker.get_compatible_versions("nonexistent_hw")
        assert versions == []

    def test_get_compatibility_matrix(self, populated_db: MeshDatabase):
        tracker = FirmwareTracker(populated_db)
        matrix = tracker.get_compatibility_matrix()
        assert len(matrix) >= 8  # At least the seeded entries
        assert all("hw_model" in e and "firmware_version" in e for e in matrix)

    def test_add_compatibility_entry(self, db: MeshDatabase):
        tracker = FirmwareTracker(db)
        tracker.add_compatibility_entry("heltec_v3", "2.6.0", COMPATIBLE, "Tested OK")
        entry = db.get_firmware_compat_entry("heltec_v3", "2.6.0")
        assert entry is not None
        assert entry["status"] == COMPATIBLE
        assert entry["notes"] == "Tested OK"

    def test_is_safe_to_flash_compatible(self, populated_db: MeshDatabase):
        tracker = FirmwareTracker(populated_db)
        assert tracker.is_safe_to_flash("heltec_v3", "2.5.6") is True

    def test_is_safe_to_flash_incompatible(self, populated_db: MeshDatabase):
        tracker = FirmwareTracker(populated_db)
        assert tracker.is_safe_to_flash("t_echo", "2.4.0") is False

    def test_is_safe_to_flash_untested(self, populated_db: MeshDatabase):
        tracker = FirmwareTracker(populated_db)
        # Untested should be treated as unsafe (conservative)
        assert tracker.is_safe_to_flash("heltec_v3", "9.9.9") is False

    def test_get_upgradeable_devices(self, populated_db: MeshDatabase):
        tracker = FirmwareTracker(populated_db)
        # Seed compat for tbeam_s3 2.5.6 so ccc33333 (fw 2.4.2) can upgrade
        tracker.add_compatibility_entry("tbeam_s3", "2.5.6", COMPATIBLE)
        upgradeable = tracker.get_upgradeable_devices()
        node_ids = {d["node_id"] for d in upgradeable}
        # !ccc33333 is on 2.4.2 (tbeam_s3), latest 2.5.6 is COMPATIBLE
        assert "!ccc33333" in node_ids
        # !aaa11111 is already on latest — should NOT be upgradeable
        assert "!aaa11111" not in node_ids

    def test_seed_compatibility_matrix(self, db: MeshDatabase):
        tracker = FirmwareTracker(db)
        count = tracker.seed_compatibility_matrix()
        assert count >= 13  # DEFAULT_COMPATIBILITY_MATRIX has 13 entries
        matrix = tracker.get_compatibility_matrix()
        assert len(matrix) >= 13
