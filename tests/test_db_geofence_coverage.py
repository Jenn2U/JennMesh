"""Tests for Schema v12 — geofences and coverage_samples tables + CRUD methods."""

from __future__ import annotations

import json

import pytest

from jenn_mesh.db import SCHEMA_VERSION, MeshDatabase


class TestSchemaV12:
    """Verify schema version bumped and new tables exist."""

    def test_schema_version_is_15(self):
        assert SCHEMA_VERSION == 15

    def test_geofences_table_exists(self, db: MeshDatabase):
        with db.connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='geofences'"
            ).fetchone()
            assert row is not None

    def test_coverage_samples_table_exists(self, db: MeshDatabase):
        with db.connection() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master " "WHERE type='table' AND name='coverage_samples'"
            ).fetchone()
            assert row is not None

    def test_coverage_indexes_exist(self, db: MeshDatabase):
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_coverage%'"
            ).fetchall()
            names = {r["name"] for r in rows}
            assert "idx_coverage_location" in names
            assert "idx_coverage_time" in names


class TestGeofenceCRUD:
    """Geofence create, read, update, delete operations."""

    def test_create_circle_geofence(self, db: MeshDatabase):
        fence_id = db.create_geofence(
            name="HQ Perimeter",
            fence_type="circle",
            center_lat=30.2672,
            center_lon=-97.7431,
            radius_m=500.0,
            trigger_on="exit",
        )
        assert fence_id is not None
        assert fence_id > 0

    def test_create_polygon_geofence(self, db: MeshDatabase):
        polygon = json.dumps([[30.26, -97.74], [30.27, -97.74], [30.27, -97.75], [30.26, -97.75]])
        fence_id = db.create_geofence(
            name="Warehouse Zone",
            fence_type="polygon",
            polygon_json=polygon,
            trigger_on="both",
        )
        assert fence_id > 0

    def test_create_geofence_with_node_filter(self, db: MeshDatabase):
        node_filter = json.dumps(["!aaa11111", "!bbb22222"])
        fence_id = db.create_geofence(
            name="Mobile Only",
            fence_type="circle",
            center_lat=30.0,
            center_lon=-97.0,
            radius_m=1000.0,
            node_filter=node_filter,
        )
        fence = db.get_geofence(fence_id)
        assert fence is not None
        assert json.loads(fence["node_filter"]) == ["!aaa11111", "!bbb22222"]

    def test_get_geofence(self, db: MeshDatabase):
        fence_id = db.create_geofence(
            name="Test Fence",
            fence_type="circle",
            center_lat=30.0,
            center_lon=-97.0,
            radius_m=100.0,
        )
        fence = db.get_geofence(fence_id)
        assert fence is not None
        assert fence["name"] == "Test Fence"
        assert fence["fence_type"] == "circle"
        assert fence["center_lat"] == 30.0
        assert fence["radius_m"] == 100.0
        assert fence["enabled"] == 1
        assert fence["trigger_on"] == "exit"

    def test_get_nonexistent_geofence(self, db: MeshDatabase):
        assert db.get_geofence(999) is None

    def test_list_geofences_all(self, db: MeshDatabase):
        db.create_geofence(name="Fence A", center_lat=30.0, center_lon=-97.0, radius_m=100.0)
        db.create_geofence(
            name="Fence B", center_lat=31.0, center_lon=-97.0, radius_m=200.0, enabled=False
        )
        fences = db.list_geofences()
        assert len(fences) == 2

    def test_list_geofences_enabled_only(self, db: MeshDatabase):
        db.create_geofence(name="Enabled", center_lat=30.0, center_lon=-97.0, radius_m=100.0)
        db.create_geofence(
            name="Disabled", center_lat=31.0, center_lon=-97.0, radius_m=200.0, enabled=False
        )
        fences = db.list_geofences(enabled_only=True)
        assert len(fences) == 1
        assert fences[0]["name"] == "Enabled"

    def test_update_geofence(self, db: MeshDatabase):
        fence_id = db.create_geofence(
            name="Original", center_lat=30.0, center_lon=-97.0, radius_m=100.0
        )
        result = db.update_geofence(fence_id, name="Renamed", radius_m=250.0)
        assert result is True
        fence = db.get_geofence(fence_id)
        assert fence["name"] == "Renamed"
        assert fence["radius_m"] == 250.0
        assert fence["updated_at"] is not None

    def test_update_geofence_disable(self, db: MeshDatabase):
        fence_id = db.create_geofence(
            name="Active", center_lat=30.0, center_lon=-97.0, radius_m=100.0
        )
        db.update_geofence(fence_id, enabled=False)
        fence = db.get_geofence(fence_id)
        assert fence["enabled"] == 0

    def test_update_nonexistent_geofence(self, db: MeshDatabase):
        result = db.update_geofence(999, name="Ghost")
        assert result is False

    def test_delete_geofence(self, db: MeshDatabase):
        fence_id = db.create_geofence(
            name="Delete Me", center_lat=30.0, center_lon=-97.0, radius_m=100.0
        )
        assert db.delete_geofence(fence_id) is True
        assert db.get_geofence(fence_id) is None

    def test_delete_nonexistent_geofence(self, db: MeshDatabase):
        assert db.delete_geofence(999) is False


