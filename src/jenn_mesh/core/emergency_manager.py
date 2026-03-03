"""Emergency broadcast manager — validate, store, and coordinate emergency alerts."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.emergency import (
    EMERGENCY_CHANNEL_INDEX,
    BroadcastStatus,
    EmergencyBroadcast,
    EmergencyType,
)

logger = logging.getLogger(__name__)

# MQTT topic for emergency commands (dashboard → agent)
EMERGENCY_COMMAND_TOPIC = "jenn/mesh/command/emergency"
EMERGENCY_ACK_TOPIC = "jenn/mesh/command/emergency/ack"


class EmergencyBroadcastManager:
    """Coordinates emergency broadcasts: validation → DB → MQTT command → status tracking.

    Lifecycle:
        1. Dashboard API calls create_broadcast() with operator confirmation
        2. Manager validates, stores in DB, publishes MQTT command to agent
        3. Agent receives command, sends via RadioBridge on Channel 3
        4. Agent publishes ACK → mark_sent()
        5. Mesh-relayed text echoes back through MQTT subscriber → mark_delivered()
    """

    def __init__(
        self,
        db: MeshDatabase,
        mqtt_client: Optional[Any] = None,
    ):
        """Initialize the emergency broadcast manager.

        Args:
            db: MeshDatabase for storing broadcast audit trail.
            mqtt_client: Optional MQTT client for publishing commands to agent.
                        If None, broadcasts are stored in DB but not sent.
        """
        self._db = db
        self._mqtt_client = mqtt_client

    def create_broadcast(
        self,
        broadcast_type: str,
        message: str,
        sender: str = "dashboard",
        confirmed: bool = False,
        channel_index: int = EMERGENCY_CHANNEL_INDEX,
    ) -> EmergencyBroadcast:
        """Create and initiate an emergency broadcast.

        Args:
            broadcast_type: One of EmergencyType values.
            message: Human-readable emergency message.
            sender: Who initiated (operator ID or "dashboard").
            confirmed: Must be True — safety gate for irreversible action.
            channel_index: Meshtastic channel (default: 3 = Emergency).

        Returns:
            EmergencyBroadcast model with DB-assigned ID and status.

        Raises:
            ValueError: If confirmed is False or type/message is invalid.
        """
        if not confirmed:
            raise ValueError(
                "Emergency broadcasts require explicit confirmation. "
                "Set confirmed=True to proceed."
            )

        # Validate emergency type
        try:
            etype = EmergencyType(broadcast_type)
        except ValueError:
            valid_types = [t.value for t in EmergencyType]
            raise ValueError(
                f"Invalid emergency type '{broadcast_type}'. "
                f"Valid types: {', '.join(valid_types)}"
            )

        # Validate via Pydantic model (message length, whitespace, etc.)
        broadcast = EmergencyBroadcast(
            broadcast_type=etype,
            message=message,
            sender=sender,
            channel_index=channel_index,
            confirmed=True,
            status=BroadcastStatus.PENDING,
        )

        # Store in DB
        broadcast_id = self._db.create_emergency_broadcast(
            broadcast_type=etype.value,
            message=broadcast.message,
            sender=sender,
            channel_index=channel_index,
        )
        broadcast.id = broadcast_id

        logger.info(
            "Emergency broadcast created: id=%d type=%s sender=%s",
            broadcast_id,
            etype.value,
            sender,
        )

        # Publish MQTT command to agent (if client available)
        self._publish_command(broadcast)

        return broadcast

    def _publish_command(self, broadcast: EmergencyBroadcast) -> None:
        """Publish an MQTT command for the agent to send over radio."""
        if self._mqtt_client is None:
            logger.warning(
                "No MQTT client — broadcast %d stored in DB but not sent to agent",
                broadcast.id,
            )
            return

        mesh_text = EmergencyBroadcast.format_mesh_text(broadcast.broadcast_type, broadcast.message)
        payload = json.dumps(
            {
                "broadcast_id": broadcast.id,
                "type": broadcast.broadcast_type.value,
                "message": broadcast.message,
                "mesh_text": mesh_text,
                "channel_index": broadcast.channel_index,
            }
        )

        try:
            self._mqtt_client.publish(EMERGENCY_COMMAND_TOPIC, payload)
            logger.info("Emergency command published to MQTT: broadcast_id=%d", broadcast.id)
        except Exception as e:
            logger.error("Failed to publish emergency command: %s", e)
            self._db.update_broadcast_status(broadcast.id, BroadcastStatus.FAILED.value)

    def mark_sent(self, broadcast_id: int) -> None:
        """Mark a broadcast as sent (agent ACK received)."""
        now = datetime.utcnow().isoformat()
        self._db.update_broadcast_status(
            broadcast_id,
            BroadcastStatus.SENT.value,
            sent_at=now,
        )
        logger.info("Broadcast %d marked as sent", broadcast_id)

    def mark_delivered(self, broadcast_id: int) -> None:
        """Mark a broadcast as delivered (mesh echo received)."""
        now = datetime.utcnow().isoformat()
        self._db.update_broadcast_status(
            broadcast_id,
            BroadcastStatus.DELIVERED.value,
            delivered_at=now,
            mesh_received=True,
        )
        logger.info("Broadcast %d confirmed delivered via mesh echo", broadcast_id)

    def mark_failed(self, broadcast_id: int) -> None:
        """Mark a broadcast as failed."""
        self._db.update_broadcast_status(broadcast_id, BroadcastStatus.FAILED.value)
        logger.warning("Broadcast %d marked as failed", broadcast_id)

    def get_broadcast(self, broadcast_id: int) -> Optional[dict]:
        """Get a single broadcast by ID."""
        return self._db.get_broadcast(broadcast_id)

    def list_broadcasts(self, limit: int = 50) -> list[dict]:
        """List broadcast history, most recent first."""
        return self._db.list_broadcasts(limit=limit)

    def get_fleet_emergency_status(self) -> dict:
        """Get fleet-level emergency broadcast status summary.

        Returns dict with:
            - active_broadcasts: count of non-terminal broadcasts
            - last_broadcast_time: ISO timestamp of most recent broadcast
            - recent_broadcasts: broadcasts in the last 60 minutes
        """
        all_broadcasts = self._db.list_broadcasts(limit=100)
        recent = self._db.get_recent_broadcasts(minutes=60)

        active_statuses = {
            BroadcastStatus.PENDING.value,
            BroadcastStatus.SENDING.value,
            BroadcastStatus.SENT.value,
        }
        active_count = sum(1 for b in all_broadcasts if b["status"] in active_statuses)

        last_time = None
        if all_broadcasts:
            last_time = all_broadcasts[0].get("created_at")

        return {
            "active_broadcasts": active_count,
            "last_broadcast_time": last_time,
            "recent_count": len(recent),
            "recent_broadcasts": recent,
        }

    def find_broadcast_for_mesh_text(self, emergency_type: str) -> Optional[dict]:
        """Find a pending/sent broadcast matching the emergency type from a mesh echo.

        When an emergency text propagates back through the mesh and arrives
        at the MQTT subscriber, we need to match it to the original broadcast.

        Args:
            emergency_type: Lowercase type string parsed from [EMERGENCY:{TYPE}].

        Returns:
            Matching broadcast dict, or None.
        """
        recent = self._db.get_recent_broadcasts(minutes=60)
        matchable_statuses = {
            BroadcastStatus.PENDING.value,
            BroadcastStatus.SENDING.value,
            BroadcastStatus.SENT.value,
        }
        for broadcast in recent:
            if (
                broadcast["broadcast_type"] == emergency_type
                and broadcast["status"] in matchable_statuses
            ):
                return broadcast
        return None
