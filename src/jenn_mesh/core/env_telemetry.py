"""Environmental telemetry manager — threshold-based alerting and aggregation.

Ingests Meshtastic environment sensor data (temperature, humidity,
pressure, air quality) and evaluates configurable thresholds to
trigger ENV_THRESHOLD_EXCEEDED alerts.
"""

from __future__ import annotations

import logging
from typing import Optional

from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.env_telemetry import EnvAlert, EnvThreshold
from jenn_mesh.models.fleet import ALERT_SEVERITY_MAP, AlertType

logger = logging.getLogger(__name__)

# Default thresholds — operator can override via API
DEFAULT_THRESHOLDS: list[dict] = [
    {"metric": "temperature", "min_value": -20.0, "max_value": 60.0, "enabled": True},
    {"metric": "humidity", "min_value": 0.0, "max_value": 100.0, "enabled": True},
    {"metric": "pressure", "min_value": 870.0, "max_value": 1084.0, "enabled": True},
    {"metric": "air_quality", "min_value": None, "max_value": 300, "enabled": True},
]


class EnvTelemetryManager:
    """Manage environmental sensor data and threshold-based alerting.

    Usage:
        mgr = EnvTelemetryManager(db)
        alerts = mgr.ingest_reading("!aaa11111", temperature=45.0, humidity=90.0)
    """

    def __init__(self, db: MeshDatabase, thresholds: Optional[list[dict]] = None):
        self.db = db
        self._thresholds = [EnvThreshold(**t) for t in (thresholds or DEFAULT_THRESHOLDS)]

    # ── Ingestion ──────────────────────────────────────────────────

    def ingest_reading(
        self,
        node_id: str,
        temperature: Optional[float] = None,
        humidity: Optional[float] = None,
        pressure: Optional[float] = None,
        air_quality: Optional[int] = None,
        timestamp: Optional[str] = None,
    ) -> list[EnvAlert]:
        """Record an environmental reading and check thresholds.

        Returns list of EnvAlerts for any threshold breaches.
        """
        # Store in DB
        self.db.add_env_reading(
            node_id=node_id,
            temperature=temperature,
            humidity=humidity,
            pressure=pressure,
            air_quality=air_quality,
            timestamp=timestamp,
        )

        # Check thresholds
        values = {
            "temperature": temperature,
            "humidity": humidity,
            "pressure": pressure,
            "air_quality": float(air_quality) if air_quality is not None else None,
        }

        alerts: list[EnvAlert] = []
        for threshold in self._thresholds:
            if not threshold.enabled:
                continue
            value = values.get(threshold.metric)
            if value is None:
                continue

            breach = False
            message = ""

            if threshold.min_value is not None and value < threshold.min_value:
                breach = True
                message = (
                    f"{threshold.metric} = {value} is below minimum "
                    f"threshold {threshold.min_value} on {node_id}"
                )
            elif threshold.max_value is not None and value > threshold.max_value:
                breach = True
                message = (
                    f"{threshold.metric} = {value} exceeds maximum "
                    f"threshold {threshold.max_value} on {node_id}"
                )

            if breach:
                alert = EnvAlert(
                    node_id=node_id,
                    metric=threshold.metric,
                    value=value,
                    threshold_min=threshold.min_value,
                    threshold_max=threshold.max_value,
                    message=message,
                )
                alerts.append(alert)

                # Create alert in fleet alert system
                severity = ALERT_SEVERITY_MAP[AlertType.ENV_THRESHOLD_EXCEEDED].value
                self.db.create_alert(
                    node_id=node_id,
                    alert_type=AlertType.ENV_THRESHOLD_EXCEEDED.value,
                    severity=severity,
                    message=message,
                )

        return alerts

    # ── Query ──────────────────────────────────────────────────────

    def get_node_readings(
        self,
        node_id: str,
        since: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Get environmental readings for a node."""
        return self.db.get_env_readings(node_id, since=since, limit=limit)

    def get_fleet_summary(self) -> dict:
        """Get fleet-wide environmental summary."""
        return self.db.get_fleet_env_summary()

    def get_env_alerts(self, limit: int = 50) -> list[dict]:
        """Get recent environmental threshold alerts."""
        return self.db.get_env_alerts(limit=limit)

    # ── Threshold Management ───────────────────────────────────────

    def get_thresholds(self) -> list[dict]:
        """Get current threshold configuration."""
        return [t.model_dump() for t in self._thresholds]

    def update_thresholds(self, thresholds: list[dict]) -> list[dict]:
        """Replace threshold configuration. Returns new thresholds."""
        self._thresholds = [EnvThreshold(**t) for t in thresholds]
        return self.get_thresholds()

    def get_status(self) -> dict:
        """Get environmental telemetry manager status."""
        return {
            "enabled": True,
            "threshold_count": len(self._thresholds),
            "active_thresholds": sum(1 for t in self._thresholds if t.enabled),
        }
