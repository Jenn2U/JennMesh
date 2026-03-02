"""Mesh device models — radio inventory and metadata."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DeviceRole(str, Enum):
    """Meshtastic device roles — maps to device.role config values."""

    RELAY = "ROUTER"
    GATEWAY = "CLIENT_MUTE"
    MOBILE = "CLIENT"
    SENSOR = "SENSOR"
    REPEATER = "REPEATER"
    ROUTER_CLIENT = "ROUTER_CLIENT"
    TRACKER = "TRACKER"

    @classmethod
    def from_meshtastic(cls, role_str: str) -> DeviceRole:
        """Convert Meshtastic role string to DeviceRole."""
        for member in cls:
            if member.value == role_str:
                return member
        return cls.MOBILE  # Safe default


class HardwareModel(str, Enum):
    """Common Meshtastic hardware platforms."""

    HELTEC_V3 = "heltec_v3"
    TBEAM = "tbeam"
    TBEAM_S3 = "tbeam_s3"
    RAK4631 = "rak4631"
    TECHO = "t_echo"
    STATION_G2 = "station_g2"
    NANO_G2 = "nano_g2"
    XIAO = "xiao"
    OTHER = "other"


class FirmwareInfo(BaseModel):
    """Firmware version and update status."""

    version: str = Field(description="Current firmware version (e.g., '2.5.6')")
    hw_model: str = Field(default="unknown", description="Hardware model identifier")
    needs_update: bool = Field(default=False, description="Flag: newer firmware available")
    latest_available: Optional[str] = Field(
        default=None, description="Latest firmware version for this hardware"
    )


class ConfigHash(BaseModel):
    """Hash of device configuration for drift detection."""

    hash: str = Field(description="SHA-256 hash of exported YAML config")
    template_role: Optional[DeviceRole] = Field(
        default=None, description="Golden template role this device was provisioned from"
    )
    template_hash: Optional[str] = Field(
        default=None, description="Hash of the golden template at provisioning time"
    )
    drifted: bool = Field(
        default=False, description="True if device config differs from golden template"
    )

    @staticmethod
    def compute(yaml_content: str) -> str:
        """Compute SHA-256 hash of YAML config content."""
        return hashlib.sha256(yaml_content.encode("utf-8")).hexdigest()


class MeshDevice(BaseModel):
    """A managed Meshtastic radio device."""

    node_id: str = Field(description="Meshtastic node ID (e.g., '!28979058')")
    long_name: str = Field(default="", description="User-friendly device name")
    short_name: str = Field(default="", description="4-char short name")
    role: DeviceRole = Field(default=DeviceRole.MOBILE, description="Device role")
    firmware: FirmwareInfo = Field(default_factory=FirmwareInfo)
    config_hash: Optional[ConfigHash] = Field(default=None, description="Config drift tracking")
    battery_level: Optional[int] = Field(
        default=None, ge=0, le=100, description="Battery percentage"
    )
    voltage: Optional[float] = Field(default=None, description="Battery voltage")
    signal_snr: Optional[float] = Field(default=None, description="Last received SNR")
    signal_rssi: Optional[int] = Field(default=None, description="Last received RSSI")
    last_seen: Optional[datetime] = Field(default=None, description="Last telemetry timestamp")
    registered_at: Optional[datetime] = Field(default=None, description="When device was added")
    is_online: bool = Field(default=False, description="Within offline threshold")
    latitude: Optional[float] = Field(default=None, description="Last known latitude")
    longitude: Optional[float] = Field(default=None, description="Last known longitude")
    altitude: Optional[float] = Field(default=None, description="Last known altitude (meters)")
    associated_edge_node: Optional[str] = Field(
        default=None, description="JennEdge device_id this radio is paired with"
    )

    @property
    def display_name(self) -> str:
        """Human-readable name: long_name or node_id."""
        return self.long_name or self.node_id
