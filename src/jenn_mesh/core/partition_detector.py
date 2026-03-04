"""Mesh Network Partitioning Detection.

Detects when the mesh splits into multiple disconnected components,
creates NETWORK_PARTITION / PARTITION_RESOLVED alerts, stores topology
diffs in the ``partition_events`` table, and recommends relay placement
using GPS centroids of disconnected components.

Reuses ``TopologyManager.find_connected_components()`` for the heavy
graph algorithm work.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from jenn_mesh.db import MeshDatabase

logger = logging.getLogger(__name__)


def _compute_component_centroid(db: MeshDatabase, component_node_ids: list[str]) -> Optional[str]:
    """Compute geographic centroid of a component for relay recommendation.

    Returns a human-readable string like "lat=30.267, lon=-97.743" or
    None if no GPS data is available for the component's nodes.

    Trade-off: simple average (good for small areas) vs spherical mean
    (better for global scale).  Fleet deployments are typically local,
    so arithmetic mean is fine.
    """
    lats: list[float] = []
    lons: list[float] = []
    for node_id in component_node_ids:
        device = db.get_device(node_id)
        if device is None:
            continue
        lat = device.get("latitude")
        lon = device.get("longitude")
        if lat is not None and lon is not None:
            try:
                lat_f, lon_f = float(lat), float(lon)
                # Skip zero coordinates (GPS not yet acquired)
                if lat_f == 0.0 and lon_f == 0.0:
                    continue
                lats.append(lat_f)
                lons.append(lon_f)
            except (ValueError, TypeError):
                continue

    if not lats:
        return None

    avg_lat = sum(lats) / len(lats)
    avg_lon = sum(lons) / len(lons)
    return f"lat={avg_lat:.6f}, lon={avg_lon:.6f}"


class PartitionDetector:
    """Detect and track network partition/merge events.

    Compares the current connected-component count against the last
    recorded partition event.  When the count changes, records a new
    event and creates/resolves alerts.

    Constructor Args:
        db: MeshDatabase instance.
    """

    def __init__(self, db: MeshDatabase) -> None:
        self.db = db

    # ── Public API ────────────────────────────────────────────────────

    def check_partitions(self) -> dict:
        """Run a partition check — compare current vs previous topology.

        Returns a summary dict suitable for watchdog audit logging:
            component_count, previous_count, event_type (if changed),
            new_alerts, auto_resolved, relay_recommendations.
        """
        from jenn_mesh.core.topology import TopologyManager
        from jenn_mesh.models.fleet import ALERT_SEVERITY_MAP, AlertType

        topo = TopologyManager(self.db)
        components = topo.find_connected_components()
        current_count = len(components)

        # Get previous state from the most recent partition event
        latest = self.db.get_latest_partition_event()
        previous_count = latest["component_count"] if latest else 1

        result: dict = {
            "component_count": current_count,
            "previous_count": previous_count,
            "event_type": None,
            "new_alerts": 0,
            "auto_resolved": 0,
            "relay_recommendations": [],
        }

        if current_count == previous_count:
            # No change — nothing to record
            return result

        if current_count > previous_count:
            # Partition detected — network split into more components
            result["event_type"] = "partition_detected"

            # Generate relay recommendations for bridging gaps
            recommendations = self._recommend_relays(components)
            result["relay_recommendations"] = recommendations

            # Store event
            self.db.create_partition_event(
                event_type="partition_detected",
                component_count=current_count,
                components_json=json.dumps([sorted(c) for c in components], default=str),
                previous_component_count=previous_count,
                relay_recommendation="; ".join(recommendations) if recommendations else None,
            )

            # Create NETWORK_PARTITION alerts — one per device in minority components
            # (the largest component is "primary", all others are "partitioned off")
            sorted_components = sorted(components, key=len, reverse=True)
            new_alerts = 0
            for comp in sorted_components[1:]:  # skip largest
                for node_id in comp:
                    if not self.db.has_active_alert(node_id, AlertType.NETWORK_PARTITION.value):
                        severity = ALERT_SEVERITY_MAP[AlertType.NETWORK_PARTITION].value
                        msg = (
                            f"Node in partition ({len(comp)} nodes) — "
                            f"disconnected from main mesh ({len(sorted_components[0])} nodes)"
                        )
                        self.db.create_alert(
                            node_id,
                            AlertType.NETWORK_PARTITION.value,
                            severity,
                            msg,
                        )
                        new_alerts += 1
            result["new_alerts"] = new_alerts

        else:
            # Partition resolved — components merged back
            result["event_type"] = "partition_resolved"

            # Store event
            self.db.create_partition_event(
                event_type="partition_resolved",
                component_count=current_count,
                components_json=json.dumps([sorted(c) for c in components], default=str),
                previous_component_count=previous_count,
            )

            # Resolve the most recent partition event
            if latest and latest.get("id"):
                self.db.resolve_partition_event(latest["id"])

            # Auto-resolve NETWORK_PARTITION alerts for nodes now in main component
            active_alerts = self.db.get_active_alerts()
            resolved = 0
            # All nodes in current topology are reachable (single component)
            all_current_nodes = set()
            for comp in components:
                all_current_nodes.update(comp)

            for alert in active_alerts:
                if alert["alert_type"] == AlertType.NETWORK_PARTITION.value:
                    self.db.resolve_alert(alert["id"])
                    resolved += 1
            result["auto_resolved"] = resolved

            # Also create a PARTITION_RESOLVED info alert for visibility
            severity = ALERT_SEVERITY_MAP[AlertType.PARTITION_RESOLVED].value
            self.db.create_alert(
                "mesh",
                AlertType.PARTITION_RESOLVED.value,
                severity,
                f"Network partition resolved — {current_count} component(s), was {previous_count}",
            )
            result["new_alerts"] = 1

        return result

    def get_partition_status(self) -> dict:
        """Get current partition status for the API.

        Returns:
            dict with is_partitioned, component_count, components,
            latest_event, relay_recommendations.
        """
        from jenn_mesh.core.topology import TopologyManager

        topo = TopologyManager(self.db)
        components = topo.find_connected_components()
        current_count = len(components)

        latest = self.db.get_latest_partition_event()
        recommendations = self._recommend_relays(components) if current_count > 1 else []

        return {
            "is_partitioned": current_count > 1,
            "component_count": current_count,
            "components": [sorted(c) for c in components],
            "latest_event": latest,
            "relay_recommendations": recommendations,
        }

    # ── Internal ──────────────────────────────────────────────────────

    def _recommend_relays(self, components: list[list[str]]) -> list[str]:
        """Suggest relay placement between disconnected components.

        For each pair of adjacent components, computes centroids and
        suggests placing a relay between them.
        """
        if len(components) <= 1:
            return []

        recommendations: list[str] = []
        sorted_comps = sorted(components, key=len, reverse=True)

        for i in range(1, len(sorted_comps)):
            centroid_a = _compute_component_centroid(self.db, sorted_comps[0])
            centroid_b = _compute_component_centroid(self.db, sorted_comps[i])

            if centroid_a and centroid_b:
                recommendations.append(
                    f"Place relay between main ({centroid_a}) and " f"partition #{i} ({centroid_b})"
                )
            elif centroid_a:
                recommendations.append(
                    f"Place relay near main mesh ({centroid_a}) to reach "
                    f"partition #{i} ({len(sorted_comps[i])} nodes, no GPS)"
                )
            else:
                recommendations.append(
                    f"Place relay to bridge main mesh ({len(sorted_comps[0])} nodes) "
                    f"and partition #{i} ({len(sorted_comps[i])} nodes)"
                )

        return recommendations
