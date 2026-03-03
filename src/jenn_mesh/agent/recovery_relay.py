"""Recovery relay — gateway-agent-side: relay MQTT commands to mesh and ACKs back.

Runs on gateway agents that have BOTH internet (MQTT) and radio (LoRa).
Subscribes to the recovery command MQTT topic, sends commands over mesh
to target agents, and forwards RECOVER_ACK texts back to MQTT.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from jenn_mesh.models.recovery import (
    ADMIN_CHANNEL_INDEX,
    RECOVERY_ACK_PREFIX,
    parse_recovery_ack,
)

logger = logging.getLogger(__name__)

# MQTT topics (must match recovery_manager.py constants)
RECOVERY_COMMAND_TOPIC = "jenn/mesh/command/recovery"
RECOVERY_ACK_TOPIC = "jenn/mesh/command/recovery/ack"


class RecoveryRelay:
    """Relays recovery commands from MQTT to mesh and ACKs back.

    Lifecycle:
        1. Gateway agent starts relay with start()
        2. Dashboard publishes JSON command to RECOVERY_COMMAND_TOPIC via MQTT
        3. Relay receives JSON, extracts mesh_text + target, sends via RadioBridge
        4. Target agent executes, sends RECOVER_ACK back over mesh
        5. Relay's handle_mesh_text() catches the ACK, publishes to RECOVERY_ACK_TOPIC
    """

    def __init__(self, bridge: object, mqtt_client: Optional[Any] = None):
        """Initialize the recovery relay.

        Args:
            bridge: RadioBridge instance with send_text() method.
            mqtt_client: Paho MQTT client for subscribing to commands
                        and publishing ACKs. If None, relay is inactive.
        """
        self._bridge = bridge
        self._mqtt_client = mqtt_client
        self._running = False

    def start(self) -> None:
        """Subscribe to the recovery command MQTT topic."""
        if self._mqtt_client is None:
            logger.warning("RecoveryRelay: no MQTT client — relay inactive")
            return

        self._mqtt_client.subscribe(RECOVERY_COMMAND_TOPIC)
        self._mqtt_client.message_callback_add(RECOVERY_COMMAND_TOPIC, self._on_command)
        self._running = True
        logger.info("RecoveryRelay started — subscribed to %s", RECOVERY_COMMAND_TOPIC)

    def stop(self) -> None:
        """Unsubscribe from the recovery command MQTT topic."""
        self._running = False
        if self._mqtt_client is not None:
            try:
                self._mqtt_client.unsubscribe(RECOVERY_COMMAND_TOPIC)
                self._mqtt_client.message_callback_remove(RECOVERY_COMMAND_TOPIC)
            except Exception as e:
                logger.warning("Error stopping recovery relay: %s", e)
        logger.info("RecoveryRelay stopped")

    @property
    def is_running(self) -> bool:
        """Whether the relay is currently active."""
        return self._running

    def _on_command(self, client: Any, userdata: Any, msg: Any) -> None:
        """Handle an incoming MQTT recovery command.

        Expected JSON payload:
            {
                "command_id": 42,
                "target_node_id": "!a1b2c3d4",
                "command_type": "reboot",
                "args": "",
                "nonce": "abcd1234",
                "mesh_text": "RECOVER|42|reboot||abcd1234|1709395200",
                "channel_index": 1
            }
        """
        try:
            payload = json.loads(msg.payload)
        except (json.JSONDecodeError, AttributeError) as e:
            logger.error("RecoveryRelay: invalid JSON in MQTT message: %s", e)
            return

        mesh_text = payload.get("mesh_text")
        target_node_id = payload.get("target_node_id")
        command_id = payload.get("command_id")
        channel_index = payload.get("channel_index", ADMIN_CHANNEL_INDEX)

        if not mesh_text:
            logger.error("RecoveryRelay: missing mesh_text in payload")
            return

        logger.info(
            "RecoveryRelay: relaying command %s to %s on channel %d",
            command_id,
            target_node_id,
            channel_index,
        )

        # Send via RadioBridge — targeted to specific node on ADMIN channel
        try:
            sent = self._bridge.send_text(
                mesh_text,
                destination=target_node_id,
                channel_index=channel_index,
            )
            if sent:
                logger.info("RecoveryRelay: command %s sent to mesh", command_id)
                # Publish relay ACK to MQTT (gateway confirms it sent the command)
                self._publish_relay_ack(command_id, "relayed", target_node_id)
            else:
                logger.error(
                    "RecoveryRelay: bridge.send_text returned False for cmd %s", command_id
                )
                self._publish_relay_ack(command_id, "relay_failed", target_node_id)
        except Exception as e:
            logger.error("RecoveryRelay: error sending to mesh: %s", e)
            self._publish_relay_ack(command_id, "relay_error", target_node_id)

    def handle_mesh_text(self, text: str, from_id: str = "") -> bool:
        """Check if incoming mesh text is a RECOVER_ACK and forward to MQTT.

        Called by the agent's PACKET_TEXT callback for all incoming text.

        Args:
            text: Raw text received from mesh.
            from_id: Sender's Meshtastic node ID.

        Returns:
            True if the text was a RECOVER_ACK (consumed), False otherwise.
        """
        if not text.startswith(RECOVERY_ACK_PREFIX):
            return False

        parsed = parse_recovery_ack(text)
        if parsed is None:
            logger.warning("RecoveryRelay: malformed RECOVER_ACK from %s", from_id)
            return True  # Was an ACK attempt, don't pass to other handlers

        cmd_id = parsed["cmd_id"]
        status = parsed["status"]
        message = parsed["message"]

        logger.info(
            "RecoveryRelay: ACK received from %s: cmd_id=%d status=%s msg='%s'",
            from_id,
            cmd_id,
            status,
            message,
        )

        # Forward ACK to MQTT for the dashboard
        self._publish_target_ack(cmd_id, status, message, from_id)
        return True

    def _publish_relay_ack(self, command_id: Any, status: str, target_node_id: str) -> None:
        """Publish a relay-level ACK (gateway confirms it attempted mesh send)."""
        if self._mqtt_client is None:
            return

        payload = json.dumps(
            {
                "command_id": command_id,
                "relay_status": status,
                "target_node_id": target_node_id,
                "source": "gateway_relay",
            }
        )
        try:
            self._mqtt_client.publish(RECOVERY_ACK_TOPIC, payload)
        except Exception as e:
            logger.error("RecoveryRelay: failed to publish relay ACK: %s", e)

    def _publish_target_ack(self, cmd_id: int, status: str, message: str, from_id: str) -> None:
        """Publish a target-level ACK (forwarding target agent's RECOVER_ACK)."""
        if self._mqtt_client is None:
            return

        payload = json.dumps(
            {
                "command_id": cmd_id,
                "status": status,
                "message": message,
                "from_node_id": from_id,
                "source": "target_agent",
            }
        )
        try:
            self._mqtt_client.publish(RECOVERY_ACK_TOPIC, payload)
        except Exception as e:
            logger.error("RecoveryRelay: failed to publish target ACK: %s", e)
