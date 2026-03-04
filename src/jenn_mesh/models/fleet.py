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
    CONFIG_PUSH_FAILED = "config_push_failed"
    FAILOVER_ACTIVATED = "failover_activated"
    FAILOVER_REVERTED = "failover_reverted"
    FAILOVER_REVERT_FAILED = "failover_revert_failed"
    CONFIG_ROLLBACK_TRIGGERED = "config_rollback_triggered"
    CONFIG_ROLLBACK_COMPLETED = "config_rollback_completed"
    CONFIG_ROLLBACK_FAILED = "config_rollback_failed"
    SYNC_RELAY_STARTED = "sync_relay_started"
    SYNC_RELAY_COMPLETED = "sync_relay_completed"
    SYNC_RELAY_FAILED = "sync_relay_failed"
    SYNC_SV_MISMATCH = "sync_sv_mismatch"
    # v0.4.0 — Intelligence & Analytics
    ANOMALY_DETECTED = "anomaly_detected"
    GEOFENCE_BREACH = "geofence_breach"
    GEOFENCE_DWELL = "geofence_dwell"
    COVERAGE_GAP = "coverage_gap"
    COVERAGE_DEGRADED = "coverage_degraded"
    # v0.5.0 — Environmental telemetry
    ENV_THRESHOLD_EXCEEDED = "env_threshold_exceeded"


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
    AlertType.CONFIG_PUSH_FAILED: AlertSeverity.WARNING,
    AlertType.FAILOVER_ACTIVATED: AlertSeverity.WARNING,
    AlertType.FAILOVER_REVERTED: AlertSeverity.INFO,
    AlertType.FAILOVER_REVERT_FAILED: AlertSeverity.CRITICAL,
    AlertType.CONFIG_ROLLBACK_TRIGGERED: AlertSeverity.WARNING,
    AlertType.CONFIG_ROLLBACK_COMPLETED: AlertSeverity.INFO,
    AlertType.CONFIG_ROLLBACK_FAILED: AlertSeverity.CRITICAL,
    AlertType.SYNC_RELAY_STARTED: AlertSeverity.INFO,
    AlertType.SYNC_RELAY_COMPLETED: AlertSeverity.INFO,
    AlertType.SYNC_RELAY_FAILED: AlertSeverity.WARNING,
    AlertType.SYNC_SV_MISMATCH: AlertSeverity.INFO,
    # v0.4.0 — Intelligence & Analytics
    AlertType.ANOMALY_DETECTED: AlertSeverity.WARNING,
    AlertType.GEOFENCE_BREACH: AlertSeverity.WARNING,
    AlertType.GEOFENCE_DWELL: AlertSeverity.INFO,
    AlertType.COVERAGE_GAP: AlertSeverity.INFO,
    AlertType.COVERAGE_DEGRADED: AlertSeverity.WARNING,
    # v0.5.0 — Environmental telemetry
    AlertType.ENV_THRESHOLD_EXCEEDED: AlertSeverity.WARNING,
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
