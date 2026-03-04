"""Tests for the GeofencingManager (MESH-019)."""

from __future__ import annotations

import tempfile

import pytest

from jenn_mesh.core.geofencing import GeofencingManager
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.geofence import FenceType, GeoFence, TriggerOn

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def db() -> MeshDatabase:
    """Fresh test database."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return MeshDatabase(db_path=tmp.name)


@pytest.fixture
def manager(db: MeshDatabase) -> GeofencingManager:
    return GeofencingManager(db)


def _create_circle_fence(
    manager: GeofencingManager,
    name: str = "HQ Zone",
    lat: float = 30.2672,
    lon: float = -97.7431,
    radius: float = 500.0,
    trigger_on: TriggerOn = TriggerOn.EXIT,
    node_filter: list | None = None,
) -> int:
    """Helper to create a circle fence."""
    fence = GeoFence(
        name=name,
        fence_type=FenceType.CIRCLE,
        center_lat=lat,
        center_lon=lon,
        radius_m=radius,
        trigger_on=trigger_on,
        node_filter=node_filter,
    )
    return manager.create_fence(fence)


def _create_polygon_fence(
    manager: GeofencingManager,
    name: str = "Warehouse",
    points: list | None = None,
    trigger_on: TriggerOn = TriggerOn.ENTRY,
) -> int:
    """Helper to create a polygon fence."""
    if points is None:
        # Triangle around Austin TX
        points = [[30.25, -97.76], [30.28, -97.76], [30.28, -97.72]]
    fence = GeoFence(
        name=name,
        fence_type=FenceType.POLYGON,
        polygon_points=points,
        trigger_on=trigger_on,
    )
    return manager.create_fence(fence)


# ── CRUD operations ─────────────────────────────────────────────────


class TestGeofenceCRUD:
    def test_create_circle_fence(self, manager: GeofencingManager) -> None:
        fid = _create_circle_fence(manager)
        assert fid > 0

    def test_create_polygon_fence(self, manager: GeofencingManager) -> None:
        fid = _create_polygon_fence(manager)
        assert fid > 0

    def test_get_fence(self, manager: GeofencingManager) -> None:
        fid = _create_circle_fence(manager, name="Test Zone")
        fence = manager.get_fence(fid)
        assert fence is not None
        assert fence.name == "Test Zone"
        assert fence.fence_type == FenceType.CIRCLE
        assert fence.center_lat == 30.2672
        assert fence.radius_m == 500.0

    def test_get_nonexistent_fence(self, manager: GeofencingManager) -> None:
        assert manager.get_fence(9999) is None

    def test_list_fences(self, manager: GeofencingManager) -> None:
        _create_circle_fence(manager, name="Zone A")
        _create_circle_fence(manager, name="Zone B")
        fences = manager.list_fences()
        assert len(fences) == 2

    def test_list_enabled_only(self, manager: GeofencingManager, db: MeshDatabase) -> None:
        _create_circle_fence(manager, name="Active")
        fid2 = _create_circle_fence(manager, name="Disabled")
        db.update_geofence(fid2, enabled=0)

        enabled = manager.list_fences(enabled_only=True)
        assert len(enabled) == 1
        assert enabled[0].name == "Active"

    def test_update_fence(self, manager: GeofencingManager) -> None:
        fid = _create_circle_fence(manager)
        result = manager.update_fence(fid, {"name": "Updated Zone"})
        assert result is True

        fence = manager.get_fence(fid)
        assert fence.name == "Updated Zone"

    def test_update_nonexistent(self, manager: GeofencingManager) -> None:
        result = manager.update_fence(9999, {"name": "Nope"})
        assert result is False

    def test_delete_fence(self, manager: GeofencingManager) -> None:
        fid = _create_circle_fence(manager)
        assert manager.delete_fence(fid) is True
        assert manager.get_fence(fid) is None

    def test_delete_nonexistent(self, manager: GeofencingManager) -> None:
        assert manager.delete_fence(9999) is False

    def test_create_fence_with_node_filter(self, manager: GeofencingManager) -> None:
        fid = _create_circle_fence(manager, node_filter=["!aaa11111", "!bbb22222"])
        fence = manager.get_fence(fid)
        assert fence.node_filter == ["!aaa11111", "!bbb22222"]


# ── Geometry helpers ─────────────────────────────────────────────────


class TestHaversine:
    def test_same_point(self) -> None:
        dist = GeofencingManager._haversine(30.2672, -97.7431, 30.2672, -97.7431)
        assert dist == pytest.approx(0.0, abs=0.1)

    def test_known_distance(self) -> None:
        # Austin TX to Dallas TX ≈ 296 km
        dist = GeofencingManager._haversine(30.2672, -97.7431, 32.7767, -96.7970)
        assert 290_000 < dist < 310_000

    def test_short_distance(self) -> None:
        # ~100m apart
        dist = GeofencingManager._haversine(30.2672, -97.7431, 30.2681, -97.7431)
        assert 90 < dist < 110


class TestPointInPolygon:
    def test_inside_triangle(self) -> None:
        polygon = [[30.25, -97.76], [30.28, -97.76], [30.28, -97.72]]
        assert GeofencingManager._point_in_polygon(30.27, -97.75, polygon) is True

    def test_outside_triangle(self) -> None:
        polygon = [[30.25, -97.76], [30.28, -97.76], [30.28, -97.72]]
        assert GeofencingManager._point_in_polygon(30.20, -97.80, polygon) is False

    def test_inside_square(self) -> None:
        polygon = [[30.0, -98.0], [30.0, -97.0], [31.0, -97.0], [31.0, -98.0]]
        assert GeofencingManager._point_in_polygon(30.5, -97.5, polygon) is True

    def test_outside_square(self) -> None:
        polygon = [[30.0, -98.0], [30.0, -97.0], [31.0, -97.0], [31.0, -98.0]]
        assert GeofencingManager._point_in_polygon(32.0, -97.5, polygon) is False


class TestIsInside:
    def test_circle_inside(self, manager: GeofencingManager) -> None:
        fence = GeoFence(
            name="Test",
            fence_type=FenceType.CIRCLE,
            center_lat=30.2672,
            center_lon=-97.7431,
            radius_m=1000.0,
        )
        assert GeofencingManager._is_inside(fence, 30.2672, -97.7431) is True

    def test_circle_outside(self, manager: GeofencingManager) -> None:
        fence = GeoFence(
            name="Test",
            fence_type=FenceType.CIRCLE,
            center_lat=30.2672,
            center_lon=-97.7431,
            radius_m=100.0,
        )
        # Dallas — far outside
        assert GeofencingManager._is_inside(fence, 32.7767, -96.7970) is False

    def test_polygon_inside(self) -> None:
        fence = GeoFence(
            name="Test",
            fence_type=FenceType.POLYGON,
            polygon_points=[[30.0, -98.0], [30.0, -97.0], [31.0, -97.0], [31.0, -98.0]],
        )
        assert GeofencingManager._is_inside(fence, 30.5, -97.5) is True

    def test_polygon_outside(self) -> None:
        fence = GeoFence(
            name="Test",
            fence_type=FenceType.POLYGON,
            polygon_points=[[30.0, -98.0], [30.0, -97.0], [31.0, -97.0], [31.0, -98.0]],
        )
        assert GeofencingManager._is_inside(fence, 32.0, -97.5) is False

    def test_circle_missing_params(self) -> None:
        fence = GeoFence(name="Test", fence_type=FenceType.CIRCLE)
        assert GeofencingManager._is_inside(fence, 30.0, -97.0) is False

    def test_polygon_too_few_points(self) -> None:
        fence = GeoFence(
            name="Test",
            fence_type=FenceType.POLYGON,
            polygon_points=[[30.0, -98.0], [31.0, -97.0]],
        )
        assert GeofencingManager._is_inside(fence, 30.5, -97.5) is False


# ── Trigger evaluation ───────────────────────────────────────────────


class TestEvaluateTrigger:
    def test_exit_trigger_when_outside(self) -> None:
        fence = GeoFence(name="Test", trigger_on=TriggerOn.EXIT)
        assert GeofencingManager._evaluate_trigger(fence, inside=False) == "exit"

    def test_exit_trigger_when_inside(self) -> None:
        fence = GeoFence(name="Test", trigger_on=TriggerOn.EXIT)
        assert GeofencingManager._evaluate_trigger(fence, inside=True) is None

    def test_entry_trigger_when_inside(self) -> None:
        fence = GeoFence(name="Test", trigger_on=TriggerOn.ENTRY)
        assert GeofencingManager._evaluate_trigger(fence, inside=True) == "entry"

    def test_entry_trigger_when_outside(self) -> None:
        fence = GeoFence(name="Test", trigger_on=TriggerOn.ENTRY)
        assert GeofencingManager._evaluate_trigger(fence, inside=False) is None

    def test_both_trigger_entry(self) -> None:
        fence = GeoFence(name="Test", trigger_on=TriggerOn.BOTH)
        assert GeofencingManager._evaluate_trigger(fence, inside=True) == "entry"

    def test_both_trigger_exit(self) -> None:
        fence = GeoFence(name="Test", trigger_on=TriggerOn.BOTH)
        assert GeofencingManager._evaluate_trigger(fence, inside=False) == "exit"


# ── check_position integration ───────────────────────────────────────


class TestCheckPosition:
    def test_exit_triggers_event(self, manager: GeofencingManager) -> None:
        """Node outside a circle with trigger_on=EXIT generates event."""
        _create_circle_fence(
            manager, lat=30.2672, lon=-97.7431, radius=100.0, trigger_on=TriggerOn.EXIT
        )
        # Position far outside the fence
        events = manager.check_position("!aaa11111", 32.7767, -96.7970)
        assert len(events) == 1
        assert events[0].event_type == "exit"
        assert events[0].node_id == "!aaa11111"

    def test_entry_triggers_event(self, manager: GeofencingManager) -> None:
        """Node inside a circle with trigger_on=ENTRY generates event."""
        _create_circle_fence(
            manager,
            lat=30.2672,
            lon=-97.7431,
            radius=10_000.0,
            trigger_on=TriggerOn.ENTRY,
        )
        # Position at fence center
        events = manager.check_position("!aaa11111", 30.2672, -97.7431)
        assert len(events) == 1
        assert events[0].event_type == "entry"

    def test_no_event_when_inside_exit_fence(self, manager: GeofencingManager) -> None:
        """Node inside a fence with trigger_on=EXIT — no event."""
        _create_circle_fence(
            manager,
            lat=30.2672,
            lon=-97.7431,
            radius=10_000.0,
            trigger_on=TriggerOn.EXIT,
        )
        events = manager.check_position("!aaa11111", 30.2672, -97.7431)
        assert len(events) == 0

    def test_node_filter_excludes(self, manager: GeofencingManager) -> None:
        """Node not in filter list should not trigger."""
        _create_circle_fence(
            manager,
            trigger_on=TriggerOn.EXIT,
            radius=100.0,
            node_filter=["!bbb22222"],
        )
        events = manager.check_position("!aaa11111", 32.7767, -96.7970)
        assert len(events) == 0

    def test_node_filter_includes(self, manager: GeofencingManager) -> None:
        """Node in filter list should trigger."""
        _create_circle_fence(
            manager,
            trigger_on=TriggerOn.EXIT,
            radius=100.0,
            node_filter=["!aaa11111"],
        )
        events = manager.check_position("!aaa11111", 32.7767, -96.7970)
        assert len(events) == 1

    def test_cooldown_suppresses_duplicate(self, manager: GeofencingManager) -> None:
        """Second check within cooldown window should not fire again."""
        _create_circle_fence(manager, trigger_on=TriggerOn.EXIT, radius=100.0)

        events1 = manager.check_position("!aaa11111", 32.7767, -96.7970)
        assert len(events1) == 1

        events2 = manager.check_position("!aaa11111", 32.7767, -96.7970)
        assert len(events2) == 0

    def test_different_nodes_not_suppressed(self, manager: GeofencingManager) -> None:
        """Cooldown is per-node — different node should still trigger."""
        _create_circle_fence(manager, trigger_on=TriggerOn.EXIT, radius=100.0)

        events1 = manager.check_position("!aaa11111", 32.7767, -96.7970)
        assert len(events1) == 1

        events2 = manager.check_position("!bbb22222", 32.7767, -96.7970)
        assert len(events2) == 1

    def test_alert_created_in_db(self, manager: GeofencingManager, db: MeshDatabase) -> None:
        """check_position should persist a GEOFENCE_BREACH alert."""
        _create_circle_fence(manager, trigger_on=TriggerOn.EXIT, radius=100.0)
        manager.check_position("!aaa11111", 32.7767, -96.7970)

        alerts = db.get_active_alerts("!aaa11111")
        breach_alerts = [a for a in alerts if a["alert_type"] == "geofence_breach"]
        assert len(breach_alerts) == 1
        assert "exit" in breach_alerts[0]["message"]

    def test_entry_creates_dwell_alert(self, manager: GeofencingManager, db: MeshDatabase) -> None:
        """Entry event should create GEOFENCE_DWELL alert."""
        _create_circle_fence(
            manager,
            trigger_on=TriggerOn.ENTRY,
            radius=10_000.0,
        )
        manager.check_position("!aaa11111", 30.2672, -97.7431)

        alerts = db.get_active_alerts("!aaa11111")
        dwell_alerts = [a for a in alerts if a["alert_type"] == "geofence_dwell"]
        assert len(dwell_alerts) == 1

    def test_multiple_fences(self, manager: GeofencingManager) -> None:
        """Position check against multiple active fences."""
        _create_circle_fence(
            manager, name="Zone A", lat=30.0, lon=-97.0, radius=100.0, trigger_on=TriggerOn.EXIT
        )
        _create_circle_fence(
            manager, name="Zone B", lat=31.0, lon=-96.0, radius=100.0, trigger_on=TriggerOn.EXIT
        )
        # Outside both
        events = manager.check_position("!test", 40.0, -80.0)
        assert len(events) == 2

    def test_disabled_fence_ignored(self, manager: GeofencingManager, db: MeshDatabase) -> None:
        """Disabled fences should be skipped."""
        fid = _create_circle_fence(manager, trigger_on=TriggerOn.EXIT, radius=100.0)
        db.update_geofence(fid, enabled=0)

        events = manager.check_position("!aaa11111", 32.7767, -96.7970)
        assert len(events) == 0


# ── Distance to boundary ────────────────────────────────────────────


class TestDistanceToBoundary:
    def test_circle_distance_from_center(self) -> None:
        fence = GeoFence(
            name="Test",
            fence_type=FenceType.CIRCLE,
            center_lat=30.2672,
            center_lon=-97.7431,
            radius_m=1000.0,
        )
        # At center → distance to boundary = radius
        dist = GeofencingManager._distance_to_boundary(fence, 30.2672, -97.7431)
        assert dist == pytest.approx(1000.0, abs=1.0)

    def test_circle_distance_missing_params(self) -> None:
        fence = GeoFence(name="Test", fence_type=FenceType.CIRCLE)
        assert GeofencingManager._distance_to_boundary(fence, 30.0, -97.0) == 0.0

    def test_polygon_distance(self) -> None:
        fence = GeoFence(
            name="Test",
            fence_type=FenceType.POLYGON,
            polygon_points=[[30.0, -98.0], [30.0, -97.0], [31.0, -97.0]],
        )
        dist = GeofencingManager._distance_to_boundary(fence, 30.5, -97.5)
        assert dist > 0

    def test_polygon_empty_points(self) -> None:
        fence = GeoFence(name="Test", fence_type=FenceType.POLYGON, polygon_points=[])
        assert GeofencingManager._distance_to_boundary(fence, 30.0, -97.0) == 0.0


# ── Breach queries ──────────────────────────────────────────────────


class TestBreachQueries:
    def test_get_breaches_for_node(self, manager: GeofencingManager, db: MeshDatabase) -> None:
        db.create_alert("!aaa11111", "geofence_breach", "warning", "Exit zone A")
        db.create_alert("!aaa11111", "geofence_dwell", "info", "Dwell in zone B")
        db.create_alert("!aaa11111", "low_battery", "warning", "Battery low")

        breaches = manager.get_breaches_for_node("!aaa11111")
        assert len(breaches) == 2
        alert_types = {b["alert_type"] for b in breaches}
        assert alert_types == {"geofence_breach", "geofence_dwell"}

    def test_breach_limit(self, manager: GeofencingManager, db: MeshDatabase) -> None:
        for i in range(5):
            db.create_alert("!aaa11111", "geofence_breach", "warning", f"Exit {i}")
        breaches = manager.get_breaches_for_node("!aaa11111", limit=3)
        assert len(breaches) == 3
