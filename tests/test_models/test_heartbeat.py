"""Tests for heartbeat models — parsing, serialization, validation."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from jenn_mesh.models.heartbeat import HeartbeatSummary, MeshHeartbeat, ServiceStatus

# ── ServiceStatus ────────────────────────────────────────────────────


class TestServiceStatus:
    def test_basic_creation(self):
        s = ServiceStatus(name="edge", status="ok")
        assert s.name == "edge"
        assert s.status == "ok"

    def test_down_status(self):
        s = ServiceStatus(name="mqtt", status="down")
        assert s.status == "down"


# ── MeshHeartbeat wire format parsing ────────────────────────────────


class TestServicesStringParsing:
    def test_parse_single_service(self):
        result = MeshHeartbeat.parse_services_string("edge:ok")
        assert len(result) == 1
        assert result[0].name == "edge"
        assert result[0].status == "ok"

    def test_parse_multiple_services(self):
        result = MeshHeartbeat.parse_services_string("edge:ok,mqtt:down,internet:down")
        assert len(result) == 3
        assert result[0].name == "edge"
        assert result[1].status == "down"
        assert result[2].name == "internet"

    def test_parse_empty_string(self):
        assert MeshHeartbeat.parse_services_string("") == []

    def test_parse_malformed_pair(self):
        """Pairs without exactly one colon are silently skipped."""
        result = MeshHeartbeat.parse_services_string("edge:ok,baddata,mqtt:down")
        assert len(result) == 2
        assert result[0].name == "edge"
        assert result[1].name == "mqtt"

    def test_format_services_round_trip(self):
        services = [
            ServiceStatus(name="edge", status="ok"),
            ServiceStatus(name="mqtt", status="down"),
        ]
        wire = MeshHeartbeat.format_services_string(services)
        assert wire == "edge:ok,mqtt:down"
        parsed = MeshHeartbeat.parse_services_string(wire)
        assert len(parsed) == 2
        assert parsed[0].name == "edge"
        assert parsed[1].status == "down"


# ── MeshHeartbeat JSON serialization (DB storage) ────────────────────


class TestServicesJsonSerialization:
    def test_services_json_round_trip(self):
        hb = MeshHeartbeat(
            node_id="!aaa11111",
            uptime_seconds=3600,
            services=[
                ServiceStatus(name="edge", status="ok"),
                ServiceStatus(name="mqtt", status="down"),
            ],
            battery=85,
            timestamp=datetime(2026, 3, 2, 15, 30, 0),
        )
        json_str = hb.services_json()
        parsed = json.loads(json_str)
        assert len(parsed) == 2
        assert parsed[0]["name"] == "edge"

        # Deserialize back
        services = MeshHeartbeat.services_from_json(json_str)
        assert len(services) == 2
        assert services[1].status == "down"

    def test_services_from_json_invalid(self):
        """Invalid JSON returns empty list."""
        assert MeshHeartbeat.services_from_json("not json") == []

    def test_services_from_json_empty_array(self):
        assert MeshHeartbeat.services_from_json("[]") == []


# ── MeshHeartbeat model validation ───────────────────────────────────


class TestMeshHeartbeatValidation:
    def test_valid_heartbeat(self):
        hb = MeshHeartbeat(
            node_id="!28979058",
            uptime_seconds=7200,
            battery=42,
            timestamp=datetime(2026, 3, 2, 15, 30, 0),
        )
        assert hb.node_id == "!28979058"
        assert hb.uptime_seconds == 7200
        assert hb.battery == 42
        assert hb.services == []

    def test_battery_minus_one_for_unknown(self):
        hb = MeshHeartbeat(
            node_id="!aaa11111",
            uptime_seconds=100,
            battery=-1,
            timestamp=datetime(2026, 3, 2, 15, 0, 0),
        )
        assert hb.battery == -1

    def test_battery_range_validation(self):
        """Battery must be -1 to 100."""
        with pytest.raises(Exception):
            MeshHeartbeat(
                node_id="!aaa11111",
                uptime_seconds=100,
                battery=150,
                timestamp=datetime(2026, 3, 2, 15, 0, 0),
            )

    def test_uptime_non_negative(self):
        """Uptime must be >= 0."""
        with pytest.raises(Exception):
            MeshHeartbeat(
                node_id="!aaa11111",
                uptime_seconds=-10,
                battery=50,
                timestamp=datetime(2026, 3, 2, 15, 0, 0),
            )

    def test_optional_signal_fields(self):
        hb = MeshHeartbeat(
            node_id="!aaa11111",
            uptime_seconds=100,
            timestamp=datetime(2026, 3, 2, 15, 0, 0),
            rssi=-85,
            snr=10.5,
        )
        assert hb.rssi == -85
        assert hb.snr == 10.5

    def test_received_at_defaults(self):
        hb = MeshHeartbeat(
            node_id="!aaa11111",
            uptime_seconds=100,
            timestamp=datetime(2026, 3, 2, 15, 0, 0),
        )
        assert hb.received_at is not None


# ── HeartbeatSummary ─────────────────────────────────────────────────


class TestHeartbeatSummary:
    def test_default_values(self):
        summary = HeartbeatSummary(node_id="!aaa11111")
        assert summary.heartbeat_count == 0
        assert summary.is_mesh_reachable is False
        assert summary.last_heartbeat is None
        assert summary.avg_interval_seconds is None

    def test_with_data(self):
        summary = HeartbeatSummary(
            node_id="!aaa11111",
            heartbeat_count=50,
            is_mesh_reachable=True,
            avg_interval_seconds=120.5,
            last_heartbeat=datetime(2026, 3, 2, 15, 30, 0),
        )
        assert summary.heartbeat_count == 50
        assert summary.is_mesh_reachable is True
