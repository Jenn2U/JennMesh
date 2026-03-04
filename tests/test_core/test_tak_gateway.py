"""Tests for the TAK gateway — CoT translation, XML generation/parsing."""

from __future__ import annotations

import tempfile
from xml.etree.ElementTree import fromstring

import pytest

from jenn_mesh.core.tak_gateway import TakGateway
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.tak import CotEvent, CotType, TakConnectionStatus


@pytest.fixture
def db(tmp_path) -> MeshDatabase:
    return MeshDatabase(db_path=str(tmp_path / "tak_test.db"))


@pytest.fixture
def gateway(db) -> TakGateway:
    return TakGateway(db=db)


# ── Configuration ────────────────────────────────────────────────────


class TestConfiguration:
    def test_no_config_by_default(self, gateway):
        assert gateway.get_config() is None

    def test_configure(self, gateway):
        config = gateway.configure(host="tak.example.com", port=8087)
        assert config.host == "tak.example.com"
        assert config.port == 8087
        assert config.callsign_prefix == "JENN-"

    def test_config_persists(self, gateway):
        gateway.configure(host="tak.example.com", port=9999, use_tls=True)
        config = gateway.get_config()
        assert config is not None
        assert config.host == "tak.example.com"
        assert config.port == 9999
        assert config.use_tls is True

    def test_configure_custom_prefix(self, gateway):
        config = gateway.configure(host="tak.local", callsign_prefix="MESH-")
        assert config.callsign_prefix == "MESH-"

    def test_status_reflects_config(self, gateway):
        gateway.configure(host="tak.example.com", port=8087)
        status = gateway.get_status()
        assert status.server_host == "tak.example.com"
        assert status.server_port == 8087


# ── Position → CoT translation ───────────────────────────────────────


class TestTranslatePosition:
    def test_basic_translation(self, gateway):
        event = gateway.translate_position_to_cot(
            node_id="!2a3b4c5d",
            latitude=32.123,
            longitude=-96.789,
            altitude=150.0,
        )
        assert event.uid == "JENN-MESH-2a3b4c5d"
        assert event.callsign == "JENN-2a3b4c5d"  # 8 chars after strip
        assert event.latitude == 32.123
        assert event.longitude == -96.789
        assert event.altitude == 150.0
        assert event.cot_type == CotType.FRIENDLY_GROUND.value

    def test_translation_with_battery(self, gateway):
        event = gateway.translate_position_to_cot(
            node_id="!abc",
            latitude=30.0,
            longitude=-97.0,
            battery=75,
        )
        assert event.battery == 75
        assert "75%" in event.remarks

    def test_translation_with_speed(self, gateway):
        event = gateway.translate_position_to_cot(
            node_id="!abc",
            latitude=30.0,
            longitude=-97.0,
            speed=5.5,
            course=180.0,
        )
        assert event.speed == 5.5
        assert event.course == 180.0

    def test_custom_cot_type(self, gateway):
        event = gateway.translate_position_to_cot(
            node_id="!abc",
            latitude=30.0,
            longitude=-97.0,
            cot_type=CotType.RELAY.value,
        )
        assert event.cot_type == "a-f-G-U-C-I"

    def test_translation_logs_event(self, gateway):
        gateway.translate_position_to_cot(node_id="!abc", latitude=30.0, longitude=-97.0)
        events = gateway.list_events()
        assert len(events) == 1
        assert events[0]["direction"] == "outbound"
        assert events[0]["node_id"] == "!abc"

    def test_custom_prefix_from_config(self, gateway):
        gateway.configure(host="tak.local", callsign_prefix="OPS-")
        event = gateway.translate_position_to_cot(node_id="!abc123", latitude=30.0, longitude=-97.0)
        assert event.callsign.startswith("OPS-")


# ── CoT XML generation ───────────────────────────────────────────────


