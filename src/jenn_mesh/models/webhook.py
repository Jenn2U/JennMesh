"""Webhook models — event types, config, delivery tracking, and payloads."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class WebhookEventType(str, Enum):
    """Event types that can trigger webhook deliveries."""

    NODE_ONLINE = "node_online"
    NODE_OFFLINE = "node_offline"
    ALERT_CREATED = "alert_created"
    ALERT_RESOLVED = "alert_resolved"
    GEOFENCE_ENTRY = "geofence_entry"
    GEOFENCE_EXIT = "geofence_exit"
    EMERGENCY_BROADCAST = "emergency_broadcast"
    PARTITION_DETECTED = "partition_detected"
    PARTITION_RESOLVED = "partition_resolved"
    BULK_OP_COMPLETED = "bulk_op_completed"


class WebhookConfig(BaseModel):
    """Webhook registration — an HTTP POST target for fleet events."""

    id: Optional[int] = Field(default=None, description="Webhook ID (auto-assigned)")
    name: str = Field(description="Human-readable label for this webhook")
    url: str = Field(description="HTTP POST target URL (must be HTTPS in production)")
    secret: str = Field(default="", description="HMAC-SHA256 signing secret")
    event_types: list[str] = Field(
        default_factory=list,
        description="Event types to subscribe to (empty = all events)",
    )
    is_active: bool = Field(default=True)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class WebhookDeliveryStatus(str, Enum):
    """Delivery attempt status."""

    PENDING = "pending"
    RETRYING = "retrying"
    DELIVERED = "delivered"
    FAILED = "failed"


class WebhookPayload(BaseModel):
    """Standard webhook payload envelope."""

    event_type: str = Field(description="The event that triggered this delivery")
    timestamp: str = Field(description="ISO-8601 UTC timestamp of the event")
    data: dict[str, Any] = Field(
        default_factory=dict, description="Event-specific data"
    )
    source: str = Field(default="jenn-mesh", description="Originating service")
