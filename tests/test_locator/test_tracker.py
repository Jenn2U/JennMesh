"""Tests for GPS position tracker."""

from jenn_mesh.db import MeshDatabase
from jenn_mesh.locator.tracker import PositionTracker


class TestPositionTracker:
    def test_get_latest_position(self, populated_db: MeshDatabase):
        tracker = PositionTracker(populated_db)
        pos = tracker.get_latest_position("!aaa11111")
        assert pos is not None
        assert pos.node_id == "!aaa11111"
        assert pos.latitude == 30.2672
        assert pos.longitude == -97.7431

    def test_no_position_returns_none(self, populated_db: MeshDatabase):
        tracker = PositionTracker(populated_db)
        assert tracker.get_latest_position("!ddd44444") is None

    def test_position_age_hours(self, populated_db: MeshDatabase):
        tracker = PositionTracker(populated_db)
        age = tracker.get_position_age_hours("!aaa11111")
        assert age is not None
        assert age < 1.0  # Just added, should be very recent

    def test_position_age_none_for_unknown(self, populated_db: MeshDatabase):
        tracker = PositionTracker(populated_db)
        assert tracker.get_position_age_hours("!ddd44444") is None


class TestNearbyPositions:
    def test_nearby_in_austin(self, populated_db: MeshDatabase):
        tracker = PositionTracker(populated_db)
        # Search near Austin — should find relay and gateway
        nearby = tracker.get_nearby_positions(30.268, -97.742, radius_meters=5000)
        node_ids = {n["node_id"] for n in nearby}
        assert "!aaa11111" in node_ids
        assert "!bbb22222" in node_ids
        assert "!ccc33333" not in node_ids  # Dallas is ~295 km away

    def test_nearby_sorted_by_distance(self, populated_db: MeshDatabase):
        tracker = PositionTracker(populated_db)
        nearby = tracker.get_nearby_positions(30.268, -97.742, radius_meters=10000)
        if len(nearby) >= 2:
            distances = [n["distance_meters"] for n in nearby]
            assert distances == sorted(distances)

    def test_nearby_tight_radius_excludes(self, populated_db: MeshDatabase):
        tracker = PositionTracker(populated_db)
        # Very tight radius should only match exact location
        nearby = tracker.get_nearby_positions(30.2672, -97.7431, radius_meters=10)
        node_ids = {n["node_id"] for n in nearby}
        assert "!aaa11111" in node_ids
        assert "!bbb22222" not in node_ids  # ~400m away


class TestFleetMap:
    def test_all_latest_positions(self, populated_db: MeshDatabase):
        tracker = PositionTracker(populated_db)
        positions = tracker.get_all_latest_positions()
        # 3 devices have GPS coordinates (ddd44444 has no GPS)
        assert len(positions) == 3
        node_ids = {p.node_id for p in positions}
        assert "!aaa11111" in node_ids
        assert "!ddd44444" not in node_ids
