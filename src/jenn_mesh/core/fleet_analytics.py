"""Fleet analytics — time-series aggregation and trend computation.

Processes telemetry, alerts, and device data into aggregated trends
for the analytics dashboard.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from jenn_mesh.db import MeshDatabase

logger = logging.getLogger(__name__)


class FleetAnalytics:
    """Compute fleet-wide analytics and trends from existing DB data.

    Usage:
        analytics = FleetAnalytics(db)
        summary = analytics.get_dashboard_summary()
    """

    def __init__(self, db: MeshDatabase):
        self.db = db

    # ── Uptime Trends ────────────────────────────────────────────────

    def get_uptime_trends(self, node_id: str = None, days: int = 30) -> list[dict]:
        """Compute uptime percentage per node over last N days.

        Uses device last_seen and telemetry history to estimate uptime.
        Returns list of dicts with node_id, uptime_pct, online status.
        """
        devices = self.db.list_devices()
        if node_id:
            devices = [d for d in devices if d.get("node_id") == node_id]

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days)
        results = []

        for device in devices:
            nid = device.get("node_id", "")
            last_seen = device.get("last_seen")

            # Simple uptime heuristic: if device was seen within the last period,
            # estimate based on telemetry frequency
            samples = self.db.get_telemetry_history(nid, since=cutoff.isoformat())
            sample_count = len(samples)

            # Expected samples: one per 15 minutes = 96/day
            expected = days * 96
            uptime_pct = min(100.0, (sample_count / max(expected, 1)) * 100)

            results.append(
                {
                    "node_id": nid,
                    "long_name": device.get("long_name", nid),
                    "uptime_pct": round(uptime_pct, 1),
                    "sample_count": sample_count,
                    "last_seen": last_seen,
                    "is_online": device.get("is_online", False),
                }
            )

        return sorted(results, key=lambda x: x["uptime_pct"], reverse=True)

    # ── Battery Trends ───────────────────────────────────────────────

    def get_battery_trends(self, node_id: str = None, days: int = 30) -> list[dict]:
        """Battery level trend per node — detect declining capacity.

        Returns list with node_id, current_battery, trend (rising/falling/stable).
        """
        devices = self.db.list_devices()
        if node_id:
            devices = [d for d in devices if d.get("node_id") == node_id]

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        results = []

        for device in devices:
            nid = device.get("node_id", "")
            current_battery = device.get("battery_level")

            samples = self.db.get_telemetry_history(nid, since=cutoff)
            battery_values = [
                s.get("battery_level") for s in samples if s.get("battery_level") is not None
            ]

            trend = "unknown"
            if len(battery_values) >= 2:
                first_half = battery_values[: len(battery_values) // 2]
                second_half = battery_values[len(battery_values) // 2 :]
                avg_first = sum(first_half) / len(first_half)
                avg_second = sum(second_half) / len(second_half)
                diff = avg_second - avg_first
                if diff > 5:
                    trend = "rising"
                elif diff < -5:
                    trend = "falling"
                else:
                    trend = "stable"

            results.append(
                {
                    "node_id": nid,
                    "long_name": device.get("long_name", nid),
                    "current_battery": current_battery,
                    "trend": trend,
                    "sample_count": len(battery_values),
                }
            )

        return results

    # ── Alert Frequency ──────────────────────────────────────────────

    def get_alert_frequency(self, days: int = 30) -> dict:
        """Alert counts grouped by type and severity.

        Returns dict with by_type, by_severity, and total.
        """
        alerts = self.db.get_active_alerts()

        by_type: dict[str, int] = {}
        by_severity: dict[str, int] = {}

        for alert in alerts:
            atype = alert.get("alert_type", "unknown")
            severity = alert.get("severity", "unknown")
            by_type[atype] = by_type.get(atype, 0) + 1
            by_severity[severity] = by_severity.get(severity, 0) + 1

        return {
            "total": len(alerts),
            "by_type": by_type,
            "by_severity": by_severity,
        }

    # ── Message Volume ───────────────────────────────────────────────

    def get_message_volume(self, days: int = 7) -> list[dict]:
        """Telemetry message counts across all nodes.

        Returns list with node_id and message_count for the period.
        """
        devices = self.db.list_devices()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        results = []

        for device in devices:
            nid = device.get("node_id", "")
            samples = self.db.get_telemetry_history(nid, since=cutoff)
            results.append(
                {
                    "node_id": nid,
                    "long_name": device.get("long_name", nid),
                    "message_count": len(samples),
                }
            )

        return sorted(results, key=lambda x: x["message_count"], reverse=True)

    # ── Fleet Growth ─────────────────────────────────────────────────

    def get_fleet_growth(self) -> list[dict]:
        """Provisioning activity — number of devices by role.

        Returns list with role and count.
        """
        devices = self.db.list_devices()
        role_counts: dict[str, int] = {}
        for d in devices:
            role = d.get("role", "unknown")
            role_counts[role] = role_counts.get(role, 0) + 1

        return [
            {"role": role, "count": count}
            for role, count in sorted(role_counts.items(), key=lambda x: x[1], reverse=True)
        ]

    # ── Dashboard Summary ────────────────────────────────────────────

    def get_dashboard_summary(self) -> dict:
        """All-in-one summary for the analytics dashboard page.

        Combines key metrics from all analytics methods into a single response.
        """
        devices = self.db.list_devices()
        total_devices = len(devices)
        online_count = sum(1 for d in devices if d.get("is_online"))
        offline_count = total_devices - online_count

        alert_freq = self.get_alert_frequency()
        fleet_growth = self.get_fleet_growth()
        coverage_stats = self.db.get_coverage_stats()

        return {
            "fleet": {
                "total_devices": total_devices,
                "online": online_count,
                "offline": offline_count,
                "online_pct": round((online_count / max(total_devices, 1)) * 100, 1),
            },
            "alerts": {
                "total_active": alert_freq["total"],
                "by_severity": alert_freq["by_severity"],
            },
            "roles": fleet_growth,
            "coverage": {
                "total_samples": coverage_stats.get("total_samples", 0),
                "avg_rssi": coverage_stats.get("avg_rssi"),
            },
        }
