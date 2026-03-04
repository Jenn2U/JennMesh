"""TAK (Team Awareness Kit) integration models — CoT XML gateway."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TakConnectionStatus(str, Enum):
    """TAK server connection states."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class CotType(str, Enum):
    """Common CoT (Cursor on Target) type codes."""

    FRIENDLY_GROUND = "a-f-G"  # Friendly ground unit
    FRIENDLY_GROUND_UNIT = "a-f-G-U"  # Friendly ground unit
    FRIENDLY_AIR = "a-f-A"  # Friendly air unit
    NEUTRAL_GROUND = "a-n-G"  # Neutral ground
    HOSTILE_GROUND = "a-h-G"  # Hostile ground
    UNKNOWN_GROUND = "a-u-G"  # Unknown ground
    WAYPOINT = "b-m-p-w"  # Waypoint marker
    EMERGENCY = "b-a-o-tbl"  # Emergency/911
    SENSOR = "a-f-G-E-S"  # Sensor point
    RELAY = "a-f-G-U-C-I"  # Comms relay


# Default TAK connection settings
DEFAULT_TAK_PORT = 8087  # TAK Server default TCP port
DEFAULT_TAK_TLS_PORT = 8089  # TAK Server TLS port


class TakServerConfig(BaseModel):
    """Configuration for TAK server connectivity."""

    host: str = Field(description="TAK server hostname or IP")
    port: int = Field(default=DEFAULT_TAK_PORT, description="TAK server port")
    use_tls: bool = Field(default=False, description="Use TLS for TAK connection")
    callsign_prefix: str = Field(
        default="JENN-",
        description="Prefix for mesh node callsigns on TAK COP",
    )
    stale_timeout_seconds: int = Field(
        default=600,
        description="Seconds before CoT markers become stale on TAK COP",
    )
    enabled: bool = Field(default=True)


class CotEvent(BaseModel):
    """A Cursor on Target event — the atomic unit of TAK situational awareness."""

    uid: str = Field(description="Unique event ID (mesh node_id based)")
    cot_type: str = Field(
        default=CotType.FRIENDLY_GROUND.value,
        description="CoT type code (2525C symbology)",
    )
    callsign: str = Field(description="Human-readable callsign for TAK COP")
    latitude: float = Field(description="WGS84 latitude")
    longitude: float = Field(description="WGS84 longitude")
    altitude: float = Field(default=0.0, description="Altitude in meters HAE")
    ce: float = Field(default=50.0, description="Circular error (meters)")
    le: float = Field(default=50.0, description="Linear error (meters)")
    speed: Optional[float] = Field(default=None, description="Speed in m/s")
    course: Optional[float] = Field(default=None, description="Heading in degrees")
    battery: Optional[int] = Field(default=None, description="Battery percentage")
    time: Optional[datetime] = None
    start: Optional[datetime] = None
    stale: Optional[datetime] = None

    @property
    def remarks(self) -> str:
        """Build TAK remarks string with mesh metadata."""
        parts = [f"JennMesh node {self.uid}"]
        if self.battery is not None:
            parts.append(f"Batt: {self.battery}%")
        if self.speed is not None:
            parts.append(f"Speed: {self.speed:.1f}m/s")
        return " | ".join(parts)


class TakGatewayStatus(BaseModel):
    """Runtime status of the TAK gateway."""

    connection_status: TakConnectionStatus = TakConnectionStatus.DISCONNECTED
    server_host: Optional[str] = None
    server_port: Optional[int] = None
    events_sent: int = 0
    events_received: int = 0
    last_event_time: Optional[datetime] = None
    errors: list[str] = Field(default_factory=list)
    tracked_nodes: int = 0
