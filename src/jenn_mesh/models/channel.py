"""Channel and security models — PSK management, channel definitions."""

from __future__ import annotations

import secrets
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ChannelRole(str, Enum):
    """Channel purpose in the mesh network."""

    PRIMARY = "primary"
    ADMIN = "admin"
    TELEMETRY = "telemetry"
    EMERGENCY = "emergency"
    CUSTOM = "custom"


class ChannelConfig(BaseModel):
    """A Meshtastic channel definition with PSK."""

    index: int = Field(ge=0, le=7, description="Channel index (0=primary, 1-7=secondary)")
    name: str = Field(description="Channel name (max 11 chars for Meshtastic)")
    role: ChannelRole = Field(default=ChannelRole.CUSTOM, description="Channel purpose")
    psk: str = Field(description="Pre-shared key (hex-encoded AES-128 or AES-256)")
    uplink_enabled: bool = Field(
        default=False, description="Forward packets from this channel to MQTT"
    )
    downlink_enabled: bool = Field(
        default=False, description="Forward MQTT packets to this channel"
    )

    @staticmethod
    def generate_psk(bits: int = 256) -> str:
        """Generate a random AES PSK as hex string.

        Args:
            bits: Key length — 128 or 256.

        Returns:
            Hex-encoded key string prefixed with 0x for Meshtastic CLI.
        """
        if bits not in (128, 256):
            raise ValueError("PSK must be 128 or 256 bits")
        key_bytes = secrets.token_bytes(bits // 8)
        return "0x" + key_bytes.hex()


class ChannelSet(BaseModel):
    """Complete channel configuration for a device — up to 8 channels."""

    channels: list[ChannelConfig] = Field(
        default_factory=list, max_length=8, description="Channels (index 0 = primary)"
    )

    def get_primary(self) -> Optional[ChannelConfig]:
        """Get the primary channel (index 0)."""
        for ch in self.channels:
            if ch.index == 0:
                return ch
        return None

    def get_admin(self) -> Optional[ChannelConfig]:
        """Get the admin channel (if configured)."""
        for ch in self.channels:
            if ch.role == ChannelRole.ADMIN:
                return ch
        return None


class AdminKeyConfig(BaseModel):
    """PKC admin key configuration for fleet management."""

    public_key: str = Field(description="Base64-encoded admin public key")
    description: str = Field(default="", description="Key purpose (e.g., 'fleet-admin-primary')")
    is_active: bool = Field(default=True, description="Whether this key is currently in use")


class SecurityConfig(BaseModel):
    """Security settings for a device."""

    admin_keys: list[AdminKeyConfig] = Field(
        default_factory=list, max_length=3, description="PKC admin keys (max 3 per device)"
    )
    is_managed: bool = Field(
        default=False,
        description="Managed Mode — blocks local config changes, PKC admin only",
    )
    admin_channel_enabled: bool = Field(
        default=False,
        description="Legacy admin channel (deprecated — use PKC admin keys instead)",
    )
