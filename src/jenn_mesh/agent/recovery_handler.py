"""Recovery handler — target-agent-side: receive, validate, execute, and ACK recovery commands.

Runs on edge nodes that have lost internet but still have a working radio.
Commands arrive via LoRa mesh on Channel 1 (ADMIN), are validated for
replay/staleness, executed via subprocess, and ACKed back over mesh.
"""

from __future__ import annotations

import logging
import subprocess
import time
from collections import deque

from jenn_mesh.models.recovery import (
    ADMIN_CHANNEL_INDEX,
    ALLOWED_COMMANDS,
    ALLOWED_SERVICES,
    MAX_COMMAND_AGE_SECONDS,
    format_recovery_ack,
    parse_recovery_text,
)

logger = logging.getLogger(__name__)

# Maximum nonces to track for replay prevention
MAX_NONCE_HISTORY = 100

# Subprocess timeout for command execution
COMMAND_TIMEOUT_SECONDS = 30


class RecoveryHandler:
    """Handles incoming recovery commands on the target agent.

    Lifecycle:
        1. Mesh text arrives via RadioBridge PACKET_TEXT callback
        2. handle_mesh_text() parses RECOVER| prefix
        3. Validates: nonce uniqueness, timestamp freshness, command allowlist
        4. Executes OS command via subprocess
        5. Sends RECOVER_ACK back via mesh on Channel 1 (ADMIN)
    """

    def __init__(self, bridge: object, node_id: str = ""):
        """Initialize the recovery handler.

        Args:
            bridge: RadioBridge instance with send_text() method.
            node_id: Local Meshtastic node ID (e.g., '!a1b2c3d4').
                    May be set after init once bridge.connect() resolves it.
        """
        self._bridge = bridge
        self.node_id = node_id
        self._seen_nonces: deque[str] = deque(maxlen=MAX_NONCE_HISTORY)

    def handle_mesh_text(self, text: str, from_id: str = "") -> bool:
        """Process an incoming mesh text — execute if it's a valid recovery command.

        Args:
            text: Raw text received from mesh.
            from_id: Sender's Meshtastic node ID (for logging).

        Returns:
            True if the text was a recovery command (valid or not), False otherwise.
        """
        parsed = parse_recovery_text(text)
        if parsed is None:
            return False  # Not a recovery command — let other handlers process it

        cmd_id = parsed["cmd_id"]
        command_type = parsed["command_type"]
        args = parsed["args"]
        nonce = parsed["nonce"]
        timestamp = parsed["timestamp"]

        logger.info(
            "Recovery command received: cmd_id=%d type=%s args='%s' from=%s",
            cmd_id,
            command_type,
            args,
            from_id,
        )

        # Validate nonce + timestamp
        if not self._validate_command(cmd_id, nonce, timestamp):
            return True  # Was a recovery command, but rejected

        # Validate command type against allowlist
        if command_type not in ALLOWED_COMMANDS:
            logger.warning(
                "Recovery command %d rejected: unknown command type '%s'",
                cmd_id,
                command_type,
            )
            self._send_ack(cmd_id, False, f"unknown command: {command_type}")
            return True

        # Execute the command
        success, message = self._execute_command(command_type, args)

        # Send ACK back via mesh
        self._send_ack(cmd_id, success, message)

        return True

    def _validate_command(self, cmd_id: int, nonce: str, timestamp: int) -> bool:
        """Validate nonce uniqueness and timestamp freshness.

        Args:
            cmd_id: Command ID for logging.
            nonce: 8-char hex nonce.
            timestamp: Unix epoch seconds from the command.

        Returns:
            True if valid, False if rejected.
        """
        # Replay check: reject duplicate nonces
        if nonce in self._seen_nonces:
            logger.warning("Recovery command %d rejected: duplicate nonce '%s'", cmd_id, nonce)
            self._send_ack(cmd_id, False, "duplicate nonce (replay rejected)")
            return False

        # Staleness check: reject commands older than MAX_COMMAND_AGE_SECONDS
        age = abs(int(time.time()) - timestamp)
        if age > MAX_COMMAND_AGE_SECONDS:
            logger.warning(
                "Recovery command %d rejected: stale (age=%ds, max=%ds)",
                cmd_id,
                age,
                MAX_COMMAND_AGE_SECONDS,
            )
            self._send_ack(cmd_id, False, f"command too old ({age}s)")
            return False

        # Accept: record nonce
        self._seen_nonces.append(nonce)
        return True

    def _execute_command(self, command_type: str, args: str) -> tuple[bool, str]:
        """Execute a recovery command and return (success, message).

        Args:
            command_type: One of ALLOWED_COMMANDS.
            args: Command-specific arguments.

        Returns:
            Tuple of (success: bool, message: str).
        """
        try:
            if command_type == "reboot":
                return self._execute_reboot()
            elif command_type == "restart_service":
                return self._execute_restart_service(args)
            elif command_type == "restart_ollama":
                return self._execute_restart_service("ollama")
            elif command_type == "system_status":
                return self._execute_system_status()
            else:
                return False, f"unhandled command: {command_type}"
        except Exception as e:
            logger.error("Recovery command execution error: %s", e)
            return False, f"execution error: {e}"

    def _execute_reboot(self) -> tuple[bool, str]:
        """Execute system reboot."""
        logger.warning("Executing recovery REBOOT")
        try:
            subprocess.Popen(
                ["sudo", "shutdown", "-r", "now"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True, "reboot initiated"
        except Exception as e:
            return False, f"reboot failed: {e}"

    def _execute_restart_service(self, service: str) -> tuple[bool, str]:
        """Restart a systemd service by name.

        Args:
            service: Service name — validated against ALLOWED_SERVICES.

        Returns:
            Tuple of (success, message).
        """
        if service not in ALLOWED_SERVICES:
            return False, f"service not allowed: {service}"

        logger.info("Restarting service: %s", service)
        try:
            result = subprocess.run(
                ["sudo", "systemctl", "restart", service],
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
            if result.returncode == 0:
                return True, f"{service} restarted"
            else:
                stderr = result.stderr.strip()[:100]  # Truncate for LoRa
                return False, f"{service} restart failed: {stderr}"
        except subprocess.TimeoutExpired:
            return False, f"{service} restart timed out"
        except Exception as e:
            return False, f"{service} restart error: {e}"

    def _execute_system_status(self) -> tuple[bool, str]:
        """Collect system diagnostics and return as a compact string.

        Returns:
            Tuple of (True, status_string). The status string is kept under
            ~200 bytes to fit within the LoRa ACK message limit.

        Collects: uptime, disk usage, memory usage, and key service states.
        Designed for quick triage of an offline edge node.
        """
        # TODO: This is a good candidate for user customization.
        # Trade-off: more metrics = better diagnostics, but the ~200-byte
        # LoRa limit means we must be ruthlessly compact.
        parts = []

        # Uptime
        try:
            result = subprocess.run(
                ["uptime", "-p"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                parts.append(f"up:{result.stdout.strip()[:30]}")
        except Exception:
            parts.append("up:?")

        # Disk usage (root partition)
        try:
            result = subprocess.run(
                ["df", "-h", "--output=pcent", "/"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                if len(lines) >= 2:
                    parts.append(f"disk:{lines[1].strip()}")
        except Exception:
            parts.append("disk:?")

        # Memory usage
        try:
            result = subprocess.run(
                ["free", "-m"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                if len(lines) >= 2:
                    mem_parts = lines[1].split()
                    if len(mem_parts) >= 3:
                        parts.append(f"mem:{mem_parts[2]}M/{mem_parts[1]}M")
        except Exception:
            parts.append("mem:?")

        # Key service states
        for svc in ["jennedge", "ollama"]:
            try:
                result = subprocess.run(
                    ["systemctl", "is-active", svc],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                state = result.stdout.strip()[:10]
                parts.append(f"{svc}:{state}")
            except Exception:
                parts.append(f"{svc}:?")

        status = "|".join(parts)
        # Truncate to fit LoRa ACK (~200 byte message budget)
        if len(status) > 180:
            status = status[:180]

        return True, status

    def _send_ack(self, cmd_id: int, success: bool, message: str) -> None:
        """Send a RECOVER_ACK text back over mesh on Channel 1 (ADMIN).

        Args:
            cmd_id: Command ID to acknowledge.
            success: Whether the command succeeded.
            message: Human-readable result.
        """
        status = "success" if success else "failed"
        ack_text = format_recovery_ack(cmd_id, status, message)

        logger.info("Sending recovery ACK: cmd_id=%d status=%s", cmd_id, status)
        try:
            sent = self._bridge.send_text(
                ack_text,
                destination=None,  # Broadcast ACK — gateway will pick it up
                channel_index=ADMIN_CHANNEL_INDEX,
            )
            if not sent:
                logger.error("Failed to send recovery ACK for cmd_id=%d", cmd_id)
        except Exception as e:
            logger.error("Error sending recovery ACK: %s", e)
