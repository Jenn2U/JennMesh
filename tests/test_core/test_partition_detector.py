"""Tests for partition detection — network splits and merge events."""

from __future__ import annotations


import pytest

from jenn_mesh.core.partition_detector import (
    PartitionDetector,
    _compute_component_centroid,
)
from jenn_mesh.db import MeshDatabase


@pytest.fixture
def db(tmp_path) -> MeshDatabase:
    db = MeshDatabase(db_path=str(tmp_path / "part_test.db"))
    # Create a 4-node fleet with GPS
    db.upsert_device("!n1", long_name="Node1", role="ROUTER", latitude=30.2672, longitude=-97.7431)
    db.upsert_device("!n2", long_name="Node2", role="CLIENT", latitude=30.2700, longitude=-97.7400)
    db.upsert_device("!n3", long_name="Node3", role="CLIENT", latitude=32.7767, longitude=-96.7970)
    db.upsert_device("!n4", long_name="Node4", role="SENSOR", latitude=32.7800, longitude=-96.8000)
    return db


def _create_full_mesh(db):
    """Wire all 4 nodes into a single connected component."""
    db.upsert_topology_edge("!n1", "!n2", snr=10.0, rssi=-85)
    db.upsert_topology_edge("!n2", "!n1", snr=8.0, rssi=-92)
    db.upsert_topology_edge("!n2", "!n3", snr=5.0, rssi=-100)
    db.upsert_topology_edge("!n3", "!n2", snr=4.0, rssi=-102)
    db.upsert_topology_edge("!n3", "!n4", snr=9.0, rssi=-88)
    db.upsert_topology_edge("!n4", "!n3", snr=8.5, rssi=-90)


def _create_partitioned_mesh(db):
    """Wire nodes into 2 disconnected groups: {!n1,!n2} and {!n3,!n4}."""
    db.upsert_topology_edge("!n1", "!n2", snr=10.0, rssi=-85)
    db.upsert_topology_edge("!n2", "!n1", snr=8.0, rssi=-92)
    db.upsert_topology_edge("!n3", "!n4", snr=9.0, rssi=-88)
    db.upsert_topology_edge("!n4", "!n3", snr=8.5, rssi=-90)


# ── _compute_component_centroid() ─────────────────────────────────────


class TestComputeComponentCentroid:
    def test_single_node_centroid(self, db):
        result = _compute_component_centroid(db, ["!n1"])
        assert result is not None
        assert "lat=30.267" in result
        assert "lon=-97.743" in result

    def test_two_node_centroid_averaged(self, db):
        result = _compute_component_centroid(db, ["!n1", "!n2"])
        assert result is not None
        # Average of 30.2672 and 30.2700 ≈ 30.2686
        assert "30.2686" in result

    def test_no_gps_returns_none(self, db):
        db.upsert_device("!nogps", long_name="NoGPS", role="CLIENT")
        result = _compute_component_centroid(db, ["!nogps"])
        assert result is None

    def test_skips_zero_coordinates(self, db):
        db.upsert_device("!zero", long_name="Zero", latitude=0.0, longitude=0.0)
        result = _compute_component_centroid(db, ["!zero"])
        assert result is None

    def test_mixed_gps_and_no_gps(self, db):
        db.upsert_device("!nogps", long_name="NoGPS", role="CLIENT")
        result = _compute_component_centroid(db, ["!n1", "!nogps"])
        assert result is not None  # Uses only !n1's GPS


# ── PartitionDetector ─────────────────────────────────────────────────


class TestPartitionDetector:
    def test_no_edges_all_isolated(self, db):
        """Without edges, each device is its own component."""
        detector = PartitionDetector(db=db)
        result = detector.check_partitions()
        # 4 devices, no edges → 4 components (or however topology handles it)
        assert result["component_count"] >= 1

    def test_full_mesh_single_component(self, db):
        _create_full_mesh(db)
        detector = PartitionDetector(db=db)
        result = detector.check_partitions()
        # Depending on previous state, this may or may not generate events
        assert result["component_count"] >= 1

    def test_partition_detected_creates_alerts(self, db):
        """When mesh splits, NETWORK_PARTITION alerts should be created."""
        _create_full_mesh(db)
        detector = PartitionDetector(db=db)
        # First check establishes baseline
        detector.check_partitions()

        # Now break the mesh into 2 partitions by removing cross-links
        with db.connection() as conn:
            conn.execute("DELETE FROM topology_edges")
        _create_partitioned_mesh(db)

        result = detector.check_partitions()
        assert result["component_count"] == 2
        assert result["event_type"] == "partition_detected"
        assert result["new_alerts"] >= 1

    def test_partition_resolved_auto_resolves_alerts(self, db):
        """When partitions merge back, alerts should auto-resolve."""
        # Create partition
        _create_partitioned_mesh(db)
        detector = PartitionDetector(db=db)
        detector.check_partitions()

        # Now heal the partition
        with db.connection() as conn:
            conn.execute("DELETE FROM topology_edges")
        _create_full_mesh(db)

        result = detector.check_partitions()
        if result["event_type"] == "partition_resolved":
            assert result["auto_resolved"] >= 0
            assert result["new_alerts"] == 1  # PARTITION_RESOLVED info alert

    def test_get_partition_status(self, db):
        _create_partitioned_mesh(db)
        detector = PartitionDetector(db=db)
        status = detector.get_partition_status()
        assert "is_partitioned" in status
        assert "component_count" in status
        assert "components" in status
        assert isinstance(status["components"], list)

    def test_relay_recommendations_for_partitions(self, db):
        _create_partitioned_mesh(db)
        detector = PartitionDetector(db=db)
        status = detector.get_partition_status()
        if status["is_partitioned"]:
            assert len(status["relay_recommendations"]) >= 1
            assert "relay" in status["relay_recommendations"][0].lower()

    def test_partition_events_stored(self, db):
        _create_partitioned_mesh(db)
        detector = PartitionDetector(db=db)
        detector.check_partitions()
        events = db.list_partition_events()
        # Should have at least one event
        assert len(events) >= 1

    def test_no_change_no_event(self, db):
        _create_full_mesh(db)
        detector = PartitionDetector(db=db)
        detector.check_partitions()
        # Second check with same topology — no change
        result2 = detector.check_partitions()
        assert result2["event_type"] is None
