"""Asset tracking models — vehicles, equipment, personnel tracking via mesh GPS."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AssetType(str, Enum):
    """Types of trackable assets."""

    VEHICLE = "vehicle"
    EQUIPMENT = "equipment"
    PERSONNEL = "personnel"
    CONTAINER = "container"
    DRONE = "drone"
    OTHER = "other"


class AssetStatus(str, Enum):
    """Current asset status."""

    ACTIVE = "active"
    IDLE = "idle"
    IN_TRANSIT = "in_transit"
    OUT_OF_RANGE = "out_of_range"
    MAINTENANCE = "maintenance"


class Asset(BaseModel):
    """A tracked asset linked to a mesh node."""

    id: Optional[int] = Field(default=None, description="Auto-assigned by DB")
    name: str = Field(description="Human-readable asset name")
    asset_type: AssetType = Field(description="Type of asset")
    node_id: str = Field(description="Associated mesh radio node_id")
    zone: Optional[str] = Field(default=None, description="Assigned zone/area")
    team: Optional[str] = Field(default=None, description="Assigned team")
    project: Optional[str] = Field(default=None, description="Assigned project")
    status: AssetStatus = Field(default=AssetStatus.ACTIVE)
    metadata_json: Optional[str] = Field(
        default=None, description="Extra metadata as JSON string"
    )
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AssetPosition(BaseModel):
    """A position sample for an asset, enriched with speed and heading."""

    asset_id: int
    node_id: str
    latitude: float
    longitude: float
    altitude: Optional[float] = None
    speed_mps: Optional[float] = Field(
        default=None, description="Speed in meters/second"
    )
    heading_deg: Optional[float] = Field(
        default=None, description="Heading in degrees (0-360)"
    )
    timestamp: Optional[datetime] = None


class AssetTrail(BaseModel):
    """Position history for an asset."""

    asset_id: int
    asset_name: str
    node_id: str
    positions: list[AssetPosition] = Field(default_factory=list)
    total_distance_m: float = 0.0
    avg_speed_mps: Optional[float] = None
    time_span_hours: Optional[float] = None
