"""Firmware version tracking, update flagging, and hardware compatibility."""

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

# Compatibility status constants
COMPATIBLE = "COMPATIBLE"
INCOMPATIBLE = "INCOMPATIBLE"
UNTESTED = "UNTESTED"

# Default compatibility matrix: (hw_model, firmware_version) -> status
# Pre-seeded from known-good combinations
DEFAULT_COMPATIBILITY_MATRIX: list[tuple[str, str, str]] = [
    ("heltec_v3", "2.5.6", COMPATIBLE),
    ("heltec_v3", "2.5.0", COMPATIBLE),
    ("tbeam", "2.5.6", COMPATIBLE),
    ("tbeam", "2.5.0", COMPATIBLE),
    ("tbeam_s3", "2.5.6", COMPATIBLE),
    ("tbeam_s3", "2.5.0", COMPATIBLE),
    ("tbeam_s3", "2.4.2", COMPATIBLE),
    ("rak4631", "2.5.6", COMPATIBLE),
    ("rak4631", "2.5.0", COMPATIBLE),
    ("t_echo", "2.5.6", COMPATIBLE),
    ("t_echo", "2.4.0", INCOMPATIBLE),
    ("station_g2", "2.5.6", COMPATIBLE),
    ("nano_g2", "2.5.6", COMPATIBLE),
]


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

    # --- Firmware compatibility matrix methods (MESH-021) ---

    def seed_compatibility_matrix(self) -> int:
        """Seed the firmware compatibility table from DEFAULT_COMPATIBILITY_MATRIX."""
        return self.db.seed_firmware_compat(DEFAULT_COMPATIBILITY_MATRIX)

    def check_compatibility(self, hw_model: str, firmware_version: str) -> str:
        """Check compatibility status for a hardware-firmware combination.

        Returns COMPATIBLE, INCOMPATIBLE, or UNTESTED.
        """
        entry = self.db.get_firmware_compat_entry(hw_model, firmware_version)
        if entry is None:
            return UNTESTED
        return entry["status"]

    def get_compatible_versions(self, hw_model: str) -> list[dict]:
        """Get all compatible firmware versions for a hardware model."""
        entries = self.db.get_firmware_compat(hw_model)
        return [e for e in entries if e["status"] == COMPATIBLE]

    def get_compatibility_matrix(self) -> list[dict]:
        """Get the full firmware-hardware compatibility matrix."""
        return self.db.get_all_firmware_compat()

    def is_safe_to_flash(self, hw_model: str, target_version: str) -> bool:
        """Check if a firmware version is safe to flash on hardware.

        Only COMPATIBLE status is considered safe. UNTESTED is treated as
        unsafe to prevent bricking devices with untested combinations.
        """
        return self.check_compatibility(hw_model, target_version) == COMPATIBLE

    def add_compatibility_entry(
        self,
        hw_model: str,
        firmware_version: str,
        status: str = UNTESTED,
        notes: Optional[str] = None,
    ) -> None:
        """Add or update a firmware compatibility entry."""
        self.db.upsert_firmware_compat(hw_model, firmware_version, status, notes)

    def get_upgradeable_devices(self) -> list[dict]:
        """Get devices that can safely be upgraded to latest firmware.

        A device is upgradeable when:
        1. It needs a firmware update (current < latest)
        2. The latest firmware is COMPATIBLE with its hardware
        """
        outdated = self.get_outdated_devices()
        upgradeable = []

        for device in outdated:
            hw = device["hw_model"]
            latest = device["latest_version"]
            if latest != "unknown" and self.is_safe_to_flash(hw, latest):
                device["target_version"] = latest
                upgradeable.append(device)

        return upgradeable
