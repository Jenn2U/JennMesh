"""Tests for the EnvTelemetryManager core module."""

from __future__ import annotations

from pathlib import Path

import pytest

from jenn_mesh.core.env_telemetry import EnvTelemetryManager
from jenn_mesh.db import MeshDatabase


@pytest.fixture
def db(tmp_path: Path) -> MeshDatabase:
    return MeshDatabase(db_path=str(tmp_path / "test.db"))


@pytest.fixture
def manager(db: MeshDatabase) -> EnvTelemetryManager:
    return EnvTelemetryManager(db=db)


# ── Ingestion ─────────────────────────────────────────────────────


class TestIngestion:
    def test_ingest_normal_reading(self, manager: EnvTelemetryManager, db: MeshDatabase):
        """Normal reading is stored without triggering alerts."""
        # Seed the device first
        db.upsert_device("!aaa11111", long_name="Test")
        alerts = manager.ingest_reading(
            "!aaa11111", temperature=22.0, humidity=55.0, pressure=1013.0
        )
        assert alerts == []
        readings = manager.get_node_readings("!aaa11111")
        assert len(readings) == 1
        assert readings[0]["temperature"] == 22.0

    def test_ingest_partial_reading(self, manager: EnvTelemetryManager, db: MeshDatabase):
        """Partial reading (only some fields) is accepted."""
        db.upsert_device("!aaa11111", long_name="Test")
        alerts = manager.ingest_reading("!aaa11111", temperature=25.0)
        assert alerts == []
        readings = manager.get_node_readings("!aaa11111")
        assert readings[0]["humidity"] is None
        assert readings[0]["pressure"] is None

    def test_ingest_high_temperature_alert(self, manager: EnvTelemetryManager, db: MeshDatabase):
        """Temperature above max threshold triggers alert."""
        db.upsert_device("!aaa11111", long_name="Test")
        alerts = manager.ingest_reading("!aaa11111", temperature=65.0)
        assert len(alerts) == 1
        assert alerts[0].metric == "temperature"
        assert alerts[0].value == 65.0
        assert "exceeds" in alerts[0].message

    def test_ingest_low_temperature_alert(self, manager: EnvTelemetryManager, db: MeshDatabase):
        """Temperature below min threshold triggers alert."""
        db.upsert_device("!aaa11111", long_name="Test")
        alerts = manager.ingest_reading("!aaa11111", temperature=-25.0)
        assert len(alerts) == 1
        assert alerts[0].metric == "temperature"
        assert "below" in alerts[0].message

    def test_ingest_poor_air_quality_alert(self, manager: EnvTelemetryManager, db: MeshDatabase):
        """Air quality above max threshold triggers alert."""
        db.upsert_device("!aaa11111", long_name="Test")
        alerts = manager.ingest_reading("!aaa11111", air_quality=350)
        assert len(alerts) == 1
        assert alerts[0].metric == "air_quality"

    def test_ingest_multiple_breaches(self, manager: EnvTelemetryManager, db: MeshDatabase):
        """Multiple threshold breaches in one reading."""
        db.upsert_device("!aaa11111", long_name="Test")
        alerts = manager.ingest_reading("!aaa11111", temperature=70.0, air_quality=400)
        assert len(alerts) == 2
        metrics = {a.metric for a in alerts}
        assert metrics == {"temperature", "air_quality"}

    def test_alert_creates_fleet_alert(self, manager: EnvTelemetryManager, db: MeshDatabase):
        """Threshold breach also creates a fleet alert in the alerts table."""
        db.upsert_device("!aaa11111", long_name="Test")
        manager.ingest_reading("!aaa11111", temperature=65.0)
        fleet_alerts = db.get_active_alerts()
        env_alerts = [a for a in fleet_alerts if a.get("alert_type") == "env_threshold_exceeded"]
        assert len(env_alerts) == 1


# ── Threshold Management ──────────────────────────────────────────


class TestThresholds:
    def test_get_default_thresholds(self, manager: EnvTelemetryManager):
        """Default thresholds are present."""
        thresholds = manager.get_thresholds()
        assert len(thresholds) == 4
        metrics = {t["metric"] for t in thresholds}
        assert metrics == {"temperature", "humidity", "pressure", "air_quality"}

    def test_update_thresholds(self, manager: EnvTelemetryManager):
        """Thresholds can be replaced."""
        new_thresholds = [
            {"metric": "temperature", "min_value": -10.0, "max_value": 50.0, "enabled": True}
        ]
        result = manager.update_thresholds(new_thresholds)
        assert len(result) == 1
        assert result[0]["max_value"] == 50.0

    def test_disabled_threshold_no_alert(self, manager: EnvTelemetryManager, db: MeshDatabase):
        """Disabled threshold doesn't trigger alerts."""
        db.upsert_device("!aaa11111", long_name="Test")
        manager.update_thresholds(
            [{"metric": "temperature", "min_value": -20.0, "max_value": 60.0, "enabled": False}]
        )
        alerts = manager.ingest_reading("!aaa11111", temperature=70.0)
        assert alerts == []


# ── Query ─────────────────────────────────────────────────────────


class TestQuery:
    def test_get_node_readings(self, manager: EnvTelemetryManager, db: MeshDatabase):
        """Readings are returned in reverse chronological order."""
        db.upsert_device("!aaa11111", long_name="Test")
        manager.ingest_reading("!aaa11111", temperature=20.0)
        manager.ingest_reading("!aaa11111", temperature=25.0)
        readings = manager.get_node_readings("!aaa11111")
        assert len(readings) == 2
        # Most recent first
        assert readings[0]["temperature"] == 25.0

    def test_get_fleet_summary(self, manager: EnvTelemetryManager, db: MeshDatabase):
        """Fleet summary aggregates across nodes."""
        db.upsert_device("!aaa11111", long_name="Node A")
        db.upsert_device("!bbb22222", long_name="Node B")
        manager.ingest_reading("!aaa11111", temperature=20.0)
        manager.ingest_reading("!bbb22222", temperature=30.0)
        summary = manager.get_fleet_summary()
        assert summary["node_count"] == 2
        assert summary["avg_temperature"] == 25.0

    def test_get_env_alerts_empty(self, manager: EnvTelemetryManager):
        """No alerts when no breaches."""
        alerts = manager.get_env_alerts()
        assert alerts == []


# ── Status ────────────────────────────────────────────────────────


class TestStatus:
    def test_status(self, manager: EnvTelemetryManager):
        status = manager.get_status()
        assert status["enabled"] is True
        assert status["threshold_count"] == 4
        assert status["active_thresholds"] == 4
