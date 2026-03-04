"""Encryption audit models for fleet channel security assessment."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class EncryptionStatus(str, Enum):
    """Classification of a device's channel encryption strength."""

    STRONG = "strong"
    WEAK = "weak"
    UNENCRYPTED = "unencrypted"
    UNKNOWN = "unknown"


class DeviceEncryptionAudit(BaseModel):
    """Encryption audit result for a single device."""

    node_id: str
    encryption_status: EncryptionStatus
    weak_channels: list[dict] = Field(default_factory=list)
    mqtt_encryption_enabled: Optional[bool] = None
    uses_default_longfast: bool = False
    channel_count: int = Field(default=0, description="Total channels audited")


class FleetEncryptionReport(BaseModel):
    """Fleet-wide encryption audit summary."""

    fleet_score: float = Field(description="Percentage of devices with strong encryption (0-100)")
    total_devices: int
    strong_count: int
    weak_count: int
    unencrypted_count: int
    unknown_count: int
    devices: list[DeviceEncryptionAudit] = Field(default_factory=list)
