"""Asset Tracker — vehicles, equipment, personnel tracking via mesh GPS.

Builds on the existing positions table and geofencing system to provide
asset-level tracking with trail history, speed/heading computation, and
geofence integration.

Usage::

    tracker = AssetTracker(db=app.state.db)
    tracker.register_asset(name="Truck-01", asset_type="vehicle", node_id="!2a3b4c")
    trail = tracker.get_trail("!2a3b4c", hours=24)
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from typing import Optional

from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.asset_tracking import (
    Asset,
    AssetPosition,
    AssetStatus,
    AssetTrail,
    AssetType,
)

logger = logging.getLogger(__name__)


def _haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two GPS points in meters."""
    R = 6371000  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _bearing_degrees(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate bearing from point 1 to point 2 in degrees (0-360)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    x = math.sin(dlam) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360


class AssetTracker:
    """Manages asset tracking — registration, trail history, speed/heading."""

    def __init__(self, db: MeshDatabase):
        self._db = db

    def register_asset(
        self,
        name: str,
        asset_type: str,
        node_id: str,
        zone: str | None = None,
        team: str | None = None,
        project: str | None = None,
        metadata: dict | None = None,
    ) -> Asset:
        """Register a new trackable asset.

        Args:
            name: Human-readable asset name.
            asset_type: One of AssetType values.
            node_id: Associated mesh radio node_id.
            zone: Assigned zone/area.
            team: Assigned team.
            project: Assigned project.
            metadata: Extra metadata dict (stored as JSON).

        Returns:
            Asset model with DB-assigned ID.

        Raises:
            ValueError: If asset_type is invalid or node_id is empty.
        """
        # Validate type
        try:
            AssetType(asset_type)
        except ValueError:
            raise ValueError(
                f"Invalid asset_type '{asset_type}'. "
                f"Must be one of: {[t.value for t in AssetType]}"
            )

        if not node_id or not node_id.strip():
            raise ValueError("node_id is required")

        metadata_json = json.dumps(metadata) if metadata else None
        asset_id = self._db.create_asset(
            name=name,
            asset_type=asset_type,
            node_id=node_id,
            zone=zone,
            team=team,
            project=project,
            metadata_json=metadata_json,
        )

        return Asset(
            id=asset_id,
            name=name,
            asset_type=AssetType(asset_type),
            node_id=node_id,
            zone=zone,
            team=team,
            project=project,
            metadata_json=metadata_json,
        )

    def get_asset(self, asset_id: int) -> Optional[dict]:
        """Get asset by ID."""
        return self._db.get_asset(asset_id)

    def get_asset_by_node(self, node_id: str) -> Optional[dict]:
        """Get asset by associated node_id."""
        return self._db.get_asset_by_node(node_id)

    def list_assets(
        self,
        asset_type: str | None = None,
        zone: str | None = None,
        team: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """List assets with optional filters."""
        return self._db.list_assets(asset_type=asset_type, zone=zone, team=team, status=status)

    def update_asset(self, asset_id: int, **kwargs: object) -> bool:
        """Update asset fields."""
        return self._db.update_asset(asset_id, **kwargs)

    def delete_asset(self, asset_id: int) -> bool:
        """Delete an asset."""
        return self._db.delete_asset(asset_id)

    def get_trail(
        self,
        node_id: str,
        hours: int = 24,
        limit: int = 500,
    ) -> AssetTrail:
        """Get position trail for an asset with computed speed and heading.

        Args:
            node_id: Mesh radio node_id.
            hours: Time window in hours (default: 24).
            limit: Max positions to return.

        Returns:
            AssetTrail with enriched positions and summary stats.
        """
        asset = self._db.get_asset_by_node(node_id)
        asset_id = asset["id"] if asset else 0
        asset_name = asset["name"] if asset else node_id

        raw_positions = self._db.get_asset_position_trail(node_id=node_id, hours=hours, limit=limit)

        # Reverse to chronological order for speed/heading computation
        raw_positions.reverse()

        positions: list[AssetPosition] = []
        total_distance = 0.0
        speeds: list[float] = []

        for i, pos in enumerate(raw_positions):
            speed = None
            heading = None

            if i > 0:
                prev = raw_positions[i - 1]
                dist = _haversine_meters(
                    prev["latitude"],
                    prev["longitude"],
                    pos["latitude"],
                    pos["longitude"],
                )
                total_distance += dist

                # Compute speed from time delta
                try:
                    t_curr = datetime.fromisoformat(pos["timestamp"])
                    t_prev = datetime.fromisoformat(prev["timestamp"])
                    dt = (t_curr - t_prev).total_seconds()
                    if dt > 0:
                        speed = dist / dt
                        speeds.append(speed)
                except (ValueError, TypeError):
                    pass

                # Compute heading
                heading = _bearing_degrees(
                    prev["latitude"],
                    prev["longitude"],
                    pos["latitude"],
                    pos["longitude"],
                )

            positions.append(
                AssetPosition(
                    asset_id=asset_id,
                    node_id=node_id,
                    latitude=pos["latitude"],
                    longitude=pos["longitude"],
                    altitude=pos.get("altitude"),
                    speed_mps=speed,
                    heading_deg=heading,
                    timestamp=pos.get("timestamp"),
                )
            )

        # Compute time span
        time_span = None
        if len(raw_positions) >= 2:
            try:
                t_first = datetime.fromisoformat(raw_positions[0]["timestamp"])
                t_last = datetime.fromisoformat(raw_positions[-1]["timestamp"])
                time_span = (t_last - t_first).total_seconds() / 3600
            except (ValueError, TypeError):
                pass

        return AssetTrail(
            asset_id=asset_id,
            asset_name=asset_name,
            node_id=node_id,
            positions=positions,
            total_distance_m=total_distance,
            avg_speed_mps=sum(speeds) / len(speeds) if speeds else None,
            time_span_hours=time_span,
        )

    def update_asset_statuses(self) -> int:
        """Update asset statuses based on their node's last_seen timestamp.

        Called periodically (e.g., from watchdog) to keep statuses current.
        Returns number of assets whose status changed.
        """
        assets = self._db.list_assets()
        changed = 0
        for asset in assets:
            device = None
            with self._db.connection() as conn:
                row = conn.execute(
                    "SELECT last_seen FROM devices WHERE node_id = ?",
                    (asset["node_id"],),
                ).fetchone()
                device = dict(row) if row else None

            if device is None or device.get("last_seen") is None:
                if asset["status"] != AssetStatus.OUT_OF_RANGE.value:
                    self._db.update_asset(asset["id"], status=AssetStatus.OUT_OF_RANGE.value)
                    changed += 1
                continue

            try:
                last_seen = datetime.fromisoformat(device["last_seen"])
                now = datetime.utcnow()
                age_minutes = (now - last_seen).total_seconds() / 60

                if age_minutes > 30:
                    new_status = AssetStatus.OUT_OF_RANGE.value
                elif age_minutes > 10:
                    new_status = AssetStatus.IDLE.value
                else:
                    new_status = AssetStatus.ACTIVE.value

                if asset["status"] != new_status:
                    self._db.update_asset(asset["id"], status=new_status)
                    changed += 1
            except (ValueError, TypeError):
                pass

        if changed:
            logger.info("Updated %d asset statuses", changed)
        return changed
