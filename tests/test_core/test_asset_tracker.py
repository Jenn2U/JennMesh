"""Tests for the asset tracker — registration, trails, status updates."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from jenn_mesh.core.asset_tracker import AssetTracker, _bearing_degrees, _haversine_meters
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.asset_tracking import AssetType


@pytest.fixture
def db(tmp_path) -> MeshDatabase:
    return MeshDatabase(db_path=str(tmp_path / "asset_test.db"))


@pytest.fixture
def tracker(db) -> AssetTracker:
    return AssetTracker(db=db)


# ── Haversine / bearing helpers ──────────────────────────────────────


class TestGeoHelpers:
    def test_haversine_same_point(self):
        assert _haversine_meters(30.0, -97.0, 30.0, -97.0) == pytest.approx(0, abs=0.1)

    def test_haversine_known_distance(self):
        # Austin TX → Dallas TX ≈ ~290 km
        dist = _haversine_meters(30.2672, -97.7431, 32.7767, -96.7970)
        assert 280_000 < dist < 300_000

    def test_bearing_north(self):
        bearing = _bearing_degrees(30.0, -97.0, 31.0, -97.0)
        assert 355 < bearing or bearing < 5  # ~0° (north)

    def test_bearing_east(self):
        bearing = _bearing_degrees(30.0, -97.0, 30.0, -96.0)
        assert 85 < bearing < 95  # ~90° (east)


# ── Asset registration ───────────────────────────────────────────────


class TestRegisterAsset:
    def test_register_vehicle(self, tracker):
        asset = tracker.register_asset(
            name="Truck-01",
            asset_type="vehicle",
            node_id="!abc123",
            zone="Zone-A",
            team="Alpha",
        )
        assert asset.id is not None
        assert asset.name == "Truck-01"
        assert asset.asset_type == AssetType.VEHICLE
        assert asset.node_id == "!abc123"

    def test_register_personnel(self, tracker):
        asset = tracker.register_asset(
            name="Field-Op-1",
            asset_type="personnel",
            node_id="!def456",
        )
        assert asset.asset_type == AssetType.PERSONNEL

    def test_invalid_type(self, tracker):
        with pytest.raises(ValueError, match="Invalid asset_type"):
            tracker.register_asset(name="Bad", asset_type="invalid", node_id="!abc")

    def test_empty_node_id(self, tracker):
        with pytest.raises(ValueError, match="node_id is required"):
            tracker.register_asset(name="Bad", asset_type="vehicle", node_id="")

    def test_register_with_metadata(self, tracker):
        asset = tracker.register_asset(
            name="Drone-01",
            asset_type="drone",
            node_id="!drone1",
            metadata={"model": "DJI Mavic", "max_altitude": 120},
        )
        assert asset.metadata_json is not None
        assert "DJI Mavic" in asset.metadata_json


# ── CRUD operations ──────────────────────────────────────────────────


class TestAssetCRUD:
    def test_get_asset(self, tracker):
        asset = tracker.register_asset(name="Test", asset_type="equipment", node_id="!abc")
        fetched = tracker.get_asset(asset.id)
        assert fetched is not None
        assert fetched["name"] == "Test"

    def test_get_nonexistent(self, tracker):
        assert tracker.get_asset(9999) is None

    def test_get_by_node(self, tracker):
        tracker.register_asset(name="ByNode", asset_type="vehicle", node_id="!target")
        fetched = tracker.get_asset_by_node("!target")
        assert fetched is not None
        assert fetched["name"] == "ByNode"

    def test_list_assets(self, tracker):
        tracker.register_asset(name="A", asset_type="vehicle", node_id="!a")
        tracker.register_asset(name="B", asset_type="equipment", node_id="!b")
        all_assets = tracker.list_assets()
        assert len(all_assets) == 2

    def test_list_by_type(self, tracker):
        tracker.register_asset(name="A", asset_type="vehicle", node_id="!a")
        tracker.register_asset(name="B", asset_type="equipment", node_id="!b")
        vehicles = tracker.list_assets(asset_type="vehicle")
        assert len(vehicles) == 1
        assert vehicles[0]["name"] == "A"

    def test_update_asset(self, tracker):
        asset = tracker.register_asset(name="Old", asset_type="vehicle", node_id="!abc")
        assert tracker.update_asset(asset.id, name="New")
        fetched = tracker.get_asset(asset.id)
        assert fetched["name"] == "New"

    def test_delete_asset(self, tracker):
        asset = tracker.register_asset(name="Del", asset_type="vehicle", node_id="!abc")
        assert tracker.delete_asset(asset.id)
        assert tracker.get_asset(asset.id) is None


# ── Trail computation ────────────────────────────────────────────────


class TestTrail:
    def test_empty_trail(self, tracker, db):
        tracker.register_asset(name="T", asset_type="vehicle", node_id="!abc")
        trail = tracker.get_trail("!abc")
        assert len(trail.positions) == 0
        assert trail.total_distance_m == 0.0

    def test_trail_with_positions(self, tracker, db):
        tracker.register_asset(name="T", asset_type="vehicle", node_id="!abc")
        now = datetime.utcnow()
        for i in range(5):
            ts = (now - timedelta(minutes=10 * (4 - i))).isoformat()
            db.add_position(
                "!abc",
                30.0 + i * 0.01,
                -97.0 + i * 0.01,
                source="gps",
                timestamp=ts,
            )
        trail = tracker.get_trail("!abc", hours=24)
        assert len(trail.positions) == 5
        assert trail.total_distance_m > 0

    def test_trail_computes_speed(self, tracker, db):
        tracker.register_asset(name="T", asset_type="vehicle", node_id="!abc")
        now = datetime.utcnow()
        db.add_position(
            "!abc",
            30.0,
            -97.0,
            source="gps",
            timestamp=(now - timedelta(minutes=10)).isoformat(),
        )
        db.add_position(
            "!abc",
            30.01,
            -97.01,
            source="gps",
            timestamp=now.isoformat(),
        )
        trail = tracker.get_trail("!abc")
        assert len(trail.positions) == 2
        # Second position should have speed computed
        assert trail.positions[1].speed_mps is not None
        assert trail.positions[1].speed_mps > 0

    def test_trail_for_unregistered_node(self, tracker, db):
        # Should still work, just uses node_id as name
        trail = tracker.get_trail("!unknown")
        assert trail.asset_name == "!unknown"
        assert trail.asset_id == 0


# ── Status updates ───────────────────────────────────────────────────


class TestUpdateStatuses:
    def test_no_assets_returns_zero(self, tracker):
        assert tracker.update_asset_statuses() == 0

    def test_active_device(self, tracker, db):
        tracker.register_asset(name="T", asset_type="vehicle", node_id="!abc")
        now = datetime.utcnow()
        db.upsert_device("!abc", last_seen=now.isoformat())
        tracker.update_asset_statuses()
        asset = tracker.get_asset_by_node("!abc")
        assert asset["status"] == "active"

    def test_idle_device(self, tracker, db):
        tracker.register_asset(name="T", asset_type="vehicle", node_id="!abc")
        old = datetime.utcnow() - timedelta(minutes=15)
        db.upsert_device("!abc", last_seen=old.isoformat())
        tracker.update_asset_statuses()
        asset = tracker.get_asset_by_node("!abc")
        assert asset["status"] == "idle"

    def test_out_of_range_device(self, tracker, db):
        tracker.register_asset(name="T", asset_type="vehicle", node_id="!abc")
        very_old = datetime.utcnow() - timedelta(hours=1)
        db.upsert_device("!abc", last_seen=very_old.isoformat())
        tracker.update_asset_statuses()
        asset = tracker.get_asset_by_node("!abc")
        assert asset["status"] == "out_of_range"

    def test_no_device_marks_out_of_range(self, tracker, db):
        tracker.register_asset(name="T", asset_type="vehicle", node_id="!ghost")
        tracker.update_asset_statuses()
        asset = tracker.get_asset_by_node("!ghost")
        assert asset["status"] == "out_of_range"