class TestCoverageSamplesCRUD:
    """Coverage sample recording and querying."""

    def test_add_coverage_sample(self, populated_db: MeshDatabase):
        sample_id = populated_db.add_coverage_sample(
            from_node="!aaa11111",
            to_node="!bbb22222",
            latitude=30.2672,
            longitude=-97.7431,
            rssi=-85.0,
            snr=10.5,
        )
        assert sample_id > 0

    def test_add_coverage_sample_minimal(self, populated_db: MeshDatabase):
        """SNR and timestamp are optional."""
        sample_id = populated_db.add_coverage_sample(
            from_node="!aaa11111",
            to_node="!bbb22222",
            latitude=30.0,
            longitude=-97.0,
            rssi=-100.0,
        )
        assert sample_id > 0

    def test_get_coverage_in_bounds(self, populated_db: MeshDatabase):
        # Add samples in Austin area
        for i in range(5):
            populated_db.add_coverage_sample(
                from_node="!aaa11111",
                to_node="!bbb22222",
                latitude=30.267 + i * 0.001,
                longitude=-97.743 + i * 0.001,
                rssi=-85.0 - i * 2,
            )
        # Add sample in Dallas (outside Austin bounds)
        populated_db.add_coverage_sample(
            from_node="!ccc33333",
            to_node="!aaa11111",
            latitude=32.776,
            longitude=-96.797,
            rssi=-100.0,
        )

        # Query Austin area only
        results = populated_db.get_coverage_in_bounds(30.26, 30.28, -97.75, -97.73)
        assert len(results) == 5
        # Dallas sample should NOT be included
        dallas_lats = [r["latitude"] for r in results if r["latitude"] > 32.0]
        assert len(dallas_lats) == 0

    def test_get_coverage_in_bounds_empty(self, db: MeshDatabase):
        results = db.get_coverage_in_bounds(0.0, 1.0, 0.0, 1.0)
        assert results == []

    def test_get_coverage_stats(self, populated_db: MeshDatabase):
        populated_db.add_coverage_sample("!aaa11111", "!bbb22222", 30.0, -97.0, -80.0)
        populated_db.add_coverage_sample("!aaa11111", "!bbb22222", 30.1, -97.1, -90.0)
        populated_db.add_coverage_sample("!bbb22222", "!ccc33333", 30.2, -97.2, -100.0)

        stats = populated_db.get_coverage_stats()
        assert stats["total_samples"] == 3
        assert stats["avg_rssi"] == pytest.approx(-90.0, abs=0.1)
        assert stats["min_rssi"] == -100.0
        assert stats["max_rssi"] == -80.0
        assert stats["last_sample_at"] is not None

    def test_get_coverage_stats_empty(self, db: MeshDatabase):
        stats = db.get_coverage_stats()
        assert stats["total_samples"] == 0

    def test_get_coverage_for_node(self, populated_db: MeshDatabase):
        populated_db.add_coverage_sample("!aaa11111", "!bbb22222", 30.0, -97.0, -85.0)
        populated_db.add_coverage_sample("!bbb22222", "!aaa11111", 30.1, -97.1, -90.0)
        populated_db.add_coverage_sample("!ccc33333", "!ddd44444", 32.0, -96.0, -100.0)

        # !aaa11111 is in 2 of 3 samples (as sender and receiver)
        results = populated_db.get_coverage_for_node("!aaa11111")
        assert len(results) == 2

        # !ccc33333 is in only 1 sample
        results = populated_db.get_coverage_for_node("!ccc33333")
        assert len(results) == 1

    def test_prune_old_coverage(self, populated_db: MeshDatabase):
        # Add a sample with explicit old timestamp
        populated_db.add_coverage_sample(
            "!aaa11111",
            "!bbb22222",
            30.0,
            -97.0,
            -85.0,
            timestamp="2020-01-01T00:00:00",
        )
        # Add a recent sample (default timestamp = now)
        populated_db.add_coverage_sample(
            "!aaa11111",
            "!bbb22222",
            30.1,
            -97.1,
            -90.0,
        )

        deleted = populated_db.prune_old_coverage(days=30)
        assert deleted == 1  # Only the old one

        # Recent sample should survive
        stats = populated_db.get_coverage_stats()
        assert stats["total_samples"] == 1
