"""Recovery command manager — validate, store, publish, and track mesh recovery commands."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.recovery import (
    ADMIN_CHANNEL_INDEX,
    ALLOWED_COMMANDS,
    ALLOWED_SERVICES,
    MAX_COMMAND_AGE_SECONDS,
    RATE_LIMIT_SECONDS,
    RecoveryCommand,
    RecoveryCommandStatus,
    RecoveryCommandType,
    format_recovery_text,
    generate_nonce,
)

logger = logging.getLogger(__name__)

# MQTT topics for recovery commands (dashboard ↔ gateway agent)
RECOVERY_COMMAND_TOPIC = "jenn/mesh/command/recovery"
RECOVERY_ACK_TOPIC = "jenn/mesh/command/recovery/ack"


class RecoveryManager:
    """Coordinates mesh-based recovery commands: validation → DB → MQTT → status tracking.

    Lifecycle:
        1. Dashboard API calls send_command() with operator confirmation
        2. Manager validates command type, rate limit, stores in DB
        3. Publishes MQTT command payload → gateway agent
        4. Gateway agent relays over mesh (Channel 1 / ADMIN) → mark_sent()
        5. Target agent executes, sends RECOVER_ACK → mark_completed() / mark_failed()
        6. Stale commands expire via expire_stale_commands()
    """

    def __init__(
        self,
        db: MeshDatabase,
        mqtt_client: Optional[Any] = None,
    ):
        """Initialize the recovery manager.

        Args:
            db: MeshDatabase for storing recovery command audit trail.
            mqtt_client: Optional MQTT client for publishing commands to gateway agent.
                        If None, commands are stored in DB but not sent.
        """
        self._db = db
        self._mqtt_client = mqtt_client

    def send_command(
        self,
        target_node_id: str,
        command_type: str,
        args: str = "",
        sender: str = "dashboard",
        confirmed: bool = False,
    ) -> RecoveryCommand:
        """Create and send a recovery command to a target edge node.

        Args:
            target_node_id: Meshtastic node ID (e.g., '!a1b2c3d4').
            command_type: One of ALLOWED_COMMANDS.
            args: Command-specific args (e.g., service name for restart_service).
            sender: Who initiated (operator ID or "dashboard").
            confirmed: Must be True — safety gate for destructive commands.

        Returns:
            RecoveryCommand model with DB-assigned ID and pending status.

        Raises:
            ValueError: If confirmed is False, command type is invalid, or
                       args are invalid for the command type.
            RuntimeError: If rate-limited (duplicate command to same node too soon).
        """
        # Safety gate: require explicit confirmation
        if not confirmed:
            raise ValueError(
                "Recovery commands require explicit confirmation. " "Set confirmed=True to proceed."
            )

        # Validate command type against hardcoded allowlist
        if command_type not in ALLOWED_COMMANDS:
            raise ValueError(
                f"Invalid command type '{command_type}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_COMMANDS))}"
            )

        # Validate args for restart_service
        if command_type == "restart_service" and args not in ALLOWED_SERVICES:
            raise ValueError(
                f"Invalid service '{args}' for restart_service. "
                f"Allowed: {', '.join(sorted(ALLOWED_SERVICES))}"
            )

        # Validate target node ID format
        if not target_node_id or not target_node_id.startswith("!"):
            raise ValueError(
                f"Invalid target_node_id '{target_node_id}'. "
                "Must start with '!' (e.g., '!a1b2c3d4')."
            )

        # Check rate limit
        self._validate_rate_limit(target_node_id)

        # Generate nonce and expiry
        nonce = generate_nonce()
        expires_at = (
            datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=MAX_COMMAND_AGE_SECONDS)
        ).isoformat()

        # Build Pydantic model for validation
        cmd = RecoveryCommand(
            target_node_id=target_node_id,
            command_type=RecoveryCommandType(command_type),
            args=args,
            nonce=nonce,
            status=RecoveryCommandStatus.PENDING,
            confirmed=True,
            sender=sender,
            expires_at=expires_at,
        )

        # Store in DB
        command_id = self._db.create_recovery_command(
            target_node_id=target_node_id,
            command_type=command_type,
            args=args,
            nonce=nonce,
            sender=sender,
            expires_at=expires_at,
        )
        cmd.id = command_id

        logger.info(
            "Recovery command created: id=%d type=%s target=%s sender=%s",
            command_id,
            command_type,
            target_node_id,
            sender,
        )

        # Publish MQTT command to gateway agent
        self._publish_command(
            command_id=command_id,
            target_node_id=target_node_id,
            command_type=command_type,
            args=args,
            nonce=nonce,
            channel_index=ADMIN_CHANNEL_INDEX,
        )

        return cmd

    def _validate_rate_limit(self, target_node_id: str) -> None:
        """Check that we haven't sent a command to this node too recently.

        Raises:
            RuntimeError: If a command was sent within RATE_LIMIT_SECONDS.
        """
        recent = self._db.list_recovery_commands(target_node_id=target_node_id, limit=1)
        if not recent:
            return

        last_cmd = recent[0]
        created_str = last_cmd.get("created_at")
        if not created_str:
            return

        try:
            created_at = datetime.fromisoformat(created_str)
        except (ValueError, TypeError):
            return

        elapsed = (datetime.now(UTC).replace(tzinfo=None) - created_at).total_seconds()
        if elapsed < RATE_LIMIT_SECONDS:
            remaining = int(RATE_LIMIT_SECONDS - elapsed)
            raise RuntimeError(
                f"Rate limited: last command to {target_node_id} was {int(elapsed)}s ago. "
                f"Wait {remaining}s before sending another."
            )

    def _publish_command(
        self,
        command_id: int,
        target_node_id: str,
        command_type: str,
        args: str,
        nonce: str,
        channel_index: int,
    ) -> None:
        """Publish an MQTT command for the gateway agent to relay over mesh."""
        if self._mqtt_client is None:
            logger.warning(
                "No MQTT client — recovery command %d stored in DB but not sent to agent",
                command_id,
            )
            return

        mesh_text = format_recovery_text(command_id, command_type, args, nonce)
        payload = json.dumps(
            {
                "command_id": command_id,
                "target_node_id": target_node_id,
                "command_type": command_type,
                "args": args,
                "nonce": nonce,
                "mesh_text": mesh_text,
                "channel_index": channel_index,
            }
        )

        try:
            self._mqtt_client.publish(RECOVERY_COMMAND_TOPIC, payload)
            logger.info("Recovery command published to MQTT: command_id=%d", command_id)
        except Exception as e:
            logger.error("Failed to publish recovery command: %s", e)
            self._db.update_recovery_status(command_id, "failed", result_message=str(e))

    def mark_sent(self, command_id: int) -> None:
        """Mark a command as sent (gateway agent ACKed relay to mesh)."""
        now = datetime.now(UTC).replace(tzinfo=None).isoformat()
        self._db.update_recovery_status(command_id, "sent", sent_at=now)
        logger.info("Recovery command %d marked as sent", command_id)

    def mark_completed(self, command_id: int, result_message: str = "") -> None:
        """Mark a command as completed (target agent executed and ACKed success)."""
        now = datetime.now(UTC).replace(tzinfo=None).isoformat()
        self._db.update_recovery_status(
            command_id,
            "completed",
            result_message=result_message,
            completed_at=now,
        )
        logger.info("Recovery command %d completed: %s", command_id, result_message)

    def mark_failed(self, command_id: int, error: str = "") -> None:
        """Mark a command as failed (target agent reported failure or timeout)."""
        now = datetime.now(UTC).replace(tzinfo=None).isoformat()
        self._db.update_recovery_status(
            command_id,
            "failed",
            result_message=error,
            completed_at=now,
        )
        logger.warning("Recovery command %d failed: %s", command_id, error)

    def expire_stale_commands(self) -> int:
        """Find commands past their expires_at time and mark them expired.

        Returns:
            Number of commands expired.
        """
        now = datetime.now(UTC).replace(tzinfo=None).isoformat()
        # Only expire commands still in non-terminal states
        non_terminal = {"pending", "sending", "sent"}
        all_recent = self._db.list_recovery_commands(limit=200)

        expired_count = 0
        for cmd in all_recent:
            if cmd["status"] in non_terminal and cmd.get("expires_at"):
                if cmd["expires_at"] < now:
                    self._db.update_recovery_status(
                        cmd["id"],
                        "expired",
                        result_message="Command expired before completion",
                    )
                    logger.info("Recovery command %d expired", cmd["id"])
                    expired_count += 1

        return expired_count

    def get_command(self, command_id: int) -> Optional[dict]:
        """Get a single recovery command by ID."""
        return self._db.get_recovery_command(command_id)

    def list_commands(self, target_node_id: Optional[str] = None, limit: int = 50) -> list[dict]:
        """List recovery command history, most recent first."""
        return self._db.list_recovery_commands(target_node_id=target_node_id, limit=limit)

    def get_node_recovery_status(self, node_id: str) -> dict:
        """Get recovery status summary for a specific node.

        Returns dict with:
            - total_commands: count of all commands sent to this node
            - pending_commands: count of non-terminal commands
            - last_command_time: ISO timestamp of most recent command
            - last_command_status: status of most recent command
            - recent_commands: commands in the last 60 minutes targeting this node
        """
        all_commands = self._db.list_recovery_commands(target_node_id=node_id, limit=100)

        non_terminal = {"pending", "sending", "sent"}
        pending_count = sum(1 for c in all_commands if c["status"] in non_terminal)

        last_time = None
        last_status = None
        if all_commands:
            last_time = all_commands[0].get("created_at")
            last_status = all_commands[0].get("status")

        # Filter recent (last 60 min) from the already-fetched list
        recent = self._db.get_recent_recovery_commands(minutes=60)
        node_recent = [c for c in recent if c["target_node_id"] == node_id]

        return {
            "node_id": node_id,
            "total_commands": len(all_commands),
            "pending_commands": pending_count,
            "last_command_time": last_time,
            "last_command_status": last_status,
            "recent_commands": node_recent,
        }
