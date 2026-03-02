"""Location models — GPS tracking and lost node locator."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class GPSPosition(BaseModel):
    """A GPS position report from a mesh node."""

    node_id: str = Field(description="Reporting device node_id")
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    altitude: Optional[float] = Field(default=None, description="Altitude in meters")
    precision_bits: Optional[int] = Field(
        default=None, description="Meshtastic position precision (higher = more precise)"
    )
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    source: str = Field(default="gps", description="Position source: gps, manual, mesh_estimate")

    def distance_to(self, other: GPSPosition) -> float:
        """Haversine distance to another position in meters."""
        R = 6371000  # Earth radius in meters
        lat1, lat2 = math.radians(self.latitude), math.radians(other.latitude)
        dlat = math.radians(other.latitude - self.latitude)
        dlon = math.radians(other.longitude - self.longitude)
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class LostNodeQuery(BaseModel):
    """Query parameters for finding a lost node."""

    target_node_id: str = Field(description="Node ID of the device to locate")
    search_radius_meters: float = Field(
        default=5000.0, description="Search radius for nearby nodes"
    )
    max_age_hours: float = Field(
        default=72.0, description="Maximum age of position data to consider"
    )


class NearbyNode(BaseModel):
    """A mesh node near the last known position of a lost node."""

    node_id: str
    distance_meters: float
    position: GPSPosition
    is_online: bool = Field(default=False)


class ProximityResult(BaseModel):
    """Result of a lost node location query."""

    target_node_id: str
    last_known_position: Optional[GPSPosition] = Field(
        default=None, description="Last reported GPS position of the target"
    )
    position_age_hours: Optional[float] = Field(
        default=None, description="Hours since last position report"
    )
    nearby_nodes: list[NearbyNode] = Field(
        default_factory=list, description="Active nodes near last known position"
    )
    confidence: str = Field(
        default="unknown",
        description="Location confidence: high (fresh GPS), medium (stale GPS), low (no GPS)",
    )
    associated_edge_node: Optional[str] = Field(
        default=None, description="JennEdge device_id if paired"
    )

    @property
    def is_found(self) -> bool:
        """Whether we have any position data for the target."""
        return self.last_known_position is not None
