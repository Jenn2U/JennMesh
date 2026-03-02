"""Tests for GPS location models — including Haversine distance."""

import math

import pytest

from jenn_mesh.models.location import (
    GPSPosition,
    LostNodeQuery,
    NearbyNode,
    ProximityResult,
)


class TestGPSPosition:
    def test_distance_to_same_point_is_zero(self):
        p = GPSPosition(node_id="!a", latitude=30.0, longitude=-97.0)
        assert p.distance_to(p) == pytest.approx(0.0, abs=0.01)

    def test_distance_austin_to_dallas(self):
        """Austin TX to Dallas TX is roughly 300 km."""
        austin = GPSPosition(node_id="!a", latitude=30.2672, longitude=-97.7431)
        dallas = GPSPosition(node_id="!b", latitude=32.7767, longitude=-96.7970)
        dist = austin.distance_to(dallas)
        assert 280_000 < dist < 310_000  # ~295 km in meters

    def test_distance_short_range(self):
        """Two points 1 km apart on the equator."""
        p1 = GPSPosition(node_id="!a", latitude=0.0, longitude=0.0)
        # 1 km east: ~0.009 degrees at the equator
        p2 = GPSPosition(node_id="!b", latitude=0.0, longitude=0.008993)
        dist = p1.distance_to(p2)
        assert 900 < dist < 1100

    def test_distance_is_symmetric(self):
        p1 = GPSPosition(node_id="!a", latitude=30.0, longitude=-97.0)
        p2 = GPSPosition(node_id="!b", latitude=31.0, longitude=-96.0)
        assert p1.distance_to(p2) == pytest.approx(p2.distance_to(p1), rel=1e-9)


class TestLostNodeQuery:
    def test_defaults(self):
        q = LostNodeQuery(target_node_id="!abc")
        assert q.search_radius_meters == 5000.0
        assert q.max_age_hours == 72.0


class TestProximityResult:
    def test_is_found_with_position(self):
        r = ProximityResult(
            target_node_id="!abc",
            last_known_position=GPSPosition(node_id="!abc", latitude=30.0, longitude=-97.0),
        )
        assert r.is_found is True

    def test_not_found_without_position(self):
        r = ProximityResult(target_node_id="!abc")
        assert r.is_found is False
