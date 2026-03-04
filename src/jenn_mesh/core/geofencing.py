"""Geofencing manager — check node positions against virtual boundary zones.

Evaluates every GPS position update against all enabled geofences.
Generates GEOFENCE_BREACH alerts when nodes enter/exit monitored zones.
Supports circle (Haversine) and polygon (ray-casting) geometries.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Optional

from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.fleet import ALERT_SEVERITY_MAP, AlertType
from jenn_mesh.models.geofence import (
    FenceType,
    GeoFence,
    GeoFenceEvent,
    TriggerOn,
)

logger = logging.getLogger(__name__)

# Default cooldown period before re-alerting for the same fence+node
DEFAULT_COOLDOWN_SECONDS = 300  # 5 minutes


class GeofencingManager:
    """Evaluate node positions against geofence boundaries.

    Usage:
        manager = GeofencingManager(db)
        events = manager.check_position("!aaa11111", 30.27, -97.74)
        # events is a list of GeoFenceEvent for any triggered fences
    """

    def __init__(
        self,
        db: MeshDatabase,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    ):
        self.db = db
        self._cooldown_seconds = cooldown_seconds
        # Track last alert time per (fence_id, node_id) to avoid spamming
        self._last_alert: dict[tuple[int, str], datetime] = {}

    # ── Public API ───────────────────────────────────────────────────

    def check_position(self, node_id: str, lat: float, lon: float) -> list[GeoFenceEvent]:
        """Check a node's position against all enabled geofences.

        Iterates through all active fences, evaluates containment for each,
        and returns a list of breach/entry events. Also creates alerts in
        the DB for each event.

        Args:
            node_id: The mesh node reporting its position.
            lat: Latitude of the position update.
            lon: Longitude of the position update.

        Returns:
            List of GeoFenceEvent objects for each triggered fence.
        """
        fences_raw = self.db.list_geofences(enabled_only=True)
        events: list[GeoFenceEvent] = []

        for row in fences_raw:
            fence = self._row_to_geofence(row)
            if not fence.applies_to_node(node_id):
                continue

            inside = self._is_inside(fence, lat, lon)
            distance = self._distance_to_boundary(fence, lat, lon)

            # Determine if this position triggers an event
            event_type = self._evaluate_trigger(fence, inside)
            if event_type is None:
                continue

            # Cooldown: skip if we recently alerted for this fence+node
            cache_key = (fence.id, node_id)  # type: ignore[arg-type]
            now = datetime.now(timezone.utc)
            last = self._last_alert.get(cache_key)
            if last and (now - last).total_seconds() < self._cooldown_seconds:
                continue

            # Create the event
            event = GeoFenceEvent(
                fence_id=fence.id,  # type: ignore[arg-type]
                fence_name=fence.name,
                node_id=node_id,
                event_type=event_type,
                latitude=lat,
                longitude=lon,
                distance_m=distance,
                timestamp=now,
            )
            events.append(event)
            self._last_alert[cache_key] = now

            # Persist alert in the DB
            alert_type = (
                AlertType.GEOFENCE_BREACH if event_type == "exit" else AlertType.GEOFENCE_DWELL
            )
            severity = ALERT_SEVERITY_MAP[alert_type].value
            self.db.create_alert(
                node_id=node_id,
                alert_type=alert_type.value,
                severity=severity,
                message=(
                    f"Node {node_id} {event_type} geofence '{fence.name}' "
                    f"(distance: {distance:.0f}m)"
                ),
            )

        return events

    def create_fence(self, fence: GeoFence) -> int:
        """Create a new geofence in the database. Returns fence ID."""
        polygon_json = json.dumps(fence.polygon_points) if fence.polygon_points else None
        node_filter_json = json.dumps(fence.node_filter) if fence.node_filter else None
        return self.db.create_geofence(
            name=fence.name,
            fence_type=fence.fence_type.value,
            center_lat=fence.center_lat,
            center_lon=fence.center_lon,
            radius_m=fence.radius_m,
            polygon_json=polygon_json,
            node_filter=node_filter_json,
            trigger_on=fence.trigger_on.value,
            enabled=fence.enabled,
        )

    def update_fence(self, fence_id: int, updates: dict) -> bool:
        """Update a geofence. Returns True if fence existed."""
        return self.db.update_geofence(fence_id, **updates)

    def delete_fence(self, fence_id: int) -> bool:
        """Delete a geofence. Returns True if fence existed."""
        return self.db.delete_geofence(fence_id)

    def get_fence(self, fence_id: int) -> Optional[GeoFence]:
        """Get a single geofence by ID."""
        row = self.db.get_geofence(fence_id)
        if row is None:
            return None
        return self._row_to_geofence(row)

    def list_fences(self, enabled_only: bool = False) -> list[GeoFence]:
        """List all geofences."""
        rows = self.db.list_geofences(enabled_only=enabled_only)
        return [self._row_to_geofence(r) for r in rows]

    def get_breaches_for_node(self, node_id: str, limit: int = 20) -> list[dict]:
        """Get recent geofence-related alerts for a node."""
        alerts = self.db.get_active_alerts(node_id=node_id)
        fence_alerts = [
            a
            for a in alerts
            if a.get("alert_type")
            in (AlertType.GEOFENCE_BREACH.value, AlertType.GEOFENCE_DWELL.value)
        ]
        return fence_alerts[:limit]

    # ── Geometry helpers ─────────────────────────────────────────────

    @staticmethod
    def _is_inside(fence: GeoFence, lat: float, lon: float) -> bool:
        """Check if a point is inside a geofence boundary."""
        if fence.fence_type == FenceType.CIRCLE:
            if fence.center_lat is None or fence.center_lon is None or fence.radius_m is None:
                return False
            dist = GeofencingManager._haversine(lat, lon, fence.center_lat, fence.center_lon)
            return dist <= fence.radius_m
        elif fence.fence_type == FenceType.POLYGON:
            if not fence.polygon_points or len(fence.polygon_points) < 3:
                return False
            return GeofencingManager._point_in_polygon(lat, lon, fence.polygon_points)
        return False

    @staticmethod
    def _distance_to_boundary(fence: GeoFence, lat: float, lon: float) -> float:
        """Calculate distance from point to nearest fence boundary in meters."""
        if fence.fence_type == FenceType.CIRCLE:
            if fence.center_lat is None or fence.center_lon is None or fence.radius_m is None:
                return 0.0
            dist_to_center = GeofencingManager._haversine(
                lat, lon, fence.center_lat, fence.center_lon
            )
            return abs(dist_to_center - fence.radius_m)
        # For polygons, approximate as distance to nearest edge
        if not fence.polygon_points or len(fence.polygon_points) < 3:
            return 0.0
        min_dist = float("inf")
        for i in range(len(fence.polygon_points)):
            dist = GeofencingManager._haversine(
                lat, lon, fence.polygon_points[i][0], fence.polygon_points[i][1]
            )
            min_dist = min(min_dist, dist)
        return min_dist

    @staticmethod
    def _evaluate_trigger(fence: GeoFence, inside: bool) -> Optional[str]:
        """Determine if a position triggers an event given the fence's trigger mode.

        Returns 'entry', 'exit', or None.
        """
        if fence.trigger_on == TriggerOn.EXIT and not inside:
            return "exit"
        if fence.trigger_on == TriggerOn.ENTRY and inside:
            return "entry"
        if fence.trigger_on == TriggerOn.BOTH:
            if inside:
                return "entry"
            else:
                return "exit"
        return None

    @staticmethod
    def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Haversine distance between two points in meters.

        Reuses the same formula as GPSPosition.distance_to() in location.py.
        """
        R = 6371000  # Earth radius in meters
        lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def _point_in_polygon(lat: float, lon: float, polygon: list[list[float]]) -> bool:
        """Ray-casting algorithm for point-in-polygon test.

        Args:
            lat, lon: Test point coordinates.
            polygon: List of [lat, lon] vertex pairs forming a closed polygon.

        Returns:
            True if the point is inside the polygon.
        """
        n = len(polygon)
        inside = False
        j = n - 1
        for i in range(n):
            yi, xi = polygon[i][0], polygon[i][1]
            yj, xj = polygon[j][0], polygon[j][1]
            if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
                inside = not inside
            j = i
        return inside

    # ── Internal helpers ─────────────────────────────────────────────

    @staticmethod
    def _row_to_geofence(row: dict) -> GeoFence:
        """Convert a DB row dict to a GeoFence model."""
        polygon_points = None
        if row.get("polygon_json"):
            try:
                polygon_points = json.loads(row["polygon_json"])
            except (json.JSONDecodeError, TypeError):
                pass

        node_filter = None
        if row.get("node_filter"):
            try:
                node_filter = json.loads(row["node_filter"])
            except (json.JSONDecodeError, TypeError):
                pass

        return GeoFence(
            id=row.get("id"),
            name=row["name"],
            fence_type=FenceType(row.get("fence_type", "circle")),
            center_lat=row.get("center_lat"),
            center_lon=row.get("center_lon"),
            radius_m=row.get("radius_m"),
            polygon_points=polygon_points,
            node_filter=node_filter,
            trigger_on=TriggerOn(row.get("trigger_on", "exit")),
            enabled=bool(row.get("enabled", 1)),
        )
