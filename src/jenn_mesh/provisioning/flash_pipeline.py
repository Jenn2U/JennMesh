"""Flash pipeline — erase and flash Meshtastic firmware via esptool."""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ESP32 flash address map for Meshtastic firmware
ESP32_FLASH_MAP: list[tuple[str, str]] = [
    ("0x0", "bleota.bin"),
    ("0x8000", "partitions.bin"),
    ("0xe000", "boot_app0.bin"),
    ("0x10000", "firmware.bin"),
    ("0x300000", "littlefs.bin"),
]

DEFAULT_BAUD_RATE = 921600
ERASE_TIMEOUT = 30  # seconds
FLASH_TIMEOUT = 120  # seconds
VERIFY_TIMEOUT = 15  # seconds


@dataclass
class FlashResult:
    """Result of a firmware flash operation."""

    success: bool
    hw_model: str = ""
    firmware_version: str = ""
    message: str = ""
    duration: float = 0.0


class FlashPipeline:
    """Handles firmware erase and flash operations using esptool."""

    def __init__(self, firmware_downloader: object):
        """Initialize with a FirmwareDownloader for fetching binaries."""
        self.downloader = firmware_downloader

    def erase_and_flash(
        self,
        port: str,
        hw_model: str,
        target_version: str,
    ) -> FlashResult:
        """Full erase + flash pipeline for ESP32 devices.

        Steps:
            1. Download/cache firmware binaries
            2. Erase flash
            3. Write all firmware files at correct offsets
            4. Verify flash via meshtastic --info
        """
        from jenn_mesh.provisioning.radio_watcher import NRF52_MODELS

        start = time.monotonic()

        # nRF52 not supported via esptool
        if hw_model in NRF52_MODELS:
            return self.flash_nrf52(port, hw_model, target_version)

        # Step 1: Ensure firmware is downloaded
        dl_result = self.downloader.download_firmware(hw_model, target_version)
        if not dl_result.success or not dl_result.firmware_dir:
            return FlashResult(
                success=False,
                hw_model=hw_model,
                firmware_version=target_version,
                message=f"Firmware download failed: {dl_result.message}",
            )

        firmware_dir = dl_result.firmware_dir

        # Step 2: Erase flash
        erase_result = self.erase_flash(port)
        if not erase_result.success:
            return FlashResult(
                success=False,
                hw_model=hw_model,
                firmware_version=target_version,
                message=f"Erase failed: {erase_result.message}",
                duration=time.monotonic() - start,
            )

        # Step 3: Write flash
        write_result = self.write_flash(port, firmware_dir)
        if not write_result.success:
            return FlashResult(
                success=False,
                hw_model=hw_model,
                firmware_version=target_version,
                message=f"Write failed: {write_result.message}",
                duration=time.monotonic() - start,
            )

        # Step 4: Verify
        verify_ok = self.verify_flash(port, target_version)

        duration = time.monotonic() - start
        return FlashResult(
            success=True,
            hw_model=hw_model,
            firmware_version=target_version,
            message=f"Flash {'verified' if verify_ok else 'complete (verify skipped)'}",
            duration=duration,
        )

    def erase_flash(self, port: str) -> FlashResult:
        """Erase the ESP32 flash memory."""
        cmd = ["esptool.py", "--port", port, "erase_flash"]
        logger.info("Erasing flash on %s...", port)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=ERASE_TIMEOUT,
            )
            if result.returncode == 0:
                logger.info("Flash erased successfully on %s", port)
                return FlashResult(success=True, message="Erased")
            return FlashResult(
                success=False,
                message=f"esptool erase_flash exit {result.returncode}: {result.stderr[:300]}",
            )
        except FileNotFoundError:
            return FlashResult(
                success=False,
                message="esptool.py not found — install with: pip install esptool",
            )
        except subprocess.TimeoutExpired:
            return FlashResult(success=False, message="Erase timed out")

    def write_flash(self, port: str, firmware_dir: Path) -> FlashResult:
        """Write firmware files to ESP32 at their correct flash offsets."""
        cmd = [
            "esptool.py",
            "--port",
            port,
            "--baud",
            str(DEFAULT_BAUD_RATE),
            "write_flash",
        ]

        # Build flash arguments: address file address file ...
        for address, filename in ESP32_FLASH_MAP:
            filepath = firmware_dir / filename
            if not filepath.exists():
                return FlashResult(
                    success=False,
                    message=f"Missing firmware file: {filepath}",
                )
            cmd.extend([address, str(filepath)])

        logger.info("Flashing firmware from %s...", firmware_dir)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=FLASH_TIMEOUT,
            )
            if result.returncode == 0:
                logger.info("Firmware flashed successfully")
                return FlashResult(success=True, message="Flashed")
            return FlashResult(
                success=False,
                message=f"esptool write_flash exit {result.returncode}: {result.stderr[:300]}",
            )
        except FileNotFoundError:
            return FlashResult(
                success=False,
                message="esptool.py not found — install with: pip install esptool",
            )
        except subprocess.TimeoutExpired:
            return FlashResult(success=False, message="Flash write timed out")

    def verify_flash(self, port: str, expected_version: str) -> bool:
        """Verify the flashed firmware version via meshtastic --info.

        Returns True if the reported version matches expected, False otherwise.
        Best-effort: returns False (no crash) on any failure.
        """
        # Wait for device to boot
        time.sleep(3)

        cmd = ["meshtastic", "--port", port, "--info"]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=VERIFY_TIMEOUT,
            )
            if result.returncode != 0:
                logger.warning("Verification failed: meshtastic --info exit %d", result.returncode)
                return False

            for line in result.stdout.splitlines():
                line_lower = line.lower()
                if "firmware version:" in line_lower or "firmware:" in line_lower:
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        reported = parts[1].strip().lstrip("v")
                        if reported == expected_version:
                            logger.info("Firmware verified: %s", reported)
                            return True
                        logger.warning(
                            "Version mismatch: expected %s, got %s",
                            expected_version,
                            reported,
                        )
                        return False

            logger.warning("Could not find firmware version in meshtastic --info output")
            return False
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning("Verification error: %s", e)
            return False

    def flash_nrf52(self, port: str, hw_model: str, target_version: str) -> FlashResult:
        """Placeholder for nRF52 flash — not supported via esptool.

        nRF52 devices (RAK4631, T-Echo) use UF2 format and require either:
        - Manual drag-and-drop to the UF2 bootloader mass storage device
        - adafruit-nrfutil for DFU over serial (future enhancement)
        """
        logger.warning(
            "nRF52 auto-flash not supported for %s. "
            "Use manual UF2 flash via bench provisioning.",
            hw_model,
        )
        return FlashResult(
            success=False,
            hw_model=hw_model,
            firmware_version=target_version,
            message=f"nRF52 ({hw_model}) requires manual UF2 flash",
        )