class TestCotToXml:
    def test_xml_structure(self, gateway):
        event = gateway.translate_position_to_cot(
            node_id="!abc",
            latitude=32.123,
            longitude=-96.789,
            altitude=150.0,
        )
        xml_str = TakGateway.cot_to_xml(event)
        root = fromstring(xml_str)
        assert root.tag == "event"
        assert root.get("version") == "2.0"
        assert root.get("type") == CotType.FRIENDLY_GROUND.value
        assert root.get("how") == "m-g"

    def test_xml_point_element(self, gateway):
        event = gateway.translate_position_to_cot(
            node_id="!abc",
            latitude=32.1234567,
            longitude=-96.7890123,
            altitude=150.5,
        )
        xml_str = TakGateway.cot_to_xml(event)
        root = fromstring(xml_str)
        point = root.find("point")
        assert point is not None
        assert float(point.get("lat")) == pytest.approx(32.1234567, abs=1e-6)
        assert float(point.get("lon")) == pytest.approx(-96.7890123, abs=1e-6)
        assert float(point.get("hae")) == pytest.approx(150.5, abs=0.1)

    def test_xml_contact_callsign(self, gateway):
        event = gateway.translate_position_to_cot(node_id="!abc", latitude=30.0, longitude=-97.0)
        xml_str = TakGateway.cot_to_xml(event)
        root = fromstring(xml_str)
        contact = root.find(".//contact")
        assert contact is not None
        assert contact.get("callsign").startswith("JENN-")

    def test_xml_battery_status(self, gateway):
        event = gateway.translate_position_to_cot(
            node_id="!abc", latitude=30.0, longitude=-97.0, battery=80
        )
        xml_str = TakGateway.cot_to_xml(event)
        root = fromstring(xml_str)
        status = root.find(".//status")
        assert status is not None
        assert status.get("battery") == "80"


# ── CoT XML parsing ──────────────────────────────────────────────────


class TestParseCotXml:
    def test_roundtrip(self, gateway):
        event = gateway.translate_position_to_cot(
            node_id="!abc",
            latitude=32.123,
            longitude=-96.789,
            altitude=150.0,
            battery=75,
        )
        xml_str = TakGateway.cot_to_xml(event)
        parsed = TakGateway.parse_cot_xml(xml_str)
        assert parsed is not None
        assert parsed.uid == event.uid
        assert parsed.latitude == pytest.approx(32.123, abs=1e-6)
        assert parsed.longitude == pytest.approx(-96.789, abs=1e-6)
        assert parsed.battery == 75

    def test_parse_minimal_xml(self):
        xml = (
            '<event version="2.0" uid="test-1" type="a-f-G" time="2025-01-01T00:00:00Z"'
            ' start="2025-01-01T00:00:00Z" stale="2025-01-01T00:10:00Z" how="m-g">'
            '<point lat="30.0" lon="-97.0" hae="100.0" ce="50" le="50"/>'
            '<detail><contact callsign="TEST-1"/></detail>'
            "</event>"
        )
        event = TakGateway.parse_cot_xml(xml)
        assert event is not None
        assert event.uid == "test-1"
        assert event.callsign == "TEST-1"
        assert event.latitude == 30.0
        assert event.longitude == -97.0

    def test_parse_invalid_xml(self):
        assert TakGateway.parse_cot_xml("not xml") is None

    def test_parse_no_point(self):
        xml = '<event version="2.0" uid="test" type="a-f-G"></event>'
        assert TakGateway.parse_cot_xml(xml) is None


# ── Gateway status ───────────────────────────────────────────────────


class TestGatewayStatus:
    def test_initial_status(self, gateway):
        status = gateway.get_status()
        assert status.connection_status == TakConnectionStatus.DISCONNECTED
        assert status.events_sent == 0

    def test_status_after_translation(self, gateway):
        gateway.translate_position_to_cot(node_id="!abc", latitude=30.0, longitude=-97.0)
        gateway.translate_position_to_cot(node_id="!def", latitude=31.0, longitude=-98.0)
        status = gateway.get_status()
        assert status.events_sent >= 2


# ── Event listing ────────────────────────────────────────────────────


class TestListEvents:
    def test_list_with_direction_filter(self, gateway):
        gateway.translate_position_to_cot(node_id="!abc", latitude=30.0, longitude=-97.0)
        outbound = gateway.list_events(direction="outbound")
        assert len(outbound) == 1
        inbound = gateway.list_events(direction="inbound")
        assert len(inbound) == 0

    def test_list_with_node_filter(self, gateway):
        gateway.translate_position_to_cot(node_id="!abc", latitude=30.0, longitude=-97.0)
        gateway.translate_position_to_cot(node_id="!def", latitude=31.0, longitude=-98.0)
        filtered = gateway.list_events(node_id="!abc")
        assert len(filtered) == 1
