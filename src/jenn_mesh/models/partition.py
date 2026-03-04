"""Network partition event models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class PartitionEventType(str, Enum):
    """Types of partition events."""

    PARTITION_DETECTED = "partition_detected"
    PARTITION_RESOLVED = "partition_resolved"


class PartitionEvent(BaseModel):
    """A network partition or merge event with topology diff."""

    id: Optional[int] = Field(default=None, description="Event ID (auto-assigned)")
    event_type: PartitionEventType
    component_count: int = Field(description="Number of connected components after event")
    components_json: str = Field(default="[]", description="JSON list of component node-id lists")
    previous_component_count: int = Field(
        default=1, description="Component count before this event"
    )
    relay_recommendation: Optional[str] = Field(
        default=None,
        description="Suggested relay placement to bridge components (GPS centroid)",
    )
    resolved_at: Optional[str] = Field(default=None)
    created_at: Optional[str] = Field(default=None)


class PartitionStatus(BaseModel):
    """Current partition status summary."""

    is_partitioned: bool = Field(default=False)
    component_count: int = Field(default=1)
    components: list[list[str]] = Field(default_factory=list)
    latest_event: Optional[PartitionEvent] = Field(default=None)
    relay_recommendations: list[str] = Field(default_factory=list)
