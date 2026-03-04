"""Edge association models — bidirectional mapping between JennEdge devices and mesh radios."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AssociationStatus(str, Enum):
    """Status of the edge-to-radio association."""

    ACTIVE = "active"
    STALE = "stale"
    DISCONNECTED = "disconnected"


class EdgeAssociation(BaseModel):
    """Mapping between a JennEdge device and its co-located mesh radio."""

    id: Optional[int] = Field(default=None, description="Auto-assigned by DB")
    edge_device_id: str = Field(description="JennEdge device identifier")
    node_id: str = Field(description="Mesh radio node_id")
    edge_hostname: Optional[str] = Field(
        default=None, description="JennEdge device hostname"
    )
    edge_ip: Optional[str] = Field(
        default=None, description="JennEdge device IP address"
    )
    association_type: str = Field(
        default="co-located",
        description="How they're associated: co-located, usb-connected, bluetooth",
    )
    status: AssociationStatus = Field(default=AssociationStatus.ACTIVE)
    last_verified: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class EdgeRadioStatus(BaseModel):
    """Combined status view of an edge node and its radio."""

    edge_device_id: str
    edge_hostname: Optional[str] = None
    edge_online: Optional[bool] = None
    node_id: str
    radio_online: bool = False
    radio_battery: Optional[int] = None
    radio_signal_rssi: Optional[int] = None
    radio_signal_snr: Optional[float] = None
    radio_latitude: Optional[float] = None
    radio_longitude: Optional[float] = None
    radio_last_seen: Optional[datetime] = None
    mesh_status: str = "unknown"
    association_status: AssociationStatus = AssociationStatus.ACTIVE
