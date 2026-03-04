"""Geofencing models — virtual boundary zones for mesh node tracking."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class FenceType(str, Enum):
    """Supported geofence geometry types."""

    CIRCLE = "circle"
    POLYGON = "polygon"


class TriggerOn(str, Enum):
    """When a geofence should fire alerts."""

    ENTRY = "entry"
    EXIT = "exit"
    BOTH = "both"


class GeoFence(BaseModel):
    """A geofence zone definition.

    Circle fences use center_lat/center_lon/radius_m.
    Polygon fences use polygon_points (list of [lat, lon] pairs).
    """

    id: Optional[int] = Field(default=None, description="Auto-assigned by DB")
    name: str = Field(description="Human-readable fence name")
    fence_type: FenceType = Field(default=FenceType.CIRCLE)
    center_lat: Optional[float] = Field(
        default=None, ge=-90.0, le=90.0, description="Circle center latitude"
    )
    center_lon: Optional[float] = Field(
        default=None, ge=-180.0, le=180.0, description="Circle center longitude"
    )
    radius_m: Optional[float] = Field(default=None, gt=0, description="Circle radius in meters")
    polygon_points: Optional[list[list[float]]] = Field(
        default=None, description="Polygon vertices as [[lat, lon], ...]"
    )
    node_filter: Optional[list[str]] = Field(
        default=None, description="Node IDs to monitor (null = all nodes)"
    )
    trigger_on: TriggerOn = Field(default=TriggerOn.EXIT)
    enabled: bool = Field(default=True)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def applies_to_node(self, node_id: str) -> bool:
        """Check if this fence monitors a specific node."""
        if self.node_filter is None:
            return True
        return node_id in self.node_filter


class GeoFenceEvent(BaseModel):
    """A geofence breach or entry event."""

    fence_id: int = Field(description="Which fence was triggered")
    fence_name: str = Field(description="Human-readable fence name")
    node_id: str = Field(description="Node that triggered the event")
    event_type: str = Field(description="'entry' or 'exit'")
    latitude: float
    longitude: float
    distance_m: Optional[float] = Field(
        default=None, description="Distance from fence boundary (for circles)"
    )
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class GeoFenceCheck(BaseModel):
    """Result of checking a node position against all active fences."""

    node_id: str
    latitude: float
    longitude: float
    events: list[GeoFenceEvent] = Field(
        default_factory=list, description="Breach/entry events detected"
    )
    fences_checked: int = Field(default=0, description="Number of fences evaluated")
