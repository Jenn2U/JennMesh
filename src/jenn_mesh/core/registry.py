"""Device registry — high-level interface over the SQLite database."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.device import ConfigHash, DeviceRole, FirmwareInfo, MeshDevice
from jenn_mesh.models.fleet import (
    ALERT_SEVERITY_MAP,
    Alert,
    AlertType,
    FleetHealth,
)


class DeviceRegistry:
    """Fleet-wide device registry backed by SQLite."""

    def __init__(self, db: MeshDatabase, offline_threshold_seconds: int = 600):
        self.db = db
        self.offline_threshold = timedelta(seconds=offline_threshold_seconds)

    def register_device(self, device: MeshDevice) -> None:
        """Register or update a device in the fleet registry."""
        self.db.upsert_device(
            node_id=device.node_id,
            long_name=device.long_name or None,
            short_name=device.short_name or None,
            role=device.role.value,
            hw_model=device.firmware.hw_model,
            firmware_version=device.firmware.version,
            battery_level=device.battery_level,
            voltage=device.voltage,
            signal_snr=device.signal_snr,
            signal_rssi=device.signal_rssi,
            latitude=device.latitude,
            longitude=device.longitude,
            altitude=device.altitude,
            last_seen=device.last_seen.isoformat() if device.last_seen else None,
        )

    def get_device(self, node_id: str) -> Optional[MeshDevice]:
        """Retrieve a device by node ID."""
        row = self.db.get_device(node_id)
        if row is None:
            return None
        return self._row_to_device(row)

    def list_devices(self) -> list[MeshDevice]:
        """List all devices in the fleet."""
        rows = self.db.list_devices()
        return [self._row_to_device(r) for r in rows]

    def get_fleet_health(self) -> FleetHealth:
        """Compute aggregate fleet health stats."""
        devices = self.list_devices()
        active_alerts = self.db.get_active_alerts()

        online = sum(1 for d in devices if d.is_online)
        offline = sum(1 for d in devices if not d.is_online and d.last_seen is not None)
        critical = sum(1 for a in active_alerts if a["severity"] == "critical")

        return FleetHealth(
            total_devices=len(devices),
            online_count=online,
            offline_count=offline,
            degraded_count=len(devices) - online - offline,
            active_alerts=len(active_alerts),
            critical_alerts=critical,
            devices_needing_update=sum(1 for d in devices if d.firmware.needs_update),
            devices_with_drift=sum(1 for d in devices if d.config_hash and d.config_hash.drifted),
        )

    def check_offline_nodes(self) -> list[Alert]:
        """Detect nodes that haven't been seen within the offline threshold."""
        devices = self.list_devices()
        cutoff = datetime.utcnow() - self.offline_threshold
        new_alerts: list[Alert] = []

        for device in devices:
            if device.last_seen and device.last_seen < cutoff:
                if not self.db.has_active_alert(device.node_id, AlertType.NODE_OFFLINE.value):
                    alert = Alert(
                        node_id=device.node_id,
                        alert_type=AlertType.NODE_OFFLINE,
                        severity=ALERT_SEVERITY_MAP[AlertType.NODE_OFFLINE],
                        message=(
                            f"Node {device.display_name} offline since "
                            f"{device.last_seen.isoformat()}"
                        ),
                    )
                    self.db.create_alert(
                        node_id=alert.node_id,
                        alert_type=alert.alert_type.value,
                        severity=alert.severity.value,
                        message=alert.message,
                    )
                    new_alerts.append(alert)

        return new_alerts

    def check_low_battery(self, threshold_percent: int = 20) -> list[Alert]:
        """Detect nodes with battery below threshold."""
        devices = self.list_devices()
        new_alerts: list[Alert] = []

        for device in devices:
            if (
                device.battery_level is not None
                and device.battery_level <= threshold_percent
                and not self.db.has_active_alert(device.node_id, AlertType.LOW_BATTERY.value)
            ):
                alert = Alert(
                    node_id=device.node_id,
                    alert_type=AlertType.LOW_BATTERY,
                    severity=ALERT_SEVERITY_MAP[AlertType.LOW_BATTERY],
                    message=(
                        f"Node {device.display_name} battery low: " f"{device.battery_level}%"
                    ),
                )
                self.db.create_alert(
                    node_id=alert.node_id,
                    alert_type=alert.alert_type.value,
                    severity=alert.severity.value,
                    message=alert.message,
                )
                new_alerts.append(alert)

        return new_alerts

    def _row_to_device(self, row: dict) -> MeshDevice:
        """Convert a database row to a MeshDevice model."""
        now = datetime.utcnow()
        last_seen = datetime.fromisoformat(row["last_seen"]) if row.get("last_seen") else None
        is_online = last_seen is not None and (now - last_seen) < self.offline_threshold

        config_hash = None
        if row.get("config_hash"):
            config_hash = ConfigHash(
                hash=row["config_hash"],
                template_role=(
                    DeviceRole.from_meshtastic(row["template_role"])
                    if row.get("template_role")
                    else None
                ),
                template_hash=row.get("template_hash"),
            )

        return MeshDevice(
            node_id=row["node_id"],
            long_name=row.get("long_name", ""),
            short_name=row.get("short_name", ""),
            role=DeviceRole.from_meshtastic(row.get("role", "CLIENT")),
            firmware=FirmwareInfo(
                version=row.get("firmware_version", "unknown"),
                hw_model=row.get("hw_model", "unknown"),
            ),
            config_hash=config_hash,
            battery_level=row.get("battery_level"),
            voltage=row.get("voltage"),
            signal_snr=row.get("signal_snr"),
            signal_rssi=row.get("signal_rssi"),
            last_seen=last_seen,
            registered_at=(
                datetime.fromisoformat(row["registered_at"]) if row.get("registered_at") else None
            ),
            is_online=is_online,
            latitude=row.get("latitude"),
            longitude=row.get("longitude"),
            altitude=row.get("altitude"),
            associated_edge_node=row.get("associated_edge_node"),
        )
