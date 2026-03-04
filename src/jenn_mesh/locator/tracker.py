"""GPS position tracker — aggregates position reports from mesh network."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.location import GPSPosition


class PositionTracker:
    """Aggregates and queries GPS positions from the mesh fleet."""

    def __init__(self, db: MeshDatabase):
        self.db = db

    def get_latest_position(self, node_id: str) -> Optional[GPSPosition]:
        """Get the most recent GPS position for a device."""
        row = self.db.get_latest_position(node_id)
        if row is None:
            return None

        return GPSPosition(
            node_id=row["node_id"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            altitude=row.get("altitude"),
            precision_bits=row.get("precision_bits"),
            timestamp=datetime.fromisoformat(row["timestamp"]),
            source=row.get("source", "gps"),
        )

    def get_position_age_hours(self, node_id: str) -> Optional[float]:
        """Get the age of the latest position in hours."""
        pos = self.get_latest_position(node_id)
        if pos is None:
            return None
        delta = datetime.utcnow() - pos.timestamp
        return delta.total_seconds() / 3600

    def get_nearby_positions(
        self,
        latitude: float,
        longitude: float,
        radius_meters: float = 5000,
    ) -> list[dict]:
        """Find positions of devices near a given coordinate.

        Uses a bounding-box filter at the DB level, then refines
        with Haversine distance for accuracy.
        """
        # Convert radius to approximate degree offset
        # 1 degree latitude ≈ 111km, longitude varies with latitude
        radius_deg = radius_meters / 111_000

        candidates = self.db.get_positions_in_radius(latitude, longitude, radius_deg)

        center = GPSPosition(
            node_id="query_center",
            latitude=latitude,
            longitude=longitude,
        )

        results = []
        for row in candidates:
            candidate_pos = GPSPosition(
                node_id=row["node_id"],
                latitude=row["latitude"],
                longitude=row["longitude"],
                altitude=row.get("altitude"),
                timestamp=datetime.fromisoformat(row["timestamp"]),
            )
            distance = center.distance_to(candidate_pos)
            if distance <= radius_meters:
                results.append(
                    {
                        "node_id": row["node_id"],
                        "long_name": row.get("long_name", ""),
                        "latitude": row["latitude"],
                        "longitude": row["longitude"],
                        "distance_meters": round(distance, 1),
                        "last_seen": row.get("last_seen"),
                        "timestamp": row["timestamp"],
                    }
                )

        return sorted(results, key=lambda x: x["distance_meters"])

    def get_all_latest_positions(self) -> list[GPSPosition]:
        """Get the most recent position for every device (for fleet map)."""
        devices = self.db.list_devices()
        positions = []

        for device in devices:
            lat = device.get("latitude")
            lon = device.get("longitude")
            if lat is not None and lon is not None:
                positions.append(
                    GPSPosition(
                        node_id=device["node_id"],
                        latitude=lat,
                        longitude=lon,
                        altitude=device.get("altitude"),
                        timestamp=(
                            datetime.fromisoformat(device["last_seen"])
                            if device.get("last_seen")
                            else datetime.utcnow()
                        ),
                    )
                )

        return positions
