"""Tests for workbench manager — single-radio config builder session.

All tests mock the meshtastic library (no real hardware). We patch at the
WorkbenchManager boundary (_create_interface) rather than the meshtastic
module itself, since meshtastic is not installed in the test environment.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

from jenn_mesh.core.workbench_manager import WorkbenchManager
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.workbench import (
    ApplyResult,
    ConfigDiff,
    ConfigSection,
    ConnectionMethod,
    ConnectionRequest,
    RadioConfig,
    SaveTemplateRequest,
    WorkbenchStatus,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _make_mock_interface(
    node_num: int = 0xAABB1122,
    long_name: str = "TestRadio",
    short_name: str = "TST",
    hw_model: str = "heltec_v3",
    firmware: str = "2.5.6",
) -> MagicMock:
    """Create a realistic mock meshtastic interface with localNode, myInfo, nodes."""
    interface = MagicMock()

    # myInfo — basic device identity
    interface.myInfo = MagicMock()
    interface.myInfo.my_node_num = node_num
    interface.myInfo.hw_model_string = hw_model
    interface.myInfo.firmware_version = firmware

    # nodes dict — user info lookup
    node_id = f"!{node_num:08x}"
    interface.nodes = {
        node_id: {
            "user": {
                "longName": long_name,
                "shortName": short_name,
                "hwModel": hw_model,
            }
        }
    }

    # localNode — config containers
    local_node = MagicMock()
    interface.localNode = local_node
    local_node.uptimeSeconds = 3600

    # localConfig — protobuf-like objects for each section
    local_config = MagicMock()
    local_node.localConfig = local_config
    for section in [
        "device",
        "lora",
        "position",
        "power",
        "display",
        "bluetooth",
        "network",
        "security",
    ]:
        setattr(local_config, section, MagicMock())

    # moduleConfig
    module_config = MagicMock()
    local_node.moduleConfig = module_config
    for section in [
        "mqtt",
        "telemetry",
        "serial",
        "external_notification",
        "range_test",
        "store_forward",
        "canned_message",
    ]:
        setattr(module_config, section, MagicMock())

    return interface


def _mock_protobuf_to_dict(proto: Any) -> dict[str, Any]:
    """Stable fake MessageToDict for tests — returns predictable fields."""
    return {"role": 4, "is_managed": False, "node_info_broadcast_secs": 900}


def _connect_workbench(wm: WorkbenchManager, **kwargs) -> WorkbenchStatus:
    """Connect a WorkbenchManager using a mock interface, bypassing meshtastic imports."""
    mock_iface = _make_mock_interface(**kwargs)
    with patch.object(wm, "_create_interface", return_value=mock_iface):
        request = ConnectionRequest(method=ConnectionMethod.SERIAL, port="/dev/ttyUSB0")
        return wm.connect(request)


# ── Connection lifecycle ─────────────────────────────────────────────


class TestConnect:
    def test_connect_serial(self, db: MeshDatabase):
        wm = WorkbenchManager(db)
        mock_iface = _make_mock_interface()

        with patch.object(wm, "_create_interface", return_value=mock_iface) as mock_create:
            request = ConnectionRequest(method=ConnectionMethod.SERIAL, port="/dev/ttyUSB0")
            status = wm.connect(request)

        assert status.connected is True
        assert status.method == ConnectionMethod.SERIAL
        assert status.address == "/dev/ttyUSB0"
        mock_create.assert_called_once_with(request)

    def test_connect_tcp(self, db: MeshDatabase):
        wm = WorkbenchManager(db)
        mock_iface = _make_mock_interface()

        with patch.object(wm, "_create_interface", return_value=mock_iface) as mock_create:
            request = ConnectionRequest(method=ConnectionMethod.TCP, host="10.10.50.100:4403")
            status = wm.connect(request)

        assert status.connected is True
        assert status.method == ConnectionMethod.TCP
        assert status.address == "10.10.50.100:4403"
        mock_create.assert_called_once_with(request)

    def test_connect_failure_returns_error_status(self, db: MeshDatabase):
        wm = WorkbenchManager(db)

        with patch.object(wm, "_create_interface", side_effect=ValueError("No radio found")):
            request = ConnectionRequest(method=ConnectionMethod.SERIAL)
            status = wm.connect(request)

        assert status.connected is False
        assert status.error is not None
        assert "No radio found" in status.error

    def test_connect_auto_disconnects_previous(self, db: MeshDatabase):
        """Connecting a new radio should close the previous interface."""
        wm = WorkbenchManager(db)
        first_interface = _make_mock_interface(node_num=0x11111111, long_name="Radio1")
        second_interface = _make_mock_interface(node_num=0x22222222, long_name="Radio2")

        # First connection
        with patch.object(wm, "_create_interface", return_value=first_interface):
            wm.connect(ConnectionRequest(method=ConnectionMethod.SERIAL, port="/dev/ttyUSB0"))
        assert wm.is_connected

        # Second connection — should close first
        with patch.object(wm, "_create_interface", return_value=second_interface):
            wm.connect(ConnectionRequest(method=ConnectionMethod.SERIAL, port="/dev/ttyUSB1"))
        first_interface.close.assert_called_once()
        assert wm.is_connected

    def test_connect_populates_node_id(self, db: MeshDatabase):
        wm = WorkbenchManager(db)
        status = _connect_workbench(wm, node_num=0xAABB1122)
        assert status.node_id == "!aabb1122"


class TestDisconnect:
    def test_disconnect(self, db: MeshDatabase):
        wm = WorkbenchManager(db)
        _connect_workbench(wm)
        assert wm.is_connected

        status = wm.disconnect()
        assert status.connected is False
        assert not wm.is_connected

    def test_disconnect_when_not_connected(self, db: MeshDatabase):
        wm = WorkbenchManager(db)
        status = wm.disconnect()
        assert status.connected is False


class TestGetStatus:
    def test_status_connected(self, db: MeshDatabase):
        wm = WorkbenchManager(db)
        _connect_workbench(wm)

        status = wm.get_status()
        assert status.connected is True
        assert status.node_id == "!aabb1122"
        assert status.long_name == "TestRadio"
        assert status.short_name == "TST"
        assert status.hw_model == "heltec_v3"
        assert status.firmware_version == "2.5.6"
        assert status.uptime_seconds == 3600

    def test_status_not_connected(self, db: MeshDatabase):
        wm = WorkbenchManager(db)
        status = wm.get_status()
        assert status.connected is False
        assert status.node_id is None


# ── Config read ──────────────────────────────────────────────────────


class TestReadConfig:
    def test_read_config_returns_sections(self, db: MeshDatabase):
        wm = WorkbenchManager(db)
        _connect_workbench(wm)

        with patch.object(wm, "_protobuf_to_dict", side_effect=_mock_protobuf_to_dict):
            config = wm.read_config()

        assert isinstance(config, RadioConfig)
        assert len(config.sections) > 0

        section_names = [s.name for s in config.sections]
        assert "device" in section_names
        assert "lora" in section_names
        assert "mqtt" in section_names
        assert "telemetry" in section_names

        # Should have YAML and hash
        assert config.raw_yaml is not None
        assert config.config_hash is not None
        assert len(config.config_hash) == 64

    def test_read_config_caches_result(self, db: MeshDatabase):
        wm = WorkbenchManager(db)
        _connect_workbench(wm)

        with patch.object(wm, "_protobuf_to_dict", side_effect=_mock_protobuf_to_dict):
            config = wm.read_config()

        assert wm._last_read_config is not None
        assert wm._last_read_config.config_hash == config.config_hash

    def test_read_config_not_connected_raises(self, db: MeshDatabase):
        wm = WorkbenchManager(db)
        with pytest.raises(RuntimeError, match="Not connected"):
            wm.read_config()


# ── Config diff ──────────────────────────────────────────────────────


class TestComputeDiff:
    def test_diff_detects_changes(self, db: MeshDatabase):
        wm = WorkbenchManager(db)
        _connect_workbench(wm)

        with patch.object(wm, "_protobuf_to_dict", side_effect=_mock_protobuf_to_dict):
            wm.read_config()

        # Propose a change to the device section
        proposed = [
            ConfigSection(
                name="device",
                fields={"role": 7, "is_managed": True, "node_info_broadcast_secs": 900},
            )
        ]
        diff = wm.compute_diff(proposed)

        assert isinstance(diff, ConfigDiff)
        assert diff.change_count >= 1
        changed_fields = [(c.section, c.field) for c in diff.changes]
        assert ("device", "role") in changed_fields
        assert ("device", "is_managed") in changed_fields

    def test_diff_no_changes(self, db: MeshDatabase):
        wm = WorkbenchManager(db)
        _connect_workbench(wm)

        with patch.object(wm, "_protobuf_to_dict", side_effect=_mock_protobuf_to_dict):
            wm.read_config()

        # Propose identical values
        proposed = [
            ConfigSection(
                name="device",
                fields={"role": 4, "is_managed": False, "node_info_broadcast_secs": 900},
            )
        ]
        diff = wm.compute_diff(proposed)
        assert diff.change_count == 0

    def test_diff_without_read_raises(self, db: MeshDatabase):
        wm = WorkbenchManager(db)
        with pytest.raises(RuntimeError, match="No config read yet"):
            wm.compute_diff([])


# ── Config apply ─────────────────────────────────────────────────────


class TestApplyConfig:
    def test_apply_success(self, db: MeshDatabase):
        wm = WorkbenchManager(db)
        _connect_workbench(wm)

        with patch.object(wm, "_apply_section"), patch.object(wm, "read_config") as mock_read:
            mock_read.return_value = RadioConfig(
                sections=[ConfigSection(name="device", fields={"role": 7})],
                raw_yaml="device:\n  role: 7\n",
                config_hash="a" * 64,
            )
            result = wm.apply_config([ConfigSection(name="device", fields={"role": 7})])

        assert isinstance(result, ApplyResult)
        assert result.success is True
        assert "device" in result.applied_sections

    def test_apply_not_connected(self, db: MeshDatabase):
        wm = WorkbenchManager(db)
        result = wm.apply_config([ConfigSection(name="device", fields={"role": 7})])
        assert result.success is False
        assert "Not connected" in (result.error or "")

    def test_apply_partial_failure(self, db: MeshDatabase):
        """If one section fails, others should still succeed."""
        wm = WorkbenchManager(db)
        _connect_workbench(wm)

        def selective_apply(local_node, section):
            if section.name == "lora":
                raise RuntimeError("LoRa write failed")

        with (
            patch.object(wm, "_apply_section", side_effect=selective_apply),
            patch.object(wm, "read_config") as mock_read,
        ):
            mock_read.return_value = RadioConfig(
                sections=[],
                raw_yaml="",
                config_hash="b" * 64,
            )
            result = wm.apply_config(
                [
                    ConfigSection(name="device", fields={"role": 7}),
                    ConfigSection(name="lora", fields={"region": 1}),
                ]
            )

        assert result.success is False
        assert "device" in result.applied_sections
        assert "lora" in result.failed_sections

    def test_apply_logs_audit_trail(self, db: MeshDatabase):
        wm = WorkbenchManager(db)
        _connect_workbench(wm)

        with patch.object(wm, "_apply_section"), patch.object(wm, "read_config") as mock_read:
            mock_read.return_value = RadioConfig(
                sections=[ConfigSection(name="lora", fields={"region": 1})],
                raw_yaml="lora:\n  region: 1\n",
                config_hash="c" * 64,
            )
            wm.apply_config([ConfigSection(name="lora", fields={"region": 1})])

        # Check provisioning log was written
        with db.connection() as conn:
            logs = conn.execute(
                "SELECT * FROM provisioning_log WHERE action = 'workbench_apply'"
            ).fetchall()
        assert len(logs) >= 1


# ── Save as template ─────────────────────────────────────────────────


class TestSaveAsTemplate:
    def test_save_template_success(self, db: MeshDatabase, tmp_path: Path):
        configs_dir = tmp_path / "configs"
        configs_dir.mkdir()
        wm = WorkbenchManager(db, configs_dir=configs_dir)
        _connect_workbench(wm)

        # Inject a cached config by reading
        with patch.object(wm, "_protobuf_to_dict", side_effect=_mock_protobuf_to_dict):
            wm.read_config()

        request = SaveTemplateRequest(
            template_name="my-test-template",
            description="Test workbench save",
        )
        result = wm.save_as_template(request)

        assert result.success is True
        assert result.template_name == "my-test-template"
        assert len(result.config_hash) == 64
        assert result.yaml_path is not None
        assert Path(result.yaml_path).exists()

        # Verify YAML content
        saved_yaml = Path(result.yaml_path).read_text()
        parsed = yaml.safe_load(saved_yaml)
        assert "device" in parsed

    def test_save_duplicate_name_fails(self, db: MeshDatabase, tmp_path: Path):
        configs_dir = tmp_path / "configs"
        configs_dir.mkdir()
        wm = WorkbenchManager(db, configs_dir=configs_dir)
        _connect_workbench(wm)

        with patch.object(wm, "_protobuf_to_dict", side_effect=_mock_protobuf_to_dict):
            wm.read_config()

        request = SaveTemplateRequest(template_name="dupe-template")
        result1 = wm.save_as_template(request)
        assert result1.success is True

        # Second save with same name should fail
        result2 = wm.save_as_template(request)
        assert result2.success is False
        assert "already exists" in (result2.error or "")

    def test_save_not_connected_no_cache(self, db: MeshDatabase, tmp_path: Path):
        wm = WorkbenchManager(db, configs_dir=tmp_path / "configs")
        request = SaveTemplateRequest(template_name="orphan-template")
        result = wm.save_as_template(request)
        assert result.success is False
        assert "Not connected" in (result.error or "")

    def test_save_logs_audit_trail(self, db: MeshDatabase, tmp_path: Path):
        configs_dir = tmp_path / "configs"
        configs_dir.mkdir()
        wm = WorkbenchManager(db, configs_dir=configs_dir)
        _connect_workbench(wm)

        with patch.object(wm, "_protobuf_to_dict", side_effect=_mock_protobuf_to_dict):
            wm.read_config()

        request = SaveTemplateRequest(template_name="audit-template")
        wm.save_as_template(request)

        with db.connection() as conn:
            logs = conn.execute(
                "SELECT * FROM provisioning_log WHERE action = 'workbench_save_template'"
            ).fetchall()
        assert len(logs) >= 1
