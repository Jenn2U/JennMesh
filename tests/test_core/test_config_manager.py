"""Tests for config manager — golden templates, drift detection."""

from pathlib import Path

import pytest

from jenn_mesh.core.config_manager import ConfigManager
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.device import ConfigHash, DeviceRole


class TestLoadTemplates:
    def test_load_from_disk(self, db: MeshDatabase):
        """Load golden configs from the actual configs/ directory."""
        cm = ConfigManager(db)
        templates = cm.load_templates_from_disk()
        assert "relay-node" in templates
        assert "edge-gateway" in templates
        assert "mobile-client" in templates
        assert "sensor-node" in templates

    def test_load_stores_in_db(self, db: MeshDatabase):
        cm = ConfigManager(db)
        cm.load_templates_from_disk()
        tpl = db.get_config_template("relay-node")
        assert tpl is not None
        assert len(tpl["config_hash"]) == 64  # SHA-256

    def test_load_missing_dir_returns_empty(self, db: MeshDatabase, tmp_path: Path):
        cm = ConfigManager(db, configs_dir=tmp_path / "nonexistent")
        templates = cm.load_templates_from_disk()
        assert templates == {}


class TestGetTemplate:
    def test_get_from_db(self, db: MeshDatabase):
        db.save_config_template("test-role", "yaml: here", "hash123")
        cm = ConfigManager(db)
        content = cm.get_template("test-role")
        assert content == "yaml: here"

    def test_get_falls_back_to_disk(self, db: MeshDatabase):
        cm = ConfigManager(db)
        # relay-node.yaml exists on disk
        content = cm.get_template("relay-node")
        assert content is not None
        assert "device:" in content

    def test_get_nonexistent_returns_none(self, db: MeshDatabase, tmp_path: Path):
        cm = ConfigManager(db, configs_dir=tmp_path)
        assert cm.get_template("nonexistent") is None


class TestDriftDetection:
    def test_check_drift_detects_mismatch(self, db: MeshDatabase):
        # Set up device with known template
        db.upsert_device("!test01", role="ROUTER")
        with db.connection() as conn:
            conn.execute(
                "UPDATE devices SET template_role = ?, template_hash = ? WHERE node_id = ?",
                ("relay-node", "original_hash", "!test01"),
            )
        db.save_config_template("relay-node", "original config", "original_hash")

        cm = ConfigManager(db)
        drifted = cm.check_drift("!test01", "modified config")
        assert drifted is True

    def test_check_drift_matching_config(self, db: MeshDatabase):
        config_content = "exact config"
        config_hash = ConfigHash.compute(config_content)

        db.upsert_device("!test01", role="ROUTER")
        with db.connection() as conn:
            conn.execute(
                "UPDATE devices SET template_role = ?, template_hash = ? WHERE node_id = ?",
                ("relay-node", config_hash, "!test01"),
            )
        db.save_config_template("relay-node", config_content, config_hash)

        cm = ConfigManager(db)
        drifted = cm.check_drift("!test01", config_content)
        assert drifted is False

    def test_drift_report_empty_fleet(self, db: MeshDatabase):
        cm = ConfigManager(db)
        assert cm.get_drift_report() == []


class TestRoleToFilename:
    def test_relay_mapping(self):
        assert ConfigManager.role_to_filename(DeviceRole.RELAY) == "relay-node"

    def test_gateway_mapping(self):
        assert ConfigManager.role_to_filename(DeviceRole.GATEWAY) == "edge-gateway"

    def test_mobile_mapping(self):
        assert ConfigManager.role_to_filename(DeviceRole.MOBILE) == "mobile-client"

    def test_sensor_mapping(self):
        assert ConfigManager.role_to_filename(DeviceRole.SENSOR) == "sensor-node"

    def test_repeater_shares_relay_template(self):
        assert ConfigManager.role_to_filename(DeviceRole.REPEATER) == "relay-node"
