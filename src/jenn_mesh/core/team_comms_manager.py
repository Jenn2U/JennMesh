"""Team Communication Manager — text messaging through the mesh for field teams.

Follows the EmergencyBroadcastManager pattern:
  1. Dashboard API calls send_message() with channel and recipient
  2. Manager validates, stores in DB, publishes MQTT command to agent
  3. Agent receives command, sends via RadioBridge on Channel 2
  4. Agent publishes ACK → mark_sent()
  5. Mesh-relayed text echoes back through MQTT subscriber → mark_delivered()

Usage::

    comms = TeamCommsManager(db=app.state.db, mqtt_client=client)
    msg = comms.send_message(channel="team", sender="operator1", message="Rally at CP2")
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.team_comms import (
    MAX_TEAM_MESSAGE_LENGTH,
    TEAM_CHANNEL_INDEX,
    MessageChannel,
    MessageStatus,
    TeamMessage,
)

logger = logging.getLogger(__name__)

# MQTT topic for team comms commands (dashboard → agent)
TEAM_COMMS_COMMAND_TOPIC = "jenn/mesh/command/team-comms"
TEAM_COMMS_ACK_TOPIC = "jenn/mesh/command/team-comms/ack"


class TeamCommsManager:
    """Coordinates team messaging: validation → DB → MQTT command → status tracking."""

    def __init__(
        self,
        db: MeshDatabase,
        mqtt_client: Optional[Any] = None,
    ):
        self._db = db
        self._mqtt_client = mqtt_client

    def send_message(
        self,
        channel: str = "broadcast",
        sender: str = "dashboard",
        message: str = "",
        recipient: str | None = None,
        mesh_channel_index: int = TEAM_CHANNEL_INDEX,
    ) -> TeamMessage:
        """Send a team communication message.

        Args:
            channel: Message channel — broadcast, team, or direct.
            sender: Who sent the message (operator ID or "dashboard").
            message: Message text (max 220 chars for LoRa).
            recipient: Target node_id (direct) or team name (team). None for broadcast.
            mesh_channel_index: Meshtastic channel index (default 2).

        Returns:
            TeamMessage with DB-assigned ID and status.

        Raises:
            ValueError: If message is empty or too long, or channel is invalid.
        """
        # Validate channel
        try:
            msg_channel = MessageChannel(channel)
        except ValueError:
            raise ValueError(
                f"Invalid channel '{channel}'. "
                f"Must be one of: {[c.value for c in MessageChannel]}"
            )

        # Validate message
        if not message or not message.strip():
            raise ValueError("Message cannot be empty")
        message = message.strip()
        if len(message) > MAX_TEAM_MESSAGE_LENGTH:
            raise ValueError(
                f"Message exceeds {MAX_TEAM_MESSAGE_LENGTH} characters "
                f"(got {len(message)})"
            )

        # Validate direct messages have a recipient
        if msg_channel == MessageChannel.DIRECT and not recipient:
            raise ValueError("Direct messages require a recipient node_id")

        # Store in DB
        msg_id = self._db.create_team_message(
            channel=msg_channel.value,
            sender=sender,
            message=message,
            recipient=recipient,
            mesh_channel_index=mesh_channel_index,
        )

        msg = TeamMessage(
            id=msg_id,
            channel=msg_channel,
            sender=sender,
            recipient=recipient,
            message=message,
            mesh_channel_index=mesh_channel_index,
            status=MessageStatus.PENDING,
        )

        # Publish MQTT command if client available
        if self._mqtt_client is not None:
            payload = {
                "id": msg_id,
                "channel": msg_channel.value,
                "sender": sender,
                "recipient": recipient,
                "message": message,
                "wire_format": msg.wire_format,
                "mesh_channel_index": mesh_channel_index,
            }
            try:
                self._mqtt_client.publish(
                    TEAM_COMMS_COMMAND_TOPIC,
                    json.dumps(payload),
                )
                self._db.update_team_message_status(msg_id, MessageStatus.SENDING.value)
                msg.status = MessageStatus.SENDING
                logger.info(
                    "Team message %d published to MQTT: [%s] %s",
                    msg_id, msg_channel.value, message[:50],
                )
            except Exception:
                logger.exception("Failed to publish team message %d to MQTT", msg_id)
                self._db.update_team_message_status(msg_id, MessageStatus.FAILED.value)
                msg.status = MessageStatus.FAILED

        return msg

    def mark_sent(self, msg_id: int) -> bool:
        """Mark message as sent by agent (ACK received from radio bridge)."""
        now = datetime.now(timezone.utc).isoformat()
        return self._db.update_team_message_status(
            msg_id, MessageStatus.SENT.value, sent_at=now
        )

    def mark_delivered(self, msg_id: int) -> bool:
        """Mark message as delivered (mesh echo received)."""
        now = datetime.now(timezone.utc).isoformat()
        return self._db.update_team_message_status(
            msg_id, MessageStatus.DELIVERED.value, delivered_at=now
        )

    def get_message(self, msg_id: int) -> Optional[dict]:
        """Get a single message by ID."""
        return self._db.get_team_message(msg_id)

    def list_messages(
        self,
        channel: str | None = None,
        limit: int = 50,
        hours: int | None = None,
    ) -> list[dict]:
        """List messages with optional filters."""
        return self._db.list_team_messages(
            channel=channel, limit=limit, hours=hours
        )

    def find_message_for_mesh_text(self, text: str) -> Optional[dict]:
        """Try to match a mesh echo text to a pending/sent team message.

        The MQTT subscriber calls this when it detects [TEAM:...] prefix
        in a received text message.
        """
        if not text.startswith("[TEAM:"):
            return None

        # Extract message body after the prefix
        try:
            bracket_end = text.index("]")
            body = text[bracket_end + 1:].strip()
        except ValueError:
            return None

        # Strip @recipient prefix for direct messages
        if body.startswith("@"):
            parts = body.split(" ", 1)
            body = parts[1] if len(parts) > 1 else ""

        # Search recent pending/sent messages for a match
        recent = self._db.list_team_messages(limit=20)
        for msg in recent:
            if msg["status"] in ("pending", "sending", "sent"):
                if msg["message"].strip() == body.strip():
                    return msg
        return None
