"""Tests for FirmwareDownloader — GitHub release download + cache management."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jenn_mesh.provisioning.firmware_downloader import (
    ESP32_FLASH_FILES,
    FIRMWARE_ASSET_MAP,
    GITHUB_RELEASE_URL,
    NRF52_ASSET_MAP,
    FirmwareDownloader,
    _default_cache_dir,
)

# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    d = tmp_path / "firmware_cache"
    d.mkdir()
    return d


@pytest.fixture
def downloader(cache_dir: Path) -> FirmwareDownloader:
    return FirmwareDownloader(cache_dir=str(cache_dir))


@pytest.fixture
def esp32_zip(tmp_path: Path) -> Path:
    """Create a fake firmware ZIP with ESP32 files for heltec_v3."""
    zip_path = tmp_path / "firmware-2.5.6.zip"
    prefix = "firmware-heltec-v3-2.5.6"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for fname in ESP32_FLASH_FILES:
            zf.writestr(f"{prefix}/{fname}", b"\x00" * 100)
    return zip_path


@pytest.fixture
def nrf52_zip(tmp_path: Path) -> Path:
    """Create a fake firmware ZIP with nRF52 UF2 for rak4631."""
    zip_path = tmp_path / "firmware-2.5.6-nrf.zip"
    uf2_name = NRF52_ASSET_MAP["rak4631"].format(version="2.5.6")
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(uf2_name, b"\xaa" * 200)
    return zip_path


# ── Cache Hit Tests ─────────────────────────────────────────────────


class TestCacheHit:
    def test_esp32_cache_hit(self, downloader, cache_dir):
        """Cached ESP32 firmware returns path without download."""
        device_dir = cache_dir / "2.5.6" / "heltec_v3"
        device_dir.mkdir(parents=True)
        for fname in ESP32_FLASH_FILES:
            (device_dir / fname).write_bytes(b"\x00" * 50)

        result = downloader.download_firmware("heltec_v3", "2.5.6")
        assert result.success is True
        assert result.firmware_dir == device_dir
        assert result.message == "Cached"

    def test_esp32_cache_incomplete(self, downloader, cache_dir):
        """Incomplete cache (missing file) does NOT return cached path."""
        device_dir = cache_dir / "2.5.6" / "heltec_v3"
        device_dir.mkdir(parents=True)
        # Only write 3 of 5 files
        for fname in ESP32_FLASH_FILES[:3]:
            (device_dir / fname).write_bytes(b"\x00" * 50)

        path = downloader.get_firmware_path("heltec_v3", "2.5.6")
        assert path is None

    def test_nrf52_cache_hit(self, downloader, cache_dir):
        """Cached nRF52 UF2 returns path."""
        device_dir = cache_dir / "2.5.6" / "rak4631"
        device_dir.mkdir(parents=True)
        uf2_name = NRF52_ASSET_MAP["rak4631"].format(version="2.5.6")
        (device_dir / uf2_name).write_bytes(b"\xaa" * 100)

        path = downloader.get_firmware_path("rak4631", "2.5.6")
        assert path == device_dir

    def test_nrf52_cache_missing_uf2(self, downloader, cache_dir):
        """nRF52 cache dir exists but UF2 file is missing."""
        device_dir = cache_dir / "2.5.6" / "rak4631"
        device_dir.mkdir(parents=True)

        path = downloader.get_firmware_path("rak4631", "2.5.6")
        assert path is None

    def test_no_cache_dir(self, downloader):
        """Non-existent version returns None."""
        path = downloader.get_firmware_path("heltec_v3", "9.9.9")
        assert path is None


# ── Download Tests ──────────────────────────────────────────────────


class TestDownload:
    def test_download_success(self, downloader, cache_dir):
        """Successful download writes ZIP and extracts files."""
        prefix = "firmware-heltec-v3-2.5.6"
        zip_data = _make_esp32_zip(prefix)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_bytes.return_value = [zip_data]
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        mock_httpx = MagicMock()
        mock_httpx.stream.return_value = mock_resp

        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            result = downloader.download_firmware("heltec_v3", "2.5.6")

        assert result.success is True
        assert result.firmware_dir is not None
        for fname in ESP32_FLASH_FILES:
            assert (result.firmware_dir / fname).exists()

    def test_download_http_error(self, downloader):
        """HTTP 404 returns failure."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        mock_httpx = MagicMock()
        mock_httpx.stream.return_value = mock_resp

        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            result = downloader.download_firmware("heltec_v3", "2.5.6")

        assert result.success is False
        assert "404" in result.message

    def test_download_httpx_missing(self, downloader):
        """Missing httpx returns clear error."""
        # Make `import httpx` raise ImportError inside download_firmware
        with patch.dict("sys.modules", {"httpx": None}):
            result = downloader.download_firmware("heltec_v3", "2.5.6")

        assert result.success is False
        assert "httpx" in result.message.lower()

    def test_download_network_error(self, downloader):
        """Network error cleans up partial ZIP and returns failure."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_bytes.side_effect = ConnectionError("Network down")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        mock_httpx = MagicMock()
        mock_httpx.stream.return_value = mock_resp

        with patch.dict("sys.modules", {"httpx": mock_httpx}):
            result = downloader.download_firmware("heltec_v3", "2.5.6")

        assert result.success is False
        assert "Download error" in result.message


# ── ZIP Extraction Tests ────────────────────────────────────────────


class TestExtraction:
    def test_esp32_extraction(self, downloader, esp32_zip):
        """ESP32 ZIP extracts all 5 binaries."""
        result = downloader._extract_device_firmware(esp32_zip, "heltec_v3", "2.5.6")
        assert result.success is True
        assert result.firmware_dir is not None
        for fname in ESP32_FLASH_FILES:
            assert (result.firmware_dir / fname).exists()

    def test_nrf52_extraction(self, downloader, nrf52_zip):
        """nRF52 ZIP extracts UF2 file."""
        result = downloader._extract_device_firmware(nrf52_zip, "rak4631", "2.5.6")
        assert result.success is True
        uf2_name = NRF52_ASSET_MAP["rak4631"].format(version="2.5.6")
        assert (result.firmware_dir / uf2_name).exists()

    def test_corrupt_zip(self, downloader, tmp_path):
        """Corrupt ZIP file is deleted and returns error."""
        bad_zip = tmp_path / "bad.zip"
        bad_zip.write_bytes(b"not a zip file")

        result = downloader._extract_device_firmware(bad_zip, "heltec_v3", "2.5.6")
        assert result.success is False
        assert "Corrupt" in result.message
        assert not bad_zip.exists()  # Should be cleaned up

    def test_missing_files_in_zip(self, downloader, tmp_path):
        """ZIP with only 2 of 5 ESP32 files returns failure."""
        zip_path = tmp_path / "incomplete.zip"
        prefix = "firmware-heltec-v3-2.5.6"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr(f"{prefix}/firmware.bin", b"\x00" * 50)
            zf.writestr(f"{prefix}/partitions.bin", b"\x00" * 50)

        result = downloader._extract_device_firmware(zip_path, "heltec_v3", "2.5.6")
        assert result.success is False
        assert "Missing" in result.message

    def test_unknown_hw_model(self, downloader, esp32_zip):
        """Unknown hardware model returns no-mapping error."""
        result = downloader._extract_device_firmware(esp32_zip, "unknown_board", "2.5.6")
        assert result.success is False
        assert "No firmware asset mapping" in result.message

    def test_nrf52_uf2_not_in_zip(self, downloader, tmp_path):
        """nRF52 ZIP without expected UF2 returns failure."""
        zip_path = tmp_path / "empty.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("unrelated.txt", "nothing here")

        result = downloader._extract_device_firmware(zip_path, "rak4631", "2.5.6")
        assert result.success is False
        assert "UF2 file not found" in result.message


# ── Checksum Tests ──────────────────────────────────────────────────


class TestChecksum:
    def test_checksum_match(self, downloader, tmp_path):
        data = b"test firmware data"
        path = tmp_path / "test.bin"
        path.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()

        assert downloader.verify_checksum(path, expected) is True

    def test_checksum_mismatch(self, downloader, tmp_path):
        path = tmp_path / "test.bin"
        path.write_bytes(b"real data")

        assert downloader.verify_checksum(path, "0" * 64) is False


# ── Cache Cleanup Tests ─────────────────────────────────────────────


class TestCleanCache:
    def test_clean_keeps_recent(self, downloader, cache_dir):
        """Keeps the N most recent versions, removes older ones."""
        for v in ["2.5.3", "2.5.4", "2.5.5", "2.5.6"]:
            (cache_dir / v).mkdir()

        removed = downloader.clean_cache(keep_versions=2)
        assert removed >= 2  # At least 2 old version dirs
        assert (cache_dir / "2.5.6").exists()
        assert (cache_dir / "2.5.5").exists()
        assert not (cache_dir / "2.5.3").exists()
        assert not (cache_dir / "2.5.4").exists()

    def test_clean_removes_zips(self, downloader, cache_dir):
        """Also removes leftover ZIP files."""
        (cache_dir / "firmware-2.5.6.zip").write_bytes(b"zip")
        (cache_dir / "firmware-2.5.5.zip").write_bytes(b"zip")

        removed = downloader.clean_cache(keep_versions=10)
        assert removed >= 2  # ZIP files
        assert not list(cache_dir.glob("firmware-*.zip"))

    def test_clean_empty_cache(self, tmp_path):
        """Clean on non-existent cache dir returns 0."""
        dl = FirmwareDownloader(cache_dir=str(tmp_path / "nonexistent"))
        # The constructor creates it, so make sure it's empty
        removed = dl.clean_cache()
        assert removed == 0


# ── Platform Cache Dir Tests ────────────────────────────────────────


class TestDefaultCacheDir:
    def test_linux_cache_dir(self):
        with patch(
            "jenn_mesh.provisioning.firmware_downloader.platform.system", return_value="Linux"
        ):
            d = _default_cache_dir()
        assert d == Path("/var/lib/jenn-mesh/firmware")

    def test_darwin_cache_dir(self):
        with patch(
            "jenn_mesh.provisioning.firmware_downloader.platform.system", return_value="Darwin"
        ):
            d = _default_cache_dir()
        assert "Library" in str(d)
        assert "JennMesh" in str(d)

    def test_windows_cache_dir(self):
        with patch(
            "jenn_mesh.provisioning.firmware_downloader.platform.system", return_value="Windows"
        ):
            with patch.dict("os.environ", {"APPDATA": "/fake/appdata"}):
                d = _default_cache_dir()
        assert "JennMesh" in str(d)

    def test_unknown_os_fallback(self):
        with patch(
            "jenn_mesh.provisioning.firmware_downloader.platform.system", return_value="FreeBSD"
        ):
            d = _default_cache_dir()
        assert d == Path("/var/lib/jenn-mesh/firmware")


# ── Constants Tests ─────────────────────────────────────────────────


class TestConstants:
    def test_esp32_flash_files_count(self):
        assert len(ESP32_FLASH_FILES) == 5

    def test_firmware_asset_map_models(self):
        assert "heltec_v3" in FIRMWARE_ASSET_MAP
        assert "tbeam" in FIRMWARE_ASSET_MAP

    def test_nrf52_asset_map_models(self):
        assert "rak4631" in NRF52_ASSET_MAP
        assert "t_echo" in NRF52_ASSET_MAP

    def test_github_url_format(self):
        url = GITHUB_RELEASE_URL.format(version="2.5.6")
        assert "2.5.6" in url
        assert "github.com/meshtastic" in url


# ── Helpers ──────────────────────────────────────────────────────────


def _make_esp32_zip(prefix: str) -> bytes:
    """Create an in-memory ZIP with ESP32 firmware files."""
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fname in ESP32_FLASH_FILES:
            zf.writestr(f"{prefix}/{fname}", b"\x00" * 100)
    return buf.getvalue()
