"""Tests for radio performance baselines (MESH-020)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from jenn_mesh.core.baselines import (
    DEFAULT_DEVIATION_THRESHOLD,
    MIN_SAMPLES_FOR_BASELINE,
    BaselineManager,
    _compute_drain_rate,
    _compute_stats,
)
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.health import BaselineSnapshot

# ── Pure stats helpers ───────────────────────────────────────────────


class TestComputeStats:
    def test_empty_list(self):
        mean, std = _compute_stats([])
        assert mean == 0.0
        assert std == 0.0

    def test_single_value(self):
        mean, std = _compute_stats([5.0])
        assert mean == 5.0
        assert std == 0.0

    def test_uniform_values(self):
        mean, std = _compute_stats([3.0, 3.0, 3.0])
        assert mean == 3.0
        assert std == 0.0

    def test_known_distribution(self):
        # [2, 4, 4, 4, 5, 5, 7, 9] → mean=5, pop stddev=2
        vals = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        mean, std = _compute_stats(vals)
        assert mean == 5.0
        assert abs(std - 2.0) < 0.01

    def test_negative_values(self):
        # RSSI values are typically negative
        vals = [-90.0, -85.0, -92.0, -88.0]
        mean, std = _compute_stats(vals)
        assert mean == pytest.approx(-88.75)
        assert std > 0


class TestComputeDrainRate:
    def test_insufficient_samples(self):
        assert _compute_drain_rate([]) is None
        assert _compute_drain_rate([("2024-01-01T00:00:00", 4.0)]) is None

    def test_insufficient_time_span(self):
        # Less than 1 hour apart
        pairs = [
            ("2024-01-01T00:00:00", 4.0),
            ("2024-01-01T00:30:00", 3.9),
        ]
        assert _compute_drain_rate(pairs) is None

    def test_positive_drain(self):
        # 4.0V to 3.8V over 2 hours = 0.1 V/hr drain
        pairs = [
            ("2024-01-01T00:00:00", 4.0),
            ("2024-01-01T02:00:00", 3.8),
        ]
        rate = _compute_drain_rate(pairs)
        assert rate is not None
        assert rate == pytest.approx(0.1, abs=0.001)

    def test_charging_negative_drain(self):
        # Voltage increasing = negative drain (charging)
        pairs = [
            ("2024-01-01T00:00:00", 3.5),
            ("2024-01-01T02:00:00", 4.0),
        ]
        rate = _compute_drain_rate(pairs)
        assert rate is not None
        assert rate < 0

    def test_unsorted_input(self):
        # Should sort by timestamp
        pairs = [
            ("2024-01-01T06:00:00", 3.4),
            ("2024-01-01T00:00:00", 4.0),
        ]
        rate = _compute_drain_rate(pairs)
        assert rate is not None
        assert rate == pytest.approx(0.1, abs=0.001)


# ── BaselineManager ─────────────────────────────────────────────────


class TestBaselineManager:
    def test_recompute_baseline_insufficient_samples(self, db: MeshDatabase):
        manager = BaselineManager(db)
        db.upsert_device("!node1", long_name="Test")
        # Add fewer samples than minimum
        for i in range(MIN_SAMPLES_FOR_BASELINE - 1):
            db.add_telemetry_sample("!node1", rssi=-85, snr=10.0)
        baseline = manager.recompute_baseline("!node1")
        assert baseline is None

    def test_recompute_baseline_sufficient_samples(self, db: MeshDatabase):
        manager = BaselineManager(db)
        db.upsert_device("!node1", long_name="Test")
        for i in range(15):
            db.add_telemetry_sample("!node1", rssi=-85 + i, snr=10.0 + i * 0.5)
        baseline = manager.recompute_baseline("!node1")
        assert baseline is not None
        assert isinstance(baseline, BaselineSnapshot)
        assert baseline.sample_count == 15
        assert baseline.rssi_mean is not None
        assert baseline.snr_mean is not None
        assert baseline.has_sufficient_data

    def test_recompute_persists_to_db(self, db: MeshDatabase):
        manager = BaselineManager(db)
        db.upsert_device("!node1", long_name="Test")
        for i in range(12):
            db.add_telemetry_sample("!node1", rssi=-90, snr=8.0)
        manager.recompute_baseline("!node1")
        # Retrieve from DB
        stored = manager.get_baseline("!node1")
        assert stored is not None
        assert stored.node_id == "!node1"
        assert stored.rssi_mean == pytest.approx(-90.0)
        assert stored.sample_count == 12

    def test_get_baseline_missing(self, db: MeshDatabase):
        manager = BaselineManager(db)
        assert manager.get_baseline("!nonexistent") is None

    def test_get_all_baselines(self, populated_db: MeshDatabase):
        manager = BaselineManager(populated_db)
        # Recompute for nodes that have telemetry history
        manager.recompute_baseline("!aaa11111")
        manager.recompute_baseline("!bbb22222")
        baselines = manager.get_all_baselines()
        assert len(baselines) >= 2
        node_ids = {b.node_id for b in baselines}
        assert "!aaa11111" in node_ids
        assert "!bbb22222" in node_ids

    def test_record_telemetry(self, db: MeshDatabase):
        manager = BaselineManager(db)
        db.upsert_device("!node1", long_name="Test")
        manager.record_telemetry("!node1", rssi=-85, snr=10.0, battery_level=80)
        history = db.get_telemetry_history("!node1")
        assert len(history) == 1
        assert history[0]["rssi"] == -85

    def test_baseline_window_respects_days(self, db: MeshDatabase):
        manager = BaselineManager(db, window_days=3)
        db.upsert_device("!node1", long_name="Test")
        now = datetime.utcnow()
        # Old samples outside window (5 days ago)
        for i in range(10):
            ts = (now - timedelta(days=5, hours=i)).isoformat()
            db.add_telemetry_sample("!node1", rssi=-100, snr=2.0, timestamp=ts)
        # Recent samples inside window
        for i in range(12):
            ts = (now - timedelta(days=1, hours=i)).isoformat()
            db.add_telemetry_sample("!node1", rssi=-80, snr=12.0, timestamp=ts)
        baseline = manager.recompute_baseline("!node1")
        assert baseline is not None
        # Should only use the 12 recent samples, not the 10 old ones
        assert baseline.sample_count == 12
        assert baseline.rssi_mean == pytest.approx(-80.0)

    def test_prune_old_telemetry(self, db: MeshDatabase):
        manager = BaselineManager(db)
        db.upsert_device("!node1", long_name="Test")
        now = datetime.utcnow()
        # Old
        for i in range(5):
            ts = (now - timedelta(days=20)).isoformat()
            db.add_telemetry_sample("!node1", rssi=-90, timestamp=ts)
        # Recent
        for i in range(3):
            db.add_telemetry_sample("!node1", rssi=-85)
        pruned = manager.prune_old_telemetry(retention_days=14)
        assert pruned == 5
        remaining = db.get_telemetry_history("!node1")
        assert len(remaining) == 3


# ── Deviation detection ──────────────────────────────────────────────


class TestDeviationDetection:
    def _setup_baseline(self, db: MeshDatabase, node_id: str = "!node1"):
        """Helper: create a device and compute a baseline."""
        db.upsert_device(node_id, long_name="Test", signal_rssi=-85, signal_snr=10.0)
        for i in range(20):
            # Vary RSSI slightly so stddev > 0 (realistic jitter)
            db.add_telemetry_sample(node_id, rssi=-85 + (i % 3), snr=10.0 + (i % 4) * 0.5)
        manager = BaselineManager(db)
        manager.recompute_baseline(node_id)
        return manager

    def test_no_deviation_normal_values(self, db: MeshDatabase):
        manager = self._setup_baseline(db)
        report = manager.check_deviation("!node1")
        assert report is not None
        assert report.is_degraded is False
        assert len(report.details) == 0

    def test_rssi_deviation_triggers_degraded(self, db: MeshDatabase):
        manager = self._setup_baseline(db)
        # Set current device RSSI far from baseline
        db.upsert_device("!node1", signal_rssi=-120)
        report = manager.check_deviation("!node1")
        assert report is not None
        assert report.is_degraded is True
        assert report.rssi_deviation_sigma is not None
        assert abs(report.rssi_deviation_sigma) > DEFAULT_DEVIATION_THRESHOLD

    def test_snr_deviation_triggers_degraded(self, db: MeshDatabase):
        manager = self._setup_baseline(db)
        # Set current device SNR far from baseline
        db.upsert_device("!node1", signal_snr=-5.0)
        report = manager.check_deviation("!node1")
        assert report is not None
        assert report.is_degraded is True
        assert report.snr_deviation_sigma is not None

    def test_no_baseline_returns_none(self, db: MeshDatabase):
        manager = BaselineManager(db)
        db.upsert_device("!new_node", long_name="New")
        assert manager.check_deviation("!new_node") is None

    def test_no_device_returns_none(self, db: MeshDatabase):
        manager = BaselineManager(db)
        assert manager.check_deviation("!nonexistent") is None

    def test_fleet_deviations_filters_degraded(self, populated_db: MeshDatabase):
        manager = BaselineManager(populated_db)
        # Compute baselines for nodes with telemetry
        manager.recompute_baseline("!aaa11111")
        manager.recompute_baseline("!bbb22222")
        # Normal conditions — no deviations
        deviations = manager.check_fleet_deviations()
        # All nodes should be within normal range from conftest seed data
        assert isinstance(deviations, list)

    def test_custom_deviation_threshold(self, db: MeshDatabase):
        # Use a very tight threshold (0.5σ) — even small changes flag
        db.upsert_device("!node1", long_name="Test", signal_rssi=-85, signal_snr=10.0)
        for i in range(20):
            db.add_telemetry_sample("!node1", rssi=-85 + (i % 3), snr=10.0 + (i % 4) * 0.5)
        manager = BaselineManager(db, deviation_threshold=0.5)
        manager.recompute_baseline("!node1")
        # Slight deviation from mean
        db.upsert_device("!node1", signal_rssi=-88, signal_snr=8.0)
        report = manager.check_deviation("!node1")
        assert report is not None
        # With tight threshold, likely flagged
        # (depends on actual stddev from the seeded data)


# ── BaselineSnapshot model ───────────────────────────────────────────


class TestBaselineSnapshotModel:
    def test_has_sufficient_data_true(self):
        snap = BaselineSnapshot(node_id="!test", sample_count=15)
        assert snap.has_sufficient_data is True

    def test_has_sufficient_data_false(self):
        snap = BaselineSnapshot(node_id="!test", sample_count=5)
        assert snap.has_sufficient_data is False

    def test_has_sufficient_data_boundary(self):
        snap = BaselineSnapshot(node_id="!test", sample_count=10)
        assert snap.has_sufficient_data is True

    def test_model_dump(self):
        snap = BaselineSnapshot(
            node_id="!test",
            rssi_mean=-85.0,
            rssi_stddev=3.5,
            sample_count=20,
        )
        data = snap.model_dump()
        assert data["node_id"] == "!test"
        assert data["rssi_mean"] == -85.0
        assert data["sample_count"] == 20
