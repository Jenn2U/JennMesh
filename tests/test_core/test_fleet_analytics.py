"""Tests for fleet analytics — time-series aggregation and trend computation."""

from __future__ import annotations

from jenn_mesh.core.fleet_analytics import FleetAnalytics
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.fleet import ALERT_SEVERITY_MAP, AlertType

# ── Helpers ─────────────────────────────────────────────────────────


def _seed_alerts(db: MeshDatabase, count: int = 5) -> None:
    """Seed active alerts into the database."""
    types = [AlertType.LOW_BATTERY, AlertType.SIGNAL_DEGRADED, AlertType.NODE_OFFLINE]
    for i in range(count):
        atype = types[i % len(types)]
        severity = ALERT_SEVERITY_MAP[atype].value
        db.create_alert(
            node_id="!aaa11111",
            alert_type=atype.value,
            severity=severity,
            message=f"Alert {i}",
        )


# ── Init ────────────────────────────────────────────────────────────


class TestFleetAnalyticsInit:
    def test_init(self, populated_db: MeshDatabase):
        analytics = FleetAnalytics(populated_db)
        assert analytics.db is populated_db


# ── Uptime Trends ──────────────────────────────────────────────────


class TestUptimeTrends:
    def test_all_nodes(self, populated_db: MeshDatabase):
        analytics = FleetAnalytics(populated_db)
        trends = analytics.get_uptime_trends()
        assert len(trends) == 4  # 4 devices in populated_db
        for t in trends:
            assert "node_id" in t
            assert "uptime_pct" in t
            assert 0 <= t["uptime_pct"] <= 100

    def test_single_node(self, populated_db: MeshDatabase):
        analytics = FleetAnalytics(populated_db)
        trends = analytics.get_uptime_trends(node_id="!aaa11111")
        assert len(trends) == 1
        assert trends[0]["node_id"] == "!aaa11111"

    def test_nonexistent_node(self, populated_db: MeshDatabase):
        analytics = FleetAnalytics(populated_db)
        trends = analytics.get_uptime_trends(node_id="!nonexistent")
        assert trends == []

    def test_sorted_by_uptime(self, populated_db: MeshDatabase):
        analytics = FleetAnalytics(populated_db)
        trends = analytics.get_uptime_trends()
        uptimes = [t["uptime_pct"] for t in trends]
        assert uptimes == sorted(uptimes, reverse=True)

    def test_custom_days(self, populated_db: MeshDatabase):
        """Wider window includes samples; narrower window may exclude them."""
        analytics = FleetAnalytics(populated_db)
        # Samples are ~6 days old, so days=30 captures them but days=1 doesn't
        trends_wide = analytics.get_uptime_trends(days=30)
        trends_narrow = analytics.get_uptime_trends(days=1)
        node_wide = [t for t in trends_wide if t["node_id"] == "!aaa11111"]
        node_narrow = [t for t in trends_narrow if t["node_id"] == "!aaa11111"]
        # days=30 should find the 6-day-old samples → uptime_pct > 0
        assert node_wide[0]["sample_count"] > 0
        # days=1 should miss 6-day-old samples → sample_count == 0
        assert node_narrow[0]["sample_count"] == 0


# ── Battery Trends ─────────────────────────────────────────────────


class TestBatteryTrends:
    def test_all_nodes(self, populated_db: MeshDatabase):
        analytics = FleetAnalytics(populated_db)
        trends = analytics.get_battery_trends()
        assert len(trends) == 4

    def test_single_node(self, populated_db: MeshDatabase):
        analytics = FleetAnalytics(populated_db)
        trends = analytics.get_battery_trends(node_id="!aaa11111")
        assert len(trends) == 1
        assert trends[0]["node_id"] == "!aaa11111"

    def test_trend_direction(self, populated_db: MeshDatabase):
        """Populated DB chronologically: oldest=61, newest=80 → rising trend."""
        analytics = FleetAnalytics(populated_db)
        trends = analytics.get_battery_trends(node_id="!aaa11111")
        node = trends[0]
        assert node["sample_count"] > 0
        # Conftest inserts oldest (i=19, battery=61) → newest (i=0, battery=80)
        # Chronological order: 61→80 = rising
        assert node["trend"] == "rising"

    def test_no_samples_unknown_trend(self, populated_db: MeshDatabase):
        """Node with no telemetry has unknown trend."""
        analytics = FleetAnalytics(populated_db)
        trends = analytics.get_battery_trends(node_id="!ddd44444")
        assert trends[0]["trend"] == "unknown"


# ── Alert Frequency ────────────────────────────────────────────────


