"""Tests for v0.4.0 models — geofence, coverage, and updated fleet AlertTypes."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jenn_mesh.models.coverage import (
    CoverageGridCell,
    CoverageHeatmap,
    CoverageSample,
    CoverageStats,
)
from jenn_mesh.models.fleet import ALERT_SEVERITY_MAP, AlertSeverity, AlertType
from jenn_mesh.models.geofence import FenceType, GeoFence, GeoFenceCheck, GeoFenceEvent, TriggerOn


class TestNewAlertTypes:
    """Verify 6 new AlertType values and their severity mappings."""

    def test_anomaly_detected_exists(self):
        assert AlertType.ANOMALY_DETECTED == "anomaly_detected"

    def test_geofence_breach_exists(self):
        assert AlertType.GEOFENCE_BREACH == "geofence_breach"

    def test_geofence_dwell_exists(self):
        assert AlertType.GEOFENCE_DWELL == "geofence_dwell"

    def test_coverage_gap_exists(self):
        assert AlertType.COVERAGE_GAP == "coverage_gap"

    def test_coverage_degraded_exists(self):
        assert AlertType.COVERAGE_DEGRADED == "coverage_degraded"

    def test_env_threshold_exceeded_exists(self):
        assert AlertType.ENV_THRESHOLD_EXCEEDED == "env_threshold_exceeded"

    def test_new_alert_severity_mappings(self):
        assert ALERT_SEVERITY_MAP[AlertType.ANOMALY_DETECTED] == AlertSeverity.WARNING
        assert ALERT_SEVERITY_MAP[AlertType.GEOFENCE_BREACH] == AlertSeverity.WARNING
        assert ALERT_SEVERITY_MAP[AlertType.GEOFENCE_DWELL] == AlertSeverity.INFO
        assert ALERT_SEVERITY_MAP[AlertType.COVERAGE_GAP] == AlertSeverity.INFO
        assert ALERT_SEVERITY_MAP[AlertType.COVERAGE_DEGRADED] == AlertSeverity.WARNING
        assert ALERT_SEVERITY_MAP[AlertType.ENV_THRESHOLD_EXCEEDED] == AlertSeverity.WARNING

    def test_all_alert_types_have_severity(self):
        """Every AlertType must have a severity mapping — prevents orphans."""
        for at in AlertType:
            assert at in ALERT_SEVERITY_MAP, f"{at} missing from ALERT_SEVERITY_MAP"

    def test_total_alert_types_is_31(self):
        """22 existing + 6 v0.4.0 + 1 v0.5.0 + 3 v0.6.0 = 31 total (1 was already counted)."""
        assert len(AlertType) == 31


class TestGeoFenceModel:
    """GeoFence Pydantic model validation."""

    def test_circle_fence(self):
        fence = GeoFence(
            name="HQ Zone",
            fence_type=FenceType.CIRCLE,
            center_lat=30.2672,
            center_lon=-97.7431,
            radius_m=500.0,
        )
        assert fence.name == "HQ Zone"
        assert fence.fence_type == FenceType.CIRCLE
        assert fence.enabled is True
        assert fence.trigger_on == TriggerOn.EXIT

    def test_polygon_fence(self):
        fence = GeoFence(
            name="Warehouse",
            fence_type=FenceType.POLYGON,
            polygon_points=[[30.0, -97.0], [30.1, -97.0], [30.1, -97.1]],
            trigger_on=TriggerOn.BOTH,
        )
        assert len(fence.polygon_points) == 3

    def test_applies_to_node_all(self):
        fence = GeoFence(name="All Nodes", node_filter=None)
        assert fence.applies_to_node("!aaa11111") is True
        assert fence.applies_to_node("!anything") is True

    def test_applies_to_node_filtered(self):
        fence = GeoFence(name="Filtered", node_filter=["!aaa11111", "!bbb22222"])
        assert fence.applies_to_node("!aaa11111") is True
        assert fence.applies_to_node("!ccc33333") is False

    def test_latitude_validation(self):
        with pytest.raises(ValidationError):
            GeoFence(name="Bad", center_lat=91.0, center_lon=0.0)

    def test_longitude_validation(self):
        with pytest.raises(ValidationError):
            GeoFence(name="Bad", center_lat=0.0, center_lon=181.0)


class TestGeoFenceEvent:
    def test_event_creation(self):
        event = GeoFenceEvent(
            fence_id=1,
            fence_name="HQ Zone",
            node_id="!aaa11111",
            event_type="exit",
            latitude=30.2672,
            longitude=-97.7431,
            distance_m=50.0,
        )
        assert event.event_type == "exit"
        assert event.distance_m == 50.0


class TestGeoFenceCheck:
    def test_check_with_no_events(self):
        check = GeoFenceCheck(
            node_id="!aaa11111",
            latitude=30.2672,
            longitude=-97.7431,
            fences_checked=5,
        )
        assert len(check.events) == 0
        assert check.fences_checked == 5


class TestCoverageSampleModel:
    def test_sample_creation(self):
        sample = CoverageSample(
            from_node="!aaa11111",
            to_node="!bbb22222",
            latitude=30.2672,
            longitude=-97.7431,
            rssi=-85.0,
            snr=10.5,
        )
        assert sample.rssi == -85.0
        assert sample.snr == 10.5

    def test_sample_without_snr(self):
        sample = CoverageSample(
            from_node="!aaa", to_node="!bbb", latitude=0.0, longitude=0.0, rssi=-100.0
        )
        assert sample.snr is None


class TestCoverageGridCell:
    def test_cell_creation(self):
        cell = CoverageGridCell(
            lat_center=30.267,
            lon_center=-97.743,
            avg_rssi=-88.5,
            min_rssi=-100.0,
            max_rssi=-75.0,
            sample_count=15,
        )
        assert cell.sample_count == 15


class TestCoverageHeatmap:
    def test_empty_heatmap(self):
        heatmap = CoverageHeatmap(
            min_lat=30.0, max_lat=31.0, min_lon=-98.0, max_lon=-97.0, resolution_m=100.0
        )
        assert len(heatmap.cells) == 0
        assert heatmap.total_samples == 0


class TestCoverageStats:
    def test_default_stats(self):
        stats = CoverageStats()
        assert stats.total_samples == 0
        assert stats.dead_zone_count == 0
        assert stats.avg_rssi is None
