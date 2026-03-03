"""Recovery command models — wire format, enums, and validation for mesh recovery."""

from __future__ import annotations

import secrets
import time
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

# Channel 1 (ADMIN) — PSK-encrypted, configured on all fleet devices
ADMIN_CHANNEL_INDEX = 1

# Wire format prefixes
RECOVERY_PREFIX = "RECOVER|"
RECOVERY_ACK_PREFIX = "RECOVER_ACK|"

# Safety: hardcoded command allowlists — NOT configurable
ALLOWED_COMMANDS = frozenset({"reboot", "restart_service", "restart_ollama", "system_status"})
ALLOWED_SERVICES = frozenset({"jennedge", "jenn-sentry-agent", "jenn-mesh-agent", "ollama"})

# Timing constants
MAX_COMMAND_AGE_SECONDS = 300  # 5 minutes — reject stale commands
RATE_LIMIT_SECONDS = 30  # 1 command per node per 30 seconds
NONCE_LENGTH = 8  # 8-char hex string


class RecoveryCommandType(str, Enum):
    """Allowed recovery command types."""

    REBOOT = "reboot"
    RESTART_SERVICE = "restart_service"
    RESTART_OLLAMA = "restart_ollama"
    SYSTEM_STATUS = "system_status"


class RecoveryCommandStatus(str, Enum):
    """Lifecycle states for a recovery command."""

    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class RecoveryCommand(BaseModel):
    """A recovery command sent to an edge node over mesh."""

    id: Optional[int] = Field(default=None, description="DB-assigned ID")
    target_node_id: str = Field(description="Meshtastic node ID, e.g., '!a1b2c3d4'")
    command_type: RecoveryCommandType
    args: str = Field(default="", description="Command-specific args (e.g., service name)")
    nonce: str = Field(description="8-char hex nonce for replay prevention")
    status: RecoveryCommandStatus = Field(default=RecoveryCommandStatus.PENDING)
    confirmed: bool = Field(default=False, description="Operator confirmed the action")
    sender: str = Field(default="dashboard", description="Who initiated the command")
    result_message: Optional[str] = Field(default=None, description="Response from target agent")
    created_at: Optional[str] = Field(default=None)
    sent_at: Optional[str] = Field(default=None)
    completed_at: Optional[str] = Field(default=None)
    expires_at: Optional[str] = Field(default=None)


def generate_nonce() -> str:
    """Generate an 8-character hex nonce for replay prevention."""
    return secrets.token_hex(NONCE_LENGTH // 2)


def format_recovery_text(
    cmd_id: int,
    command_type: str,
    args: str,
    nonce: str,
    timestamp: Optional[int] = None,
) -> str:
    """Build the mesh text message for a recovery command.

    Format: RECOVER|{cmd_id}|{command_type}|{args}|{nonce}|{timestamp}
    Example: RECOVER|42|restart_service|jennedge|a1b2c3d4|1709395200

    Args:
        cmd_id: Database command ID.
        command_type: One of ALLOWED_COMMANDS.
        args: Command-specific arguments (empty string if none).
        nonce: 8-char hex nonce.
        timestamp: Unix epoch seconds (defaults to now).

    Returns:
        Pipe-delimited recovery text for LoRa transmission.
    """
    if timestamp is None:
        timestamp = int(time.time())
    return f"RECOVER|{cmd_id}|{command_type}|{args}|{nonce}|{timestamp}"


def parse_recovery_text(text: str) -> Optional[dict]:
    """Parse a recovery command from mesh text.

    Args:
        text: Raw text received from mesh.

    Returns:
        Dict with cmd_id, command_type, args, nonce, timestamp if valid, else None.
    """
    if not text.startswith(RECOVERY_PREFIX):
        return None

    parts = text.split("|")
    if len(parts) != 6:
        return None

    try:
        return {
            "cmd_id": int(parts[1]),
            "command_type": parts[2],
            "args": parts[3],
            "nonce": parts[4],
            "timestamp": int(parts[5]),
        }
    except (ValueError, IndexError):
        return None


def format_recovery_ack(cmd_id: int, status: str, message: str) -> str:
    """Build the mesh text ACK for a recovery command response.

    Format: RECOVER_ACK|{cmd_id}|{status}|{message}
    Example: RECOVER_ACK|42|success|jennedge restarted

    Args:
        cmd_id: Database command ID being acknowledged.
        status: "success" or "failed".
        message: Human-readable result (truncated to fit LoRa limit).

    Returns:
        Pipe-delimited ACK text for LoRa transmission.
    """
    # Truncate message to keep total under 256 bytes
    max_msg_len = 200 - len(f"RECOVER_ACK|{cmd_id}|{status}|")
    if len(message) > max_msg_len:
        message = message[:max_msg_len]
    return f"RECOVER_ACK|{cmd_id}|{status}|{message}"


def parse_recovery_ack(text: str) -> Optional[dict]:
    """Parse a recovery ACK from mesh text.

    Args:
        text: Raw text received from mesh.

    Returns:
        Dict with cmd_id, status, message if valid, else None.
    """
    if not text.startswith(RECOVERY_ACK_PREFIX):
        return None

    # Split into exactly 4 parts (message may contain pipes)
    parts = text.split("|", 3)
    if len(parts) != 4:
        return None

    try:
        return {
            "cmd_id": int(parts[1]),
            "status": parts[2],
            "message": parts[3],
        }
    except (ValueError, IndexError):
        return None