class TestAlertFrequency:
    def test_no_alerts(self, populated_db: MeshDatabase):
        analytics = FleetAnalytics(populated_db)
        freq = analytics.get_alert_frequency()
        assert freq["total"] == 0
        assert freq["by_type"] == {}
        assert freq["by_severity"] == {}

    def test_with_alerts(self, populated_db: MeshDatabase):
        _seed_alerts(populated_db, 6)
        analytics = FleetAnalytics(populated_db)
        freq = analytics.get_alert_frequency()
        assert freq["total"] == 6
        assert len(freq["by_type"]) > 0
        assert len(freq["by_severity"]) > 0
        # Total across types should sum to total
        assert sum(freq["by_type"].values()) == 6

    def test_alert_type_counts(self, populated_db: MeshDatabase):
        _seed_alerts(populated_db, 3)  # One of each type
        analytics = FleetAnalytics(populated_db)
        freq = analytics.get_alert_frequency()
        assert freq["by_type"][AlertType.LOW_BATTERY.value] == 1
        assert freq["by_type"][AlertType.SIGNAL_DEGRADED.value] == 1
        assert freq["by_type"][AlertType.NODE_OFFLINE.value] == 1


# ── Message Volume ─────────────────────────────────────────────────


class TestMessageVolume:
    def test_all_nodes(self, populated_db: MeshDatabase):
        analytics = FleetAnalytics(populated_db)
        volumes = analytics.get_message_volume()
        assert len(volumes) == 4
        for v in volumes:
            assert "node_id" in v
            assert "message_count" in v

    def test_sorted_by_count(self, populated_db: MeshDatabase):
        analytics = FleetAnalytics(populated_db)
        volumes = analytics.get_message_volume()
        counts = [v["message_count"] for v in volumes]
        assert counts == sorted(counts, reverse=True)

    def test_nodes_with_samples_have_counts(self, populated_db: MeshDatabase):
        analytics = FleetAnalytics(populated_db)
        volumes = analytics.get_message_volume(days=30)
        # !aaa11111 and !bbb22222 have telemetry, others don't
        by_node = {v["node_id"]: v["message_count"] for v in volumes}
        assert by_node["!aaa11111"] > 0
        assert by_node["!bbb22222"] > 0


# ── Fleet Growth ───────────────────────────────────────────────────


class TestFleetGrowth:
    def test_role_distribution(self, populated_db: MeshDatabase):
        analytics = FleetAnalytics(populated_db)
        growth = analytics.get_fleet_growth()
        roles = {g["role"]: g["count"] for g in growth}
        # populated_db has: ROUTER, CLIENT_MUTE, CLIENT, SENSOR
        assert roles["ROUTER"] == 1
        assert roles["CLIENT_MUTE"] == 1
        assert roles["CLIENT"] == 1
        assert roles["SENSOR"] == 1

    def test_empty_db(self, db: MeshDatabase):
        analytics = FleetAnalytics(db)
        growth = analytics.get_fleet_growth()
        assert growth == []


# ── Dashboard Summary ──────────────────────────────────────────────


class TestDashboardSummary:
    def test_structure(self, populated_db: MeshDatabase):
        analytics = FleetAnalytics(populated_db)
        summary = analytics.get_dashboard_summary()
        assert "fleet" in summary
        assert "alerts" in summary
        assert "roles" in summary
        assert "coverage" in summary

    def test_fleet_section(self, populated_db: MeshDatabase):
        analytics = FleetAnalytics(populated_db)
        summary = analytics.get_dashboard_summary()
        fleet = summary["fleet"]
        assert fleet["total_devices"] == 4
        assert fleet["online"] + fleet["offline"] == 4
        assert 0 <= fleet["online_pct"] <= 100

    def test_alerts_section(self, populated_db: MeshDatabase):
        _seed_alerts(populated_db, 3)
        analytics = FleetAnalytics(populated_db)
        summary = analytics.get_dashboard_summary()
        assert summary["alerts"]["total_active"] == 3
        assert len(summary["alerts"]["by_severity"]) > 0

    def test_roles_section(self, populated_db: MeshDatabase):
        analytics = FleetAnalytics(populated_db)
        summary = analytics.get_dashboard_summary()
        total = sum(r["count"] for r in summary["roles"])
        assert total == 4

    def test_coverage_section(self, populated_db: MeshDatabase):
        analytics = FleetAnalytics(populated_db)
        summary = analytics.get_dashboard_summary()
        assert "total_samples" in summary["coverage"]
        assert "avg_rssi" in summary["coverage"]
