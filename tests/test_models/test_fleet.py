"""Tests for fleet health models."""

from jenn_mesh.models.fleet import (
    ALERT_SEVERITY_MAP,
    Alert,
    AlertSeverity,
    AlertType,
    FleetHealth,
)


class TestAlertType:
    def test_all_alert_types_have_severity_mapping(self):
        for at in AlertType:
            assert at in ALERT_SEVERITY_MAP

    def test_offline_is_critical(self):
        assert ALERT_SEVERITY_MAP[AlertType.NODE_OFFLINE] == AlertSeverity.CRITICAL

    def test_low_battery_is_warning(self):
        assert ALERT_SEVERITY_MAP[AlertType.LOW_BATTERY] == AlertSeverity.WARNING


class TestAlert:
    def test_active_alert(self):
        a = Alert(
            node_id="!abc",
            alert_type=AlertType.LOW_BATTERY,
            severity=AlertSeverity.WARNING,
            message="Battery low: 15%",
        )
        assert a.is_active is True
        assert a.is_resolved is False

    def test_resolved_alert(self):
        a = Alert(
            node_id="!abc",
            alert_type=AlertType.LOW_BATTERY,
            severity=AlertSeverity.WARNING,
            message="Battery low",
            is_resolved=True,
        )
        assert a.is_active is False


class TestFleetHealth:
    def test_health_score_full_online(self):
        fh = FleetHealth(total_devices=10, online_count=10)
        assert fh.health_score == 100.0

    def test_health_score_half_online(self):
        fh = FleetHealth(total_devices=10, online_count=5)
        assert fh.health_score == 50.0

    def test_health_score_empty_fleet(self):
        fh = FleetHealth()
        assert fh.health_score == 100.0  # No devices = healthy

    def test_health_score_none_online(self):
        fh = FleetHealth(total_devices=5, online_count=0)
        assert fh.health_score == 0.0
