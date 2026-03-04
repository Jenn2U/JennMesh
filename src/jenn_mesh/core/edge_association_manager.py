"""Edge Association Manager — bidirectional mapping between JennEdge devices and mesh radios.

Maintains a cross-reference so that:
- When JennEdge reports a device, JennMesh knows which radio is co-located
- "Edge node X is offline — but its radio is still transmitting from GPS coords Y"
- JennEdge health page can show "Radio: online, signal good, battery 78%"

Usage::

    assoc_mgr = EdgeAssociationManager(db=app.state.db)
    assoc_mgr.create_association(edge_device_id="edge-001", node_id="!2a3b4c")
    status = assoc_mgr.get_combined_status("edge-001")
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.edge_association import (
    AssociationStatus,
    EdgeAssociation,
    EdgeRadioStatus,
)

logger = logging.getLogger(__name__)


class EdgeAssociationManager:
    """Manages JennEdge device ↔ mesh radio associations."""

    def __init__(self, db: MeshDatabase):
        self._db = db

    def create_association(
        self,
        edge_device_id: str,
        node_id: str,
        edge_hostname: str | None = None,
        edge_ip: str | None = None,
        association_type: str = "co-located",
    ) -> EdgeAssociation:
        """Create a new edge-to-radio association.

        Args:
            edge_device_id: JennEdge device identifier.
            node_id: Mesh radio node_id.
            edge_hostname: JennEdge device hostname.
            edge_ip: JennEdge device IP address.
            association_type: How they're associated (co-located, usb-connected, bluetooth).

        Returns:
            EdgeAssociation model with DB-assigned ID.

        Raises:
            ValueError: If edge_device_id or node_id is empty.
            ValueError: If edge_device_id already has an association.
        """
        if not edge_device_id or not edge_device_id.strip():
            raise ValueError("edge_device_id is required")
        if not node_id or not node_id.strip():
            raise ValueError("node_id is required")

        # Check for existing
        existing = self._db.get_edge_association_by_edge(edge_device_id)
        if existing:
            raise ValueError(
                f"Edge device '{edge_device_id}' already has an association "
                f"with node '{existing['node_id']}'. "
                f"Update or delete the existing association first."
            )

        assoc_id = self._db.create_edge_association(
            edge_device_id=edge_device_id,
            node_id=node_id,
            edge_hostname=edge_hostname,
            edge_ip=edge_ip,
            association_type=association_type,
        )

        logger.info(
            "Edge association created: %s → %s (%s)",
            edge_device_id, node_id, association_type,
        )

        return EdgeAssociation(
            id=assoc_id,
            edge_device_id=edge_device_id,
            node_id=node_id,
            edge_hostname=edge_hostname,
            edge_ip=edge_ip,
            association_type=association_type,
        )

    def get_by_edge(self, edge_device_id: str) -> Optional[dict]:
        """Get association for a JennEdge device."""
        return self._db.get_edge_association_by_edge(edge_device_id)

    def get_by_node(self, node_id: str) -> Optional[dict]:
        """Get association for a mesh radio node."""
        return self._db.get_edge_association_by_node(node_id)

    def list_associations(
        self, status: str | None = None
    ) -> list[dict]:
        """List all edge-radio associations."""
        return self._db.list_edge_associations(status=status)

    def update_association(
        self, edge_device_id: str, **kwargs: object
    ) -> bool:
        """Update association fields."""
        return self._db.update_edge_association(edge_device_id, **kwargs)

    def delete_association(self, edge_device_id: str) -> bool:
        """Delete an edge-radio association."""
        return self._db.delete_edge_association(edge_device_id)

    def get_combined_status(self, edge_device_id: str) -> Optional[EdgeRadioStatus]:
        """Get combined edge + radio status for cross-reference display.

        This is the key query for the cross-reference feature:
        "Edge node X is offline — but its radio is still transmitting"
        """
        row = self._db.get_edge_radio_status(edge_device_id)
        if row is None:
            return None

        return EdgeRadioStatus(
            edge_device_id=row["edge_device_id"],
            edge_hostname=row.get("edge_hostname"),
            node_id=row["node_id"],
            radio_online=row.get("mesh_status") == "reachable",
            radio_battery=row.get("battery_level"),
            radio_signal_rssi=row.get("signal_rssi"),
            radio_signal_snr=row.get("signal_snr"),
            radio_latitude=row.get("latitude"),
            radio_longitude=row.get("longitude"),
            radio_last_seen=row.get("last_seen"),
            mesh_status=row.get("mesh_status", "unknown"),
            association_status=AssociationStatus(
                row.get("status", "active")
            ),
        )

    def update_stale_associations(self) -> int:
        """Mark associations as stale when radio hasn't been seen recently.

        Called periodically from watchdog. Returns number updated.
        """
        associations = self._db.list_edge_associations(status="active")
        stale_count = 0
        for assoc in associations:
            # Check if the radio's last_seen is stale (> 1 hour)
            with self._db.connection() as conn:
                row = conn.execute(
                    """SELECT last_seen FROM devices WHERE node_id = ?""",
                    (assoc["node_id"],),
                ).fetchone()

            if row is None:
                # Radio not in device registry — mark stale
                self._db.update_edge_association(
                    assoc["edge_device_id"], status="stale"
                )
                stale_count += 1
                continue

            if row["last_seen"]:
                try:
                    last_seen = datetime.fromisoformat(row["last_seen"])
                    age_hours = (
                        datetime.utcnow() - last_seen
                    ).total_seconds() / 3600
                    if age_hours > 1:
                        self._db.update_edge_association(
                            assoc["edge_device_id"], status="stale"
                        )
                        stale_count += 1
                except (ValueError, TypeError):
                    pass

        if stale_count:
            logger.info("Marked %d edge associations as stale", stale_count)
        return stale_count
