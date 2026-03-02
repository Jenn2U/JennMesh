"""Tests for mesh device models."""

from jenn_mesh.models.device import ConfigHash, DeviceRole, FirmwareInfo, MeshDevice


class TestDeviceRole:
    def test_relay_maps_to_router(self):
        assert DeviceRole.RELAY.value == "ROUTER"

    def test_gateway_maps_to_client_mute(self):
        assert DeviceRole.GATEWAY.value == "CLIENT_MUTE"

    def test_from_meshtastic_known_role(self):
        assert DeviceRole.from_meshtastic("ROUTER") == DeviceRole.RELAY
        assert DeviceRole.from_meshtastic("CLIENT_MUTE") == DeviceRole.GATEWAY
        assert DeviceRole.from_meshtastic("CLIENT") == DeviceRole.MOBILE
        assert DeviceRole.from_meshtastic("SENSOR") == DeviceRole.SENSOR

    def test_from_meshtastic_unknown_defaults_to_mobile(self):
        assert DeviceRole.from_meshtastic("UNKNOWN_THING") == DeviceRole.MOBILE


class TestConfigHash:
    def test_compute_sha256(self):
        h = ConfigHash.compute("some yaml content")
        assert len(h) == 64  # SHA-256 hex digest
        assert h == ConfigHash.compute("some yaml content")  # Deterministic

    def test_different_content_different_hash(self):
        assert ConfigHash.compute("config A") != ConfigHash.compute("config B")


class TestFirmwareInfo:
    def test_defaults(self):
        fw = FirmwareInfo(version="2.5.6")
        assert fw.hw_model == "unknown"
        assert fw.needs_update is False
        assert fw.latest_available is None

    def test_needs_update_flag(self):
        fw = FirmwareInfo(version="2.4.0", needs_update=True, latest_available="2.5.6")
        assert fw.needs_update is True


class TestMeshDevice:
    def test_display_name_prefers_long_name(self):
        d = MeshDevice(node_id="!abc", long_name="My Radio", firmware=FirmwareInfo(version="2.5"))
        assert d.display_name == "My Radio"

    def test_display_name_falls_back_to_node_id(self):
        d = MeshDevice(node_id="!abc", firmware=FirmwareInfo(version="2.5"))
        assert d.display_name == "!abc"

    def test_battery_validation(self):
        d = MeshDevice(node_id="!abc", battery_level=50, firmware=FirmwareInfo(version="2.5"))
        assert d.battery_level == 50

    def test_default_role_is_mobile(self):
        d = MeshDevice(node_id="!abc", firmware=FirmwareInfo(version="2.5"))
        assert d.role == DeviceRole.MOBILE

    def test_associated_edge_node(self):
        d = MeshDevice(
            node_id="!abc",
            associated_edge_node="edge-pi4-01",
            firmware=FirmwareInfo(version="2.5"),
        )
        assert d.associated_edge_node == "edge-pi4-01"
