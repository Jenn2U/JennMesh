"""Channel manager — PSK generation, channel set distribution."""

from __future__ import annotations

from typing import Optional

from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.channel import ChannelConfig, ChannelRole, ChannelSet


class ChannelManager:
    """Centralized management of Meshtastic channels and PSKs."""

    def __init__(self, db: MeshDatabase):
        self.db = db

    def create_default_channel_set(self) -> ChannelSet:
        """Create the standard JennMesh channel set with fresh PSKs.

        Default channels:
          0: JennMesh (primary) — encrypted, MQTT uplink/downlink
          1: admin — for remote admin (legacy fallback, prefer PKC)
          2: telemetry — dedicated telemetry channel
          3: emergency — emergency broadcast channel
        """
        channels = [
            ChannelConfig(
                index=0,
                name="JennMesh",
                role=ChannelRole.PRIMARY,
                psk=ChannelConfig.generate_psk(256),
                uplink_enabled=True,
                downlink_enabled=True,
            ),
            ChannelConfig(
                index=1,
                name="admin",
                role=ChannelRole.ADMIN,
                psk=ChannelConfig.generate_psk(256),
                uplink_enabled=False,
                downlink_enabled=False,
            ),
            ChannelConfig(
                index=2,
                name="telemetry",
                role=ChannelRole.TELEMETRY,
                psk=ChannelConfig.generate_psk(256),
                uplink_enabled=True,
                downlink_enabled=False,
            ),
            ChannelConfig(
                index=3,
                name="emergency",
                role=ChannelRole.EMERGENCY,
                psk=ChannelConfig.generate_psk(256),
                uplink_enabled=True,
                downlink_enabled=True,
            ),
        ]

        channel_set = ChannelSet(channels=channels)
        self._save_channels(channels)
        return channel_set

    def get_channel_set(self) -> ChannelSet:
        """Load the current channel set from the database, or create defaults."""
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM channels ORDER BY channel_index"
            ).fetchall()

        if not rows:
            return self.create_default_channel_set()

        channels = [
            ChannelConfig(
                index=r["channel_index"],
                name=r["name"],
                role=ChannelRole(r["role"]),
                psk=r["psk"],
                uplink_enabled=bool(r["uplink_enabled"]),
                downlink_enabled=bool(r["downlink_enabled"]),
            )
            for r in rows
        ]
        return ChannelSet(channels=channels)

    def rotate_psk(self, channel_index: int) -> Optional[str]:
        """Generate a new PSK for a channel. Returns the new PSK or None if not found."""
        new_psk = ChannelConfig.generate_psk(256)
        with self.db.connection() as conn:
            cursor = conn.execute(
                "UPDATE channels SET psk = ? WHERE channel_index = ?",
                (new_psk, channel_index),
            )
            if cursor.rowcount == 0:
                return None
        return new_psk

    def _save_channels(self, channels: list[ChannelConfig]) -> None:
        """Persist channel definitions to the database."""
        with self.db.connection() as conn:
            for ch in channels:
                conn.execute(
                    """INSERT INTO channels
                       (channel_index, name, role, psk, uplink_enabled, downlink_enabled)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(channel_index, name) DO UPDATE SET
                       psk = excluded.psk,
                       uplink_enabled = excluded.uplink_enabled,
                       downlink_enabled = excluded.downlink_enabled""",
                    (
                        ch.index,
                        ch.name,
                        ch.role.value,
                        ch.psk,
                        int(ch.uplink_enabled),
                        int(ch.downlink_enabled),
                    ),
                )
