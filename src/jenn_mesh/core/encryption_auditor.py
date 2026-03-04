"""Encryption auditor — assess channel encryption strength across the fleet.

Reads from the existing ``channels`` and ``devices`` tables to classify
each device's encryption posture.  No new DB methods required — the auditor
is purely read-only over existing data.

Meshtastic encrypts channels with AES-256-CTR using a pre-shared key (PSK).
The factory default "LongFast" channel uses a single-byte key ``0x01``
(base64: ``AQ==``), which provides **no real encryption** — every device
with factory settings can decode it.  The auditor flags these as
``EncryptionStatus.UNENCRYPTED``.
"""

from __future__ import annotations

import logging
from typing import Optional

from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.encryption import (
    DeviceEncryptionAudit,
    EncryptionStatus,
    FleetEncryptionReport,
)

logger = logging.getLogger(__name__)

# Known weak / empty PSKs that provide no meaningful encryption.
# ``0x01`` is the Meshtastic factory-default "LongFast" single-byte key.
# ``AQ==`` is its base64 encoding.
EMPTY_PSKS: set[str] = {"", "0x", "0x00", "0x01", "AQ==", "AA=="}


def classify_psk_strength(psk: str) -> EncryptionStatus:
    """Classify a PSK's encryption strength.

    Trade-off: AES-128 (32 hex chars / 16 bytes) is cryptographically secure
    but not the fleet standard (AES-256 / 64 hex chars / 32 bytes).  We treat
    AES-128 as "strong" to avoid false positives — fleet policy enforcement
    can use a stricter check separately.

    Classification:
    - Empty / default / single-byte → UNENCRYPTED
    - 1-15 byte hex key → WEAK  (too short for AES)
    - 16+ byte hex key or 24+ char base64 → STRONG (AES-128 or AES-256)

    TODO: This is a user-contribution point.  See the plan for trade-off
    discussion (AES-128 as "strong" vs "weak").
    """
    if not psk or psk.strip() in EMPTY_PSKS:
        return EncryptionStatus.UNENCRYPTED

    stripped = psk.strip()

    # Hex-encoded PSK: "0x" prefix followed by hex chars
    if stripped.startswith("0x") or stripped.startswith("0X"):
        hex_part = stripped[2:]
        if len(hex_part) < 32:  # Less than 16 bytes
            return EncryptionStatus.WEAK
        return EncryptionStatus.STRONG

    # Base64-encoded PSK: 24+ chars ≈ 16+ bytes decoded
    if len(stripped) >= 24:
        return EncryptionStatus.STRONG

    # Short base64 or unknown format — treat as weak
    if len(stripped) >= 4:
        return EncryptionStatus.WEAK

    return EncryptionStatus.UNENCRYPTED


class EncryptionAuditor:
    """Audit channel encryption status across the fleet."""

    def __init__(self, db: MeshDatabase) -> None:
        self.db = db

    def audit_fleet(self) -> FleetEncryptionReport:
        """Audit encryption for all devices.

        Returns a fleet-wide report with per-device breakdowns and an
        overall encryption score (0-100).
        """
        devices = self.db.list_devices()
        audits: list[DeviceEncryptionAudit] = []

        for device in devices:
            audit = self.audit_device(device["node_id"])
            audits.append(audit)

        strong = sum(1 for a in audits if a.encryption_status == EncryptionStatus.STRONG)
        weak = sum(1 for a in audits if a.encryption_status == EncryptionStatus.WEAK)
        unencrypted = sum(1 for a in audits if a.encryption_status == EncryptionStatus.UNENCRYPTED)
        unknown = sum(1 for a in audits if a.encryption_status == EncryptionStatus.UNKNOWN)

        total = len(audits)
        score = (strong / total * 100.0) if total > 0 else 100.0

        return FleetEncryptionReport(
            fleet_score=round(score, 1),
            total_devices=total,
            strong_count=strong,
            weak_count=weak,
            unencrypted_count=unencrypted,
            unknown_count=unknown,
            devices=audits,
        )

    def audit_device(self, node_id: str) -> DeviceEncryptionAudit:
        """Audit a single device's channel encryption status."""
        channels = self._get_device_channels(node_id)

        if not channels:
            return DeviceEncryptionAudit(
                node_id=node_id,
                encryption_status=EncryptionStatus.UNKNOWN,
                channel_count=0,
            )

        weak_channels: list[dict] = []
        uses_default = False
        worst_status = EncryptionStatus.STRONG

        for ch in channels:
            psk = ch.get("psk", "")
            status = classify_psk_strength(psk)

            if status == EncryptionStatus.UNENCRYPTED:
                if psk.strip() in EMPTY_PSKS:
                    uses_default = True
                weak_channels.append(
                    {
                        "channel_index": ch.get("channel_index"),
                        "name": ch.get("name", ""),
                        "reason": "unencrypted (default or empty PSK)",
                    }
                )
                worst_status = EncryptionStatus.UNENCRYPTED
            elif status == EncryptionStatus.WEAK:
                weak_channels.append(
                    {
                        "channel_index": ch.get("channel_index"),
                        "name": ch.get("name", ""),
                        "reason": "weak PSK (too short for AES)",
                    }
                )
                if worst_status == EncryptionStatus.STRONG:
                    worst_status = EncryptionStatus.WEAK

        return DeviceEncryptionAudit(
            node_id=node_id,
            encryption_status=worst_status,
            weak_channels=weak_channels,
            uses_default_longfast=uses_default,
            channel_count=len(channels),
        )

    def get_fleet_encryption_score(self) -> float:
        """Percentage of devices with strong encryption (0-100).

        Lightweight version — does not return per-device details.
        """
        report = self.audit_fleet()
        return report.fleet_score

    def _get_device_channels(self, node_id: str) -> list[dict]:
        """Get channels associated with a device.

        Currently the ``channels`` table stores fleet-wide channel definitions
        (not per-device).  We return all channels since Meshtastic pushes the
        same channel config to all devices in a fleet.  If per-device channel
        data becomes available (e.g. from ADMIN_APP packets), this method
        would filter by node_id.
        """
        try:
            with self.db.connection() as conn:
                rows = conn.execute(
                    "SELECT channel_index, name, psk FROM channels ORDER BY channel_index"
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception:
            logger.debug("Failed to read channels for %s", node_id, exc_info=True)
            return []
