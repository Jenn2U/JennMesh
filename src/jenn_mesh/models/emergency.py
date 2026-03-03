"""Emergency broadcast models — operator-initiated alerts over LoRa mesh."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class EmergencyType(str, Enum):
    """Types of emergency broadcasts that operators can send."""

    EVACUATION = "evacuation"
    NETWORK_DOWN = "network_down"
    SEVERE_WEATHER = "severe_weather"
    SECURITY_ALERT = "security_alert"
    ALL_CLEAR = "all_clear"
    CUSTOM = "custom"


class BroadcastStatus(str, Enum):
    """Lifecycle status of an emergency broadcast."""

    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"


# Human-readable labels for dashboard display
EMERGENCY_TYPE_LABELS: dict[EmergencyType, str] = {
    EmergencyType.EVACUATION: "Evacuation",
    EmergencyType.NETWORK_DOWN: "Network Down",
    EmergencyType.SEVERE_WEATHER: "Severe Weather",
    EmergencyType.SECURITY_ALERT: "Security Alert",
    EmergencyType.ALL_CLEAR: "All Clear",
    EmergencyType.CUSTOM: "Custom Alert",
}

# Default channel for emergency broadcasts (Channel 3 = Emergency)
EMERGENCY_CHANNEL_INDEX = 3

# Max message length — LoRa text messages are capped at 256 bytes,
# and the [EMERGENCY:{TYPE}] prefix takes ~25 bytes.
MAX_MESSAGE_LENGTH = 200


class EmergencyBroadcast(BaseModel):
    """An emergency broadcast record — stored in DB for audit trail."""

    id: Optional[int] = Field(default=None, description="Auto-assigned by DB")
    broadcast_type: EmergencyType = Field(description="Category of emergency")
    message: str = Field(description="Human-readable emergency message")
    sender: str = Field(default="dashboard", description="Who initiated the broadcast")
    channel_index: int = Field(
        default=EMERGENCY_CHANNEL_INDEX,
        description="Meshtastic channel index (3 = Emergency)",
    )
    status: BroadcastStatus = Field(default=BroadcastStatus.PENDING)
    confirmed: bool = Field(
        default=False,
        description="Operator must explicitly confirm before sending",
    )
    mesh_received: bool = Field(
        default=False,
        description="Whether the broadcast was echoed back through the mesh",
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    sent_at: Optional[datetime] = Field(default=None)
    delivered_at: Optional[datetime] = Field(default=None)

    @field_validator("message")
    @classmethod
    def validate_message_length(cls, v: str) -> str:
        """Ensure message fits within LoRa text frame."""
        if not v.strip():
            raise ValueError("Emergency message cannot be empty")
        if len(v) > MAX_MESSAGE_LENGTH:
            raise ValueError(
                f"Message too long ({len(v)} chars). "
                f"Maximum is {MAX_MESSAGE_LENGTH} chars to fit in LoRa frame."
            )
        return v.strip()

    @property
    def is_active(self) -> bool:
        """True if broadcast is in-flight (not yet delivered or failed)."""
        return self.status in (
            BroadcastStatus.PENDING,
            BroadcastStatus.SENDING,
            BroadcastStatus.SENT,
        )

    @staticmethod
    def format_mesh_text(broadcast_type: EmergencyType, message: str) -> str:
        """Build the human-readable text that appears on radio screens.

        Format: [EMERGENCY:{TYPE}] {message}

        Example: [EMERGENCY:EVACUATION] Building 3 fire alarm. Evacuate immediately.
        """
        return f"[EMERGENCY:{broadcast_type.value.upper()}] {message}"

    @staticmethod
    def parse_mesh_text(text: str) -> Optional[tuple[str, str]]:
        """Parse a mesh text message to extract emergency type and message.

        Returns (type_string, message) tuple, or None if not an emergency message.
        """
        prefix = "[EMERGENCY:"
        if not text.startswith(prefix):
            return None

        bracket_end = text.find("]", len(prefix))
        if bracket_end == -1:
            return None

        type_str = text[len(prefix) : bracket_end].lower()
        message = text[bracket_end + 1 :].strip()
        return (type_str, message)
