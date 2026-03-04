"""Team communication models — text messaging through the mesh for field teams."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class MessageChannel(str, Enum):
    """Channels for team communication."""

    BROADCAST = "broadcast"
    TEAM = "team"
    DIRECT = "direct"


class MessageStatus(str, Enum):
    """Delivery status of a team message."""

    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"


# Channel 2 = Team comms (Channel 0 = Primary, 1 = Heartbeat, 3 = Emergency)
TEAM_CHANNEL_INDEX = 2

# Max message length — LoRa text messages are capped at 256 bytes,
# and the [TEAM:{channel}] prefix takes ~15 bytes.
MAX_TEAM_MESSAGE_LENGTH = 220


class TeamMessage(BaseModel):
    """A team communication message stored in the DB."""

    id: Optional[int] = Field(default=None, description="Auto-assigned by DB")
    channel: MessageChannel = Field(
        default=MessageChannel.BROADCAST,
        description="Message channel: broadcast, team, or direct",
    )
    sender: str = Field(description="Sender identifier (operator or node_id)")
    recipient: Optional[str] = Field(
        default=None,
        description="Target node_id for direct messages, team name for team, None for broadcast",
    )
    message: str = Field(description="Message text")
    mesh_channel_index: int = Field(
        default=TEAM_CHANNEL_INDEX,
        description="Meshtastic channel index for transmission",
    )
    status: MessageStatus = Field(default=MessageStatus.PENDING)
    created_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None

    @field_validator("message")
    @classmethod
    def validate_message_length(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Message cannot be empty")
        if len(v) > MAX_TEAM_MESSAGE_LENGTH:
            raise ValueError(
                f"Message exceeds {MAX_TEAM_MESSAGE_LENGTH} characters "
                f"(got {len(v)})"
            )
        return v.strip()

    @property
    def wire_format(self) -> str:
        """Format for LoRa mesh transmission: [TEAM:{channel}] {message}"""
        prefix = f"[TEAM:{self.channel.value.upper()}]"
        if self.recipient:
            return f"{prefix} @{self.recipient} {self.message}"
        return f"{prefix} {self.message}"
