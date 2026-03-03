"""Mesh heartbeat models — edge node heartbeat via LoRa radio text messages."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ServiceStatus(BaseModel):
    """Status of an individual service on the edge node."""

    name: str = Field(description="Service name (e.g., 'edge', 'mqtt', 'internet')")
    status: str = Field(description="Service status: 'ok' or 'down'")


class MeshHeartbeat(BaseModel):
    """Parsed heartbeat message received from an edge node agent via mesh radio."""

    node_id: str = Field(description="Meshtastic node ID (e.g., '!28979058')")
    uptime_seconds: int = Field(ge=0, description="Agent uptime in seconds")
    services: list[ServiceStatus] = Field(
        default_factory=list, description="Service statuses on the edge node"
    )
    battery: int = Field(default=-1, ge=-1, le=100, description="Battery %, -1 if unknown")
    timestamp: datetime = Field(description="When heartbeat was sent (sender clock)")
    received_at: datetime = Field(
        default_factory=datetime.utcnow, description="When dashboard received it"
    )
    rssi: Optional[int] = Field(default=None, description="Signal RSSI at reception")
    snr: Optional[float] = Field(default=None, description="Signal SNR at reception")

    def services_json(self) -> str:
        """Serialize services list to JSON for DB storage."""
        return json.dumps([s.model_dump() for s in self.services])

    @staticmethod
    def services_from_json(raw: str) -> list[ServiceStatus]:
        """Deserialize services JSON from DB."""
        try:
            return [ServiceStatus(**s) for s in json.loads(raw)]
        except (json.JSONDecodeError, TypeError, KeyError):
            return []

    @staticmethod
    def parse_services_string(raw: str) -> list[ServiceStatus]:
        """Parse 'edge:ok,mqtt:down' format into ServiceStatus list."""
        if not raw:
            return []
        result = []
        for pair in raw.split(","):
            parts = pair.strip().split(":")
            if len(parts) == 2:
                result.append(ServiceStatus(name=parts[0].strip(), status=parts[1].strip()))
        return result

    @staticmethod
    def format_services_string(services: list[ServiceStatus]) -> str:
        """Format ServiceStatus list as 'edge:ok,mqtt:down' string."""
        return ",".join(f"{s.name}:{s.status}" for s in services)


class HeartbeatSummary(BaseModel):
    """Summary of heartbeat history for a device."""

    node_id: str
    last_heartbeat: Optional[datetime] = Field(default=None)
    heartbeat_count: int = Field(default=0)
    avg_interval_seconds: Optional[float] = Field(default=None)
    is_mesh_reachable: bool = Field(default=False)
