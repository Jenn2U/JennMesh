"""Firmware version tracking and update flagging."""

from __future__ import annotations

import logging
import re
from typing import Optional

from jenn_mesh.db import MeshDatabase

logger = logging.getLogger(__name__)

# Known firmware versions (updated periodically from Meshtastic releases)
# Format: hardware_model -> latest_version
DEFAULT_LATEST_VERSIONS: dict[str, str] = {
    "heltec_v3": "2.5.6",
    "tbeam": "2.5.6",
    "tbeam_s3": "2.5.6",
    "rak4631": "2.5.6",
    "t_echo": "2.5.6",
    "station_g2": "2.5.6",
    "nano_g2": "2.5.6",
}

# Minimum firmware version for PKC admin key support
MIN_PKC_VERSION = "2.5.0"


def parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a semver string into a comparable tuple."""
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version_str)
    if not match:
        return (0, 0, 0)
    return tuple(int(x) for x in match.groups())


def version_gte(current: str, minimum: str) -> bool:
    """Check if current version >= minimum version."""
    return parse_version(current) >= parse_version(minimum)


class FirmwareTracker:
    """Tracks firmware versions across the fleet and flags needed updates."""

    def __init__(
        self,
        db: MeshDatabase,
        latest_versions: Optional[dict[str, str]] = None,
    ):
        self.db = db
        self.latest_versions = latest_versions or DEFAULT_LATEST_VERSIONS

    def check_device_firmware(self, node_id: str) -> Optional[dict]:
        """Check if a device's firmware is current.

        Returns:
            Dict with firmware status, or None if device not found.
        """
        device = self.db.get_device(node_id)
        if device is None:
            return None

        hw_model = device.get("hw_model", "unknown")
        current = device.get("firmware_version", "unknown")
        latest = self.latest_versions.get(hw_model)

        needs_update = False
        if latest and current != "unknown":
            needs_update = parse_version(current) < parse_version(latest)

        supports_pkc = version_gte(current, MIN_PKC_VERSION) if current != "unknown" else False

        return {
            "node_id": node_id,
            "hw_model": hw_model,
            "current_version": current,
            "latest_version": latest or "unknown",
            "needs_update": needs_update,
            "supports_pkc": supports_pkc,
        }

    def get_fleet_firmware_report(self) -> list[dict]:
        """Generate firmware status for all devices in the fleet."""
        devices = self.db.list_devices()
        report = []

        for device in devices:
            status = self.check_device_firmware(device["node_id"])
            if status:
                report.append(status)

        return report

    def get_outdated_devices(self) -> list[dict]:
        """Get all devices that need firmware updates."""
        report = self.get_fleet_firmware_report()
        return [d for d in report if d["needs_update"]]

    def get_pkc_incompatible_devices(self) -> list[dict]:
        """Get all devices that don't support PKC admin keys."""
        report = self.get_fleet_firmware_report()
        return [d for d in report if not d["supports_pkc"]]

    def update_latest_versions(self, versions: dict[str, str]) -> None:
        """Update the known latest firmware versions."""
        self.latest_versions.update(versions)
