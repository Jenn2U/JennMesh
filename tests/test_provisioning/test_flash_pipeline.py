"""Tests for FlashPipeline — esptool erase/flash operations."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jenn_mesh.provisioning.flash_pipeline import (
    DEFAULT_BAUD_RATE,
    ESP32_FLASH_MAP,
    ERASE_TIMEOUT,
    FLASH_TIMEOUT,
    FlashPipeline,
    FlashResult,
)

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def firmware_dir(tmp_path: Path) -> Path:
    """Create a temporary firmware directory with all required files."""
    fw_dir = tmp_path / "firmware"
    fw_dir.mkdir()
    for _, filename in ESP32_FLASH_MAP:
        (fw_dir / filename).write_bytes(b"\x00" * 100)
    return fw_dir


@pytest.fixture
def mock_downloader(firmware_dir):
    dl = MagicMock()
    dl.download_firmware.return_value = MagicMock(
        success=True, firmware_dir=firmware_dir, message="Cached"
    )
    return dl


@pytest.fixture
def pipeline(mock_downloader) -> FlashPipeline:
    return FlashPipeline(firmware_downloader=mock_downloader)


# ── Erase Flash Tests ──────────────────────────────────────────────


class TestEraseFlash:
    def test_erase_success(self, pipeline):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
            result = pipeline.erase_flash("/dev/ttyUSB0")
        assert result.success is True
        assert "Erased" in result.message
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "erase_flash" in cmd
        assert "/dev/ttyUSB0" in cmd

    def test_erase_failure(self, pipeline):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Chip not found")
            result = pipeline.erase_flash("/dev/ttyUSB0")
        assert result.success is False
        assert "Chip not found" in result.message

    def test_erase_timeout(self, pipeline):
        with patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("esptool", ERASE_TIMEOUT)
        ):
            result = pipeline.erase_flash("/dev/ttyUSB0")
        assert result.success is False
        assert "timed out" in result.message

    def test_erase_esptool_not_found(self, pipeline):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = pipeline.erase_flash("/dev/ttyUSB0")
        assert result.success is False
        assert "esptool" in result.message.lower()


# ── Write Flash Tests ──────────────────────────────────────────────


class TestWriteFlash:
    def test_write_success(self, pipeline, firmware_dir):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
            result = pipeline.write_flash("/dev/ttyUSB0", firmware_dir)
        assert result.success is True
        cmd = mock_run.call_args[0][0]
        assert "write_flash" in cmd
        assert str(DEFAULT_BAUD_RATE) in cmd
        # Verify all 5 flash addresses are present
        assert "0x0" in cmd
        assert "0x10000" in cmd
        assert "0x300000" in cmd

    def test_write_failure(self, pipeline, firmware_dir):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Write error")
            result = pipeline.write_flash("/dev/ttyUSB0", firmware_dir)
        assert result.success is False

    def test_write_missing_file(self, pipeline, firmware_dir):
        (firmware_dir / "firmware.bin").unlink()
        result = pipeline.write_flash("/dev/ttyUSB0", firmware_dir)
        assert result.success is False
        assert "Missing firmware file" in result.message

    def test_write_timeout(self, pipeline, firmware_dir):
        with patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("esptool", FLASH_TIMEOUT)
        ):
            result = pipeline.write_flash("/dev/ttyUSB0", firmware_dir)
        assert result.success is False
        assert "timed out" in result.message

    def test_write_esptool_not_found(self, pipeline, firmware_dir):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = pipeline.write_flash("/dev/ttyUSB0", firmware_dir)
        assert result.success is False


# ── Verify Flash Tests ─────────────────────────────────────────────


class TestVerifyFlash:
    def test_verify_success(self, pipeline):
        output = "Owner: Test\nFirmware version: 2.5.6\nHardware: HELTEC_V3\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            with patch("time.sleep"):
                result = pipeline.verify_flash("/dev/ttyUSB0", "2.5.6")
        assert result is True

    def test_verify_version_mismatch(self, pipeline):
        output = "Firmware version: 2.5.0\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            with patch("time.sleep"):
                result = pipeline.verify_flash("/dev/ttyUSB0", "2.5.6")
        assert result is False

    def test_verify_no_version_in_output(self, pipeline):
        output = "Owner: Test\nHardware: HELTEC_V3\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            with patch("time.sleep"):
                result = pipeline.verify_flash("/dev/ttyUSB0", "2.5.6")
        assert result is False

    def test_verify_failure(self, pipeline):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            with patch("time.sleep"):
                result = pipeline.verify_flash("/dev/ttyUSB0", "2.5.6")
        assert result is False

    def test_verify_timeout(self, pipeline):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("meshtastic", 15)):
            with patch("time.sleep"):
                result = pipeline.verify_flash("/dev/ttyUSB0", "2.5.6")
        assert result is False


# ── Full Pipeline Tests ────────────────────────────────────────────


class TestEraseAndFlash:
    def test_full_pipeline_success(self, pipeline, firmware_dir, mock_downloader):
        with patch("subprocess.run") as mock_run:
            # erase, write, verify calls
            mock_run.return_value = MagicMock(
                returncode=0, stdout="Firmware version: 2.5.6\n", stderr=""
            )
            with patch("time.sleep"):
                result = pipeline.erase_and_flash("/dev/ttyUSB0", "heltec_v3", "2.5.6")

        assert result.success is True
        assert result.hw_model == "heltec_v3"
        assert result.firmware_version == "2.5.6"
        assert result.duration > 0
        mock_downloader.download_firmware.assert_called_once_with("heltec_v3", "2.5.6")

    def test_pipeline_download_failure(self, pipeline, mock_downloader):
        mock_downloader.download_firmware.return_value = MagicMock(
            success=False, firmware_dir=None, message="HTTP 404"
        )
        result = pipeline.erase_and_flash("/dev/ttyUSB0", "heltec_v3", "2.5.6")
        assert result.success is False
        assert "download failed" in result.message.lower()

    def test_pipeline_erase_failure(self, pipeline, firmware_dir):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Chip error")
            result = pipeline.erase_and_flash("/dev/ttyUSB0", "heltec_v3", "2.5.6")
        assert result.success is False
        assert "Erase failed" in result.message

    def test_nrf52_returns_unsupported(self, pipeline):
        result = pipeline.erase_and_flash("/dev/ttyUSB0", "rak4631", "2.5.6")
        assert result.success is False
        assert "nRF52" in result.message

    def test_nrf52_flash_placeholder(self, pipeline):
        result = pipeline.flash_nrf52("/dev/ttyUSB0", "t_echo", "2.5.6")
        assert result.success is False
        assert "UF2" in result.message


# ── Constants Tests ─────────────────────────────────────────────────


class TestConstants:
    def test_esp32_flash_map_has_5_entries(self):
        assert len(ESP32_FLASH_MAP) == 5

    def test_flash_map_addresses(self):
        addresses = [addr for addr, _ in ESP32_FLASH_MAP]
        assert "0x0" in addresses
        assert "0x8000" in addresses
        assert "0xe000" in addresses
        assert "0x10000" in addresses
        assert "0x300000" in addresses

    def test_flash_map_files(self):
        files = [f for _, f in ESP32_FLASH_MAP]
        assert "bleota.bin" in files
        assert "firmware.bin" in files
        assert "littlefs.bin" in files
