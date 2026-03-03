"""Fleet health models — monitoring, alerts, and status aggregation."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AlertType(str, Enum):
    """Types of fleet health alerts."""

    NODE_OFFLINE = "node_offline"
    LOW_BATTERY = "low_battery"
    SIGNAL_DEGRADED = "signal_degraded"
    CONFIG_DRIFT = "config_drift"
    FIRMWARE_OUTDATED = "firmware_outdated"
    POSITION_STALE = "position_stale"
    MQTT_DISCONNECTED = "mqtt_disconnected"
    BASELINE_DEVIATION = "baseline_deviation"
    INTERNET_DOWN = "internet_down"
    EMERGENCY_BROADCAST = "emergency_broadcast"
    RECOVERY_SENT = "recovery_sent"


class AlertSeverity(str, Enum):
    """Alert severity levels."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class Alert(BaseModel):
    """A fleet health alert for a specific device."""

    id: Optional[int] = Field(default=None, description="Alert ID (auto-assigned by DB)")
    node_id: str = Field(description="Affected device node_id")
    alert_type: AlertType
    severity: AlertSeverity
    message: str = Field(description="Human-readable alert description")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = Field(default=None)
    is_resolved: bool = Field(default=False)

    @property
    def is_active(self) -> bool:
        return not self.is_resolved


ALERT_SEVERITY_MAP: dict[AlertType, AlertSeverity] = {
    AlertType.NODE_OFFLINE: AlertSeverity.CRITICAL,
    AlertType.LOW_BATTERY: AlertSeverity.WARNING,
    AlertType.SIGNAL_DEGRADED: AlertSeverity.WARNING,
    AlertType.CONFIG_DRIFT: AlertSeverity.WARNING,
    AlertType.FIRMWARE_OUTDATED: AlertSeverity.INFO,
    AlertType.POSITION_STALE: AlertSeverity.INFO,
    AlertType.MQTT_DISCONNECTED: AlertSeverity.CRITICAL,
    AlertType.BASELINE_DEVIATION: AlertSeverity.WARNING,
    AlertType.INTERNET_DOWN: AlertSeverity.WARNING,
    AlertType.EMERGENCY_BROADCAST: AlertSeverity.CRITICAL,
    AlertType.RECOVERY_SENT: AlertSeverity.INFO,
}


class NodeStatus(str, Enum):
    """Aggregated node status."""

    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"
    REACHABLE_VIA_MESH = "reachable_via_mesh"


class FleetHealth(BaseModel):
    """Aggregate fleet health summary."""

    total_devices: int = Field(default=0)
    online_count: int = Field(default=0)
    offline_count: int = Field(default=0)
    degraded_count: int = Field(default=0)
    active_alerts: int = Field(default=0)
    critical_alerts: int = Field(default=0)
    devices_needing_update: int = Field(default=0)
    devices_with_drift: int = Field(default=0)
    mesh_reachable_count: int = Field(
        default=0, description="Devices reachable via mesh radio but internet may be down"
    )
    last_updated: datetime = Field(default_factory=datetime.utcnow)

    @property
    def health_score(self) -> float:
        """Fleet health as percentage (0-100). Higher is better."""
        if self.total_devices == 0:
            return 100.0
        return (self.online_count / self.total_devices) * 100.0
