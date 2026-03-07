"""Firmware downloader — fetches Meshtastic firmware from GitHub releases."""

from __future__ import annotations

import hashlib
import logging
import platform
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# GitHub release URL pattern for Meshtastic firmware
GITHUB_RELEASE_URL = (
    "https://github.com/meshtastic/firmware/releases/download/"
    "v{version}/firmware-{version}.zip"
)

# Map hw_model → filename pattern inside the firmware ZIP
# ESP32 devices need multiple binaries; we extract the device-specific directory
FIRMWARE_ASSET_MAP: dict[str, str] = {
    "heltec_v3": "firmware-heltec-v3-{version}",
    "tbeam": "firmware-tbeam-{version}",
    "tbeam_s3": "firmware-tbeam-s3-core-{version}",
    "station_g2": "firmware-station-g2-{version}",
    "nano_g2": "firmware-nano-g2-ultra-{version}",
}

# ESP32 flash files required (relative to device firmware directory)
ESP32_FLASH_FILES = [
    "bleota.bin",
    "partitions.bin",
    "boot_app0.bin",
    "firmware.bin",
    "littlefs.bin",
]

# nRF52 uses UF2 format
NRF52_ASSET_MAP: dict[str, str] = {
    "rak4631": "firmware-rak4631-{version}.uf2",
    "t_echo": "firmware-t-echo-{version}.uf2",
}


