"""Lost node locator — finds missing devices via last GPS + mesh proximity."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from jenn_mesh.db import MeshDatabase
from jenn_mesh.locator.tracker import PositionTracker
from jenn_mesh.models.location import (
    GPSPosition,
    LostNodeQuery,
    NearbyNode,
    ProximityResult,
)


class LostNodeFinder:
    """Locates missing mesh nodes or JennEdge devices via radio GPS data.

    Works by:
    1. Looking up the target's last known GPS position
    2. Finding active mesh nodes near that position
    3. Returning a confidence-rated location estimate
    """

    def __init__(self, db: MeshDatabase):
        self.db = db
        self.tracker = PositionTracker(db)

    def locate(self, query: LostNodeQuery) -> ProximityResult:
        """Execute a lost node location query.

        Args:
            query: LostNodeQuery with target_node_id, search_radius, max_age.

        Returns:
            ProximityResult with last known position, nearby nodes, confidence.
        """
        target_id = query.target_node_id

        # Check if target is a JennEdge device_id (mapped to radio node_id)
        radio_node_id = self._resolve_to_radio_id(target_id)

        # Get last known position
        last_position = self.tracker.get_latest_position(radio_node_id)
        position_age = self.tracker.get_position_age_hours(radio_node_id)

        # Determine confidence
        confidence = self._compute_confidence(last_position, position_age, query.max_age_hours)

        # Find nearby active nodes
        nearby_nodes: list[NearbyNode] = []
        if last_position:
            nearby = self.tracker.get_nearby_positions(
                latitude=last_position.latitude,
                longitude=last_position.longitude,
                radius_meters=query.search_radius_meters,
            )
            # Filter out the target itself
            for n in nearby:
                if n["node_id"] != radio_node_id:
                    nearby_nodes.append(
                        NearbyNode(
                            node_id=n["node_id"],
                            distance_meters=n["distance_meters"],
                            position=GPSPosition(
                                node_id=n["node_id"],
                                latitude=n["latitude"],
                                longitude=n["longitude"],
                            ),
                            is_online=self._is_node_online(n.get("last_seen")),
                        )
                    )

        # Get associated edge node mapping
        device = self.db.get_device(radio_node_id)
        associated_edge = device.get("associated_edge_node") if device else None

        return ProximityResult(
            target_node_id=target_id,
            last_known_position=last_position,
            position_age_hours=round(position_age, 2) if position_age else None,
            nearby_nodes=nearby_nodes,
            confidence=confidence,
            associated_edge_node=associated_edge,
        )

    def locate_edge_node(self, edge_device_id: str) -> ProximityResult:
        """Convenience method: locate a JennEdge device via its radio.

        Maps the edge device_id to its paired radio node_id, then locates.
        """
        return self.locate(
            LostNodeQuery(
                target_node_id=edge_device_id,
                search_radius_meters=10000,
                max_age_hours=168,  # 1 week
            )
        )

    def _resolve_to_radio_id(self, target_id: str) -> str:
        """Resolve a target ID to a radio node_id.

        If target_id is already a radio node_id (starts with !), return as-is.
        Otherwise, search for a radio associated with this edge device_id.
        """
        if target_id.startswith("!"):
            return target_id

        # Search for a device with this associated_edge_node
        devices = self.db.list_devices()
        for device in devices:
            if device.get("associated_edge_node") == target_id:
                return device["node_id"]

        # Fallback: use the target_id directly
        return target_id

    def _compute_confidence(
        self,
        position: Optional[GPSPosition],
        age_hours: Optional[float],
        max_age_hours: float,
    ) -> str:
        """Compute location confidence level."""
        if position is None:
            return "unknown"
        if age_hours is None:
            return "low"
        if age_hours <= 1:
            return "high"
        if age_hours <= 24:
            return "medium"
        if age_hours <= max_age_hours:
            return "low"
        return "stale"

    def _is_node_online(self, last_seen: Optional[str], threshold_minutes: int = 10) -> bool:
        """Check if a node is considered online based on last_seen timestamp."""
        if not last_seen:
            return False
        try:
            seen_dt = datetime.fromisoformat(last_seen)
            return (datetime.utcnow() - seen_dt) < timedelta(minutes=threshold_minutes)
        except (ValueError, TypeError):
            return False