def _default_cache_dir() -> Path:
    """Platform-specific firmware cache directory."""
    system = platform.system()
    if system == "Linux":
        return Path("/var/lib/jenn-mesh/firmware")
    elif system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "JennMesh" / "firmware"
    elif system == "Windows":
        appdata = Path(platform.os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return appdata / "JennMesh" / "firmware"
    return Path("/var/lib/jenn-mesh/firmware")


@dataclass
class DownloadResult:
    """Result of a firmware download operation."""

    success: bool
    firmware_dir: Optional[Path] = None
    version: str = ""
    hw_model: str = ""
    message: str = ""


class FirmwareDownloader:
    """Downloads and caches Meshtastic firmware releases from GitHub."""

    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = Path(cache_dir) if cache_dir else _default_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_firmware_path(self, hw_model: str, version: str) -> Optional[Path]:
        """Get the cached firmware directory for a hardware model + version.

        Returns the path if cached (and verified), None if not available.
        """
        device_dir = self.cache_dir / version / hw_model
        if device_dir.is_dir():
            # Verify all required files exist
            if hw_model in NRF52_ASSET_MAP:
                uf2_name = NRF52_ASSET_MAP[hw_model].format(version=version)
                if (device_dir / uf2_name).exists():
                    return device_dir
            else:
                if all((device_dir / f).exists() for f in ESP32_FLASH_FILES):
                    return device_dir
        return None

    def download_firmware(self, hw_model: str, version: str) -> DownloadResult:
        """Download firmware for a specific hardware model and version.

        Downloads the full release ZIP from GitHub, extracts the device-specific
        binaries, and caches them locally.
        """
        # Check cache first
        cached = self.get_firmware_path(hw_model, version)
        if cached:
            logger.info("Firmware %s for %s already cached at %s", version, hw_model, cached)
            return DownloadResult(
                success=True, firmware_dir=cached,
                version=version, hw_model=hw_model,
                message="Cached",
            )

        # Download release ZIP
        url = GITHUB_RELEASE_URL.format(version=version)
        zip_path = self.cache_dir / f"firmware-{version}.zip"

        if not zip_path.exists():
            logger.info("Downloading firmware %s from %s", version, url)
            try:
                import httpx

                with httpx.stream("GET", url, follow_redirects=True, timeout=120) as resp:
                    if resp.status_code != 200:
                        return DownloadResult(
                            success=False, version=version, hw_model=hw_model,
                            message=f"HTTP {resp.status_code} downloading firmware",
                        )
                    with open(zip_path, "wb") as f:
                        for chunk in resp.iter_bytes(chunk_size=8192):
                            f.write(chunk)
                logger.info("Downloaded %s (%.1f MB)", zip_path.name, zip_path.stat().st_size / 1e6)
            except ImportError:
                return DownloadResult(
                    success=False, version=version, hw_model=hw_model,
                    message="httpx not installed — cannot download firmware",
                )
            except Exception as e:
                zip_path.unlink(missing_ok=True)
                return DownloadResult(
                    success=False, version=version, hw_model=hw_model,
                    message=f"Download error: {e}",
                )

        # Extract device-specific files
        return self._extract_device_firmware(zip_path, hw_model, version)

    def _extract_device_firmware(
        self, zip_path: Path, hw_model: str, version: str
    ) -> DownloadResult:
        """Extract device-specific firmware from the release ZIP."""
        device_dir = self.cache_dir / version / hw_model
        device_dir.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                if hw_model in NRF52_ASSET_MAP:
                    return self._extract_nrf52(zf, device_dir, hw_model, version)
                else:
                    return self._extract_esp32(zf, device_dir, hw_model, version)
        except zipfile.BadZipFile:
            zip_path.unlink(missing_ok=True)
            return DownloadResult(
                success=False, version=version, hw_model=hw_model,
                message="Corrupt ZIP file — deleted, retry download",
            )

    def _extract_esp32(
        self, zf: zipfile.ZipFile, device_dir: Path, hw_model: str, version: str
    ) -> DownloadResult:
        """Extract ESP32 firmware files (5 binaries)."""
        asset_prefix = FIRMWARE_ASSET_MAP.get(hw_model)
        if not asset_prefix:
            return DownloadResult(
                success=False, version=version, hw_model=hw_model,
                message=f"No firmware asset mapping for {hw_model}",
            )

        prefix = asset_prefix.format(version=version)
        extracted = []

        for fname in ESP32_FLASH_FILES:
            # Try common ZIP structures: direct or nested in a directory
            candidates = [
                f"{prefix}/{fname}",
                f"firmware/{prefix}/{fname}",
                fname,
            ]
            for candidate in candidates:
                if candidate in zf.namelist():
                    data = zf.read(candidate)
                    out_path = device_dir / fname
                    out_path.write_bytes(data)
                    extracted.append(fname)
                    break

        if len(extracted) < len(ESP32_FLASH_FILES):
            missing = set(ESP32_FLASH_FILES) - set(extracted)
            return DownloadResult(
                success=False, firmware_dir=device_dir,
                version=version, hw_model=hw_model,
                message=f"Missing firmware files: {missing}",
            )

        logger.info("Extracted %d ESP32 firmware files for %s v%s", len(extracted), hw_model, version)
        return DownloadResult(
            success=True, firmware_dir=device_dir,
            version=version, hw_model=hw_model,
            message=f"Extracted {len(extracted)} files",
        )

    def _extract_nrf52(
        self, zf: zipfile.ZipFile, device_dir: Path, hw_model: str, version: str
    ) -> DownloadResult:
        """Extract nRF52 UF2 firmware file."""
        uf2_name = NRF52_ASSET_MAP[hw_model].format(version=version)
        candidates = [uf2_name, f"firmware/{uf2_name}"]

        for candidate in candidates:
            if candidate in zf.namelist():
                data = zf.read(candidate)
                out_path = device_dir / uf2_name
                out_path.write_bytes(data)
                logger.info("Extracted nRF52 UF2 for %s v%s", hw_model, version)
                return DownloadResult(
                    success=True, firmware_dir=device_dir,
                    version=version, hw_model=hw_model,
                    message="Extracted UF2 file",
                )

        return DownloadResult(
            success=False, firmware_dir=device_dir,
            version=version, hw_model=hw_model,
            message=f"UF2 file not found in ZIP: {uf2_name}",
        )

    def verify_checksum(self, file_path: Path, expected_sha256: str) -> bool:
        """Verify SHA256 checksum of a firmware file."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        actual = sha256.hexdigest()
        if actual != expected_sha256:
            logger.warning(
                "Checksum mismatch for %s: expected %s, got %s",
                file_path.name, expected_sha256[:16], actual[:16],
            )
            return False
        return True

    def clean_cache(self, keep_versions: int = 2) -> int:
        """Remove old cached firmware, keeping the N most recent versions."""
        if not self.cache_dir.exists():
            return 0

        version_dirs = sorted(
            [d for d in self.cache_dir.iterdir() if d.is_dir()],
            key=lambda d: d.name,
            reverse=True,
        )

        removed = 0
        for vdir in version_dirs[keep_versions:]:
            import shutil

            shutil.rmtree(vdir, ignore_errors=True)
            removed += 1
            logger.info("Cleaned cached firmware: %s", vdir.name)

        # Also clean old ZIP files
        for zf in self.cache_dir.glob("firmware-*.zip"):
            zf.unlink(missing_ok=True)
            removed += 1

        return removed
