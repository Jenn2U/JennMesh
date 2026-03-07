"""Tests for RadioWatcher daemon — port scanning, provisioning, retry logic."""

from __future__ import annotations

import http.server
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jenn_mesh.provisioning.radio_watcher import (
    MESHTASTIC_VIDS,
    NRF52_MODELS,
    HW_MODEL_MAP,
    ProvisionResult,
    RadioWatcher,
    WatcherConfig,
)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def config() -> WatcherConfig:
    return WatcherConfig(
        poll_interval=1,
        default_role="CLIENT",
        auto_flash=True,
        max_retries=2,
        retry_backoff=(0, 0),  # No delay in tests
    )


@pytest.fixture
def mock_db(populated_db):
    return populated_db


@pytest.fixture
def mock_firmware_tracker(mock_db):
    from jenn_mesh.provisioning.firmware import FirmwareTracker

    tracker = FirmwareTracker(mock_db)
    tracker.seed_compatibility_matrix()
    return tracker


@pytest.fixture
def mock_flash_pipeline():
    pipeline = MagicMock()
    pipeline.erase_and_flash.return_value = MagicMock(
        success=True, message="Flash successful"
    )
    return pipeline


@pytest.fixture
def mock_bench():
    from jenn_mesh.provisioning.bench_flash import ProvisioningResult

    bench = MagicMock()
    bench.apply_golden_config.return_value = ProvisioningResult(
        success=True, node_id="!new12345", role="CLIENT",
        config_hash="abc123", message="OK",
    )
    return bench


@pytest.fixture
def watcher(config, mock_db, mock_firmware_tracker, mock_flash_pipeline, mock_bench):
    return RadioWatcher(
        config=config,
        db=mock_db,
        firmware_tracker=mock_firmware_tracker,
        flash_pipeline=mock_flash_pipeline,
        bench_provisioner=mock_bench,
    )


def _make_port_info(device="/dev/ttyUSB0", vid=0x10C4, pid=0xEA60, desc="CP2102"):
    """Create a mock serial port info object."""
    info = MagicMock()
    info.device = device
    info.vid = vid
    info.pid = pid
    info.description = desc
    return info


# ── WatcherConfig Tests ─────────────────────────────────────────────


class TestWatcherConfig:
    def test_defaults(self):
        cfg = WatcherConfig()
        assert cfg.poll_interval == 10
        assert cfg.default_role == "CLIENT"
        assert cfg.auto_flash is True
        assert cfg.max_retries == 3
        assert cfg.retry_backoff == (10, 30, 90)

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("JENN_RADIO_POLL_INTERVAL", "5")
        monkeypatch.setenv("JENN_RADIO_DEFAULT_ROLE", "ROUTER")
        monkeypatch.setenv("JENN_RADIO_AUTO_FLASH", "false")
        cfg = WatcherConfig.from_env()
        assert cfg.poll_interval == 5
        assert cfg.default_role == "ROUTER"
        assert cfg.auto_flash is False

    def test_from_env_defaults(self, monkeypatch):
        # Clear any existing env vars
        for key in ["JENN_RADIO_POLL_INTERVAL", "JENN_RADIO_DEFAULT_ROLE", "JENN_RADIO_AUTO_FLASH"]:
            monkeypatch.delenv(key, raising=False)
        cfg = WatcherConfig.from_env()
        assert cfg.poll_interval == 10
        assert cfg.default_role == "CLIENT"
        assert cfg.auto_flash is True


# ── Port Scanning Tests ─────────────────────────────────────────────


class TestScanPorts:
    @patch("jenn_mesh.provisioning.radio_watcher.comports", create=True)
    def test_scan_finds_meshtastic_devices(self, mock_comports, watcher):
        with patch(
            "serial.tools.list_ports.comports",
            return_value=[
                _make_port_info("/dev/ttyUSB0", 0x10C4, 0xEA60, "CP2102"),
                _make_port_info("/dev/ttyUSB1", 0x1A86, 0x55D4, "CH9102"),
            ],
        ):
            devices = watcher.scan_ports()
        assert len(devices) == 2
        assert devices[0]["port"] == "/dev/ttyUSB0"
        assert devices[0]["vid"] == 0x10C4
        assert devices[1]["port"] == "/dev/ttyUSB1"

    def test_scan_ignores_non_meshtastic_devices(self, watcher):
        non_mesh_port = _make_port_info("/dev/ttyUSB2", 0x9999, 0x0001, "Other")
        with patch("serial.tools.list_ports.comports", return_value=[non_mesh_port]):
            devices = watcher.scan_ports()
        assert len(devices) == 0

    def test_scan_ignores_none_vid(self, watcher):
        no_vid_port = _make_port_info("/dev/ttyS0", None, None, "")
        no_vid_port.vid = None
        with patch("serial.tools.list_ports.comports", return_value=[no_vid_port]):
            devices = watcher.scan_ports()
        assert len(devices) == 0

    def test_scan_handles_import_error(self, watcher):
        # Make the lazy `from serial.tools.list_ports import comports` fail
        with patch.dict("sys.modules", {"serial": None, "serial.tools": None, "serial.tools.list_ports": None}):
            devices = watcher.scan_ports()
            assert devices == []

    def test_meshtastic_vids_are_correct(self):
        assert 0x10C4 in MESHTASTIC_VIDS  # CP2102
        assert 0x1A86 in MESHTASTIC_VIDS  # CH9102/CH340
        assert 0x0403 in MESHTASTIC_VIDS  # FTDI


# ── Port In Use Tests ───────────────────────────────────────────────


class TestIsPortInUse:
    def test_port_available(self, watcher):
        with patch("serial.Serial") as mock_serial:
            instance = mock_serial.return_value
            instance.open.return_value = None
            instance.close.return_value = None
            assert watcher.is_port_in_use("/dev/ttyUSB0") is False

    def test_port_busy(self, watcher):
        import serial

        with patch("serial.Serial") as mock_serial:
            instance = mock_serial.return_value
            instance.open.side_effect = serial.SerialException("Port busy")
            assert watcher.is_port_in_use("/dev/ttyUSB0") is True


# ── Device Info Tests ───────────────────────────────────────────────


class TestReadDeviceInfo:
    def test_reads_node_info(self, watcher):
        output = (
            "Owner: TestNode\n"
            "Node number: !abc12345\n"
            "Hardware: HELTEC_V3\n"
            "Firmware version: 2.5.6\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            info = watcher.read_device_info("/dev/ttyUSB0")
        assert info is not None
        assert info["node_id"] == "!abc12345"
        assert info["hw_model"] == "heltec_v3"
        assert info["firmware_version"] == "2.5.6"

    def test_returns_none_on_failure(self, watcher):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error")
            info = watcher.read_device_info("/dev/ttyUSB0")
        assert info is None

    def test_returns_none_on_timeout(self, watcher):
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="meshtastic", timeout=15)):
            info = watcher.read_device_info("/dev/ttyUSB0")
        assert info is None

    def test_returns_none_meshtastic_not_installed(self, watcher):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            info = watcher.read_device_info("/dev/ttyUSB0")
        assert info is None

    def test_strips_version_prefix(self, watcher):
        output = "Node number: !abc12345\nFirmware version: v2.5.6\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            info = watcher.read_device_info("/dev/ttyUSB0")
        assert info["firmware_version"] == "2.5.6"

    def test_hw_model_mapping(self, watcher):
        for raw, expected in HW_MODEL_MAP.items():
            output = f"Node number: !abc12345\nHardware: {raw}\n"
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
                info = watcher.read_device_info("/dev/ttyUSB0")
            assert info["hw_model"] == expected, f"HW_MODEL_MAP[{raw}] should be {expected}"


# ── Registration Check Tests ────────────────────────────────────────


class TestIsRegistered:
    def test_registered_device(self, watcher):
        assert watcher.is_registered("!aaa11111") is True

    def test_unregistered_device(self, watcher):
        assert watcher.is_registered("!zzz99999") is False


# ── Provision Device Tests ──────────────────────────────────────────


class TestProvisionDevice:
    def test_provisions_new_device(self, watcher, mock_flash_pipeline, mock_bench):
        output = "Node number: !new12345\nHardware: HELTEC_V3\nFirmware version: 2.5.6\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            with patch("time.sleep"):
                result = watcher.provision_device("/dev/ttyUSB0")

        assert result.success is True
        assert result.node_id == "!new12345"
        mock_flash_pipeline.erase_and_flash.assert_called_once()
        mock_bench.apply_golden_config.assert_called_once()

    def test_skips_registered_device(self, watcher, mock_flash_pipeline):
        # !aaa11111 is in populated_db
        output = "Node number: !aaa11111\nHardware: HELTEC_V3\nFirmware version: 2.5.6\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            result = watcher.provision_device("/dev/ttyUSB0")

        assert result.success is True
        assert result.message == "Already registered"
        mock_flash_pipeline.erase_and_flash.assert_not_called()

    def test_skips_flash_for_nrf52(self, watcher, mock_flash_pipeline):
        output = "Node number: !new12345\nHardware: RAK4631\nFirmware version: 2.5.6\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            with patch("time.sleep"):
                result = watcher.provision_device("/dev/ttyUSB0")

        # Flash should NOT be called for nRF52
        mock_flash_pipeline.erase_and_flash.assert_not_called()
        # Config should still be applied
        assert result.success is True

    def test_no_flash_when_disabled(self, watcher, config, mock_flash_pipeline):
        config.auto_flash = False
        output = "Node number: !new12345\nHardware: HELTEC_V3\nFirmware version: 2.5.6\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            with patch("time.sleep"):
                result = watcher.provision_device("/dev/ttyUSB0")

        mock_flash_pipeline.erase_and_flash.assert_not_called()
        assert result.success is True

    def test_returns_failure_on_bad_device_info(self, watcher):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error")
            result = watcher.provision_device("/dev/ttyUSB0")

        assert result.success is False
        assert "Could not read device info" in result.message

    def test_returns_failure_on_flash_failure(self, watcher, mock_flash_pipeline):
        mock_flash_pipeline.erase_and_flash.return_value = MagicMock(
            success=False, message="Erase timeout"
        )
        output = "Node number: !new12345\nHardware: HELTEC_V3\nFirmware version: 2.5.6\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            with patch("time.sleep"):
                result = watcher.provision_device("/dev/ttyUSB0")

        assert result.success is False
        assert "Flash failed" in result.message

    def test_returns_failure_on_config_failure(self, watcher, mock_bench):
        from jenn_mesh.provisioning.bench_flash import ProvisioningResult

        mock_bench.apply_golden_config.return_value = ProvisioningResult(
            success=False, message="Template not found"
        )
        output = "Node number: !new12345\nHardware: HELTEC_V3\nFirmware version: 2.5.6\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
            with patch("time.sleep"):
                result = watcher.provision_device("/dev/ttyUSB0")

        assert result.success is False
        assert "Config failed" in result.message


# ── Flash Retry Tests ───────────────────────────────────────────────


class TestFlashRetry:
    def test_retries_on_failure(self, watcher, mock_flash_pipeline):
        mock_flash_pipeline.erase_and_flash.side_effect = [
            MagicMock(success=False, message="Timeout"),
            MagicMock(success=True, message="OK"),
        ]
        with patch("time.sleep"):
            result = watcher._flash_with_retry("/dev/ttyUSB0", "heltec_v3", "2.5.6")
        assert result.success is True
        assert mock_flash_pipeline.erase_and_flash.call_count == 2

    def test_fails_after_max_retries(self, watcher, mock_flash_pipeline):
        mock_flash_pipeline.erase_and_flash.return_value = MagicMock(
            success=False, message="Timeout"
        )
        with patch("time.sleep"):
            result = watcher._flash_with_retry("/dev/ttyUSB0", "heltec_v3", "2.5.6")
        assert result.success is False
        assert mock_flash_pipeline.erase_and_flash.call_count == 2  # max_retries=2

    def test_retries_on_exception(self, watcher, mock_flash_pipeline):
        mock_flash_pipeline.erase_and_flash.side_effect = [
            RuntimeError("USB disconnect"),
            MagicMock(success=True, message="OK"),
        ]
        with patch("time.sleep"):
            result = watcher._flash_with_retry("/dev/ttyUSB0", "heltec_v3", "2.5.6")
        assert result.success is True


# ── Poll Cycle Tests ────────────────────────────────────────────────


class TestPollOnce:
    def test_provisions_new_port(self, watcher):
        port = _make_port_info("/dev/ttyUSB0", 0x10C4, 0xEA60)
        output = "Node number: !new12345\nHardware: HELTEC_V3\nFirmware version: 2.5.6\n"
        with patch("serial.tools.list_ports.comports", return_value=[port]):
            with patch.object(watcher, "is_port_in_use", return_value=False):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
                    with patch("time.sleep"):
                        results = watcher.poll_once()

        assert len(results) == 1

    def test_skips_known_port(self, watcher):
        watcher._known_ports.add("/dev/ttyUSB0")
        port = _make_port_info("/dev/ttyUSB0", 0x10C4, 0xEA60)
        with patch("serial.tools.list_ports.comports", return_value=[port]):
            results = watcher.poll_once()
        assert len(results) == 0

    def test_skips_busy_port(self, watcher):
        port = _make_port_info("/dev/ttyUSB0", 0x10C4, 0xEA60)
        with patch("serial.tools.list_ports.comports", return_value=[port]):
            with patch.object(watcher, "is_port_in_use", return_value=True):
                results = watcher.poll_once()
        assert len(results) == 0

    def test_prunes_disconnected_ports(self, watcher):
        watcher._known_ports.add("/dev/ttyUSB0")
        watcher._known_ports.add("/dev/ttyUSB1")
        # Only USB0 is present now
        port = _make_port_info("/dev/ttyUSB0", 0x10C4, 0xEA60)
        with patch("serial.tools.list_ports.comports", return_value=[port]):
            watcher.poll_once()
        assert "/dev/ttyUSB0" in watcher._known_ports
        assert "/dev/ttyUSB1" not in watcher._known_ports

    def test_empty_scan(self, watcher):
        with patch("serial.tools.list_ports.comports", return_value=[]):
            results = watcher.poll_once()
        assert len(results) == 0


# ── Daemon Run/Stop Tests ──────────────────────────────────────────


class TestRunStop:
    def test_stop_sets_flag(self, watcher):
        watcher.stop()
        assert watcher._running is False

    def test_run_respects_stop(self, watcher):
        """Verify run() exits after stop() is called."""
        call_count = 0

        def fake_poll():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                watcher.stop()
            return []

        with patch.object(watcher, "poll_once", side_effect=fake_poll):
            with patch("time.sleep"):
                watcher.run()

        assert call_count >= 2
        assert watcher._running is False


# ── Constants Tests ─────────────────────────────────────────────────


class TestConstants:
    def test_nrf52_models(self):
        assert "rak4631" in NRF52_MODELS
        assert "t_echo" in NRF52_MODELS
        assert "heltec_v3" not in NRF52_MODELS

    def test_hw_model_map_covers_common_models(self):
        assert "HELTEC_V3" in HW_MODEL_MAP
        assert "TBEAM" in HW_MODEL_MAP
        assert "RAK4631" in HW_MODEL_MAP


# ── Edge Priority Tests ────────────────────────────────────────


class _EdgeHealthHandler(http.server.BaseHTTPRequestHandler):
    """Tiny HTTP handler that returns a configurable JennEdge health JSON."""

    response_body = b'{"state": "connected"}'

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, *args):
        pass  # Suppress request logging in test output


class TestEdgePriority:
    def test_edge_connected_returns_false(self, watcher):
        """When JennEdge reports 'connected', watcher proceeds."""
        _EdgeHealthHandler.response_body = b'{"state": "connected"}'
        server = http.server.HTTPServer(("127.0.0.1", 0), _EdgeHealthHandler)
        port = server.server_address[1]
        watcher.config.edge_health_url = f"http://127.0.0.1:{port}/mesh/status"
        t = threading.Thread(target=server.handle_request, daemon=True)
        t.start()

        assert watcher._edge_needs_radio() is False
        server.server_close()

    def test_edge_disconnected_returns_true(self, watcher):
        """When JennEdge reports 'disconnected', watcher yields."""
        _EdgeHealthHandler.response_body = b'{"state": "disconnected"}'
        server = http.server.HTTPServer(("127.0.0.1", 0), _EdgeHealthHandler)
        port = server.server_address[1]
        watcher.config.edge_health_url = f"http://127.0.0.1:{port}/mesh/status"
        t = threading.Thread(target=server.handle_request, daemon=True)
        t.start()

        assert watcher._edge_needs_radio() is True
        server.server_close()

    def test_edge_unreachable_returns_false(self, watcher):
        """When JennEdge is not running, watcher proceeds normally."""
        watcher.config.edge_health_url = "http://127.0.0.1:1/nonexistent"
        assert watcher._edge_needs_radio() is False

    def test_edge_priority_disabled(self, watcher):
        """When edge_priority=False, always returns False."""
        watcher.config.edge_priority = False
        assert watcher._edge_needs_radio() is False

    def test_poll_yields_when_edge_needs_radio(self, watcher):
        """poll_once() returns empty when edge needs a radio."""
        with patch.object(watcher, "_edge_needs_radio", return_value=True):
            results = watcher.poll_once()
        assert results == []

    def test_poll_proceeds_when_edge_has_radio(self, watcher):
        """poll_once() scans ports when edge has its radio."""
        with patch.object(watcher, "_edge_needs_radio", return_value=False):
            with patch("serial.tools.list_ports.comports", return_value=[]):
                results = watcher.poll_once()
        assert results == []  # No devices, but scan happened


# ── WatcherConfig Edge Fields Tests ────────────────────────────


class TestWatcherConfigEdge:
    def test_edge_defaults(self):
        cfg = WatcherConfig()
        assert cfg.edge_priority is True
        assert cfg.edge_health_url == "http://localhost:8080/mesh/status"

    def test_edge_from_env(self, monkeypatch):
        monkeypatch.setenv("JENN_RADIO_EDGE_PRIORITY", "false")
        monkeypatch.setenv("JENN_RADIO_EDGE_HEALTH_URL", "http://custom:9090/health")
        cfg = WatcherConfig.from_env()
        assert cfg.edge_priority is False
        assert cfg.edge_health_url == "http://custom:9090/health"

    def test_edge_from_env_defaults(self, monkeypatch):
        monkeypatch.delenv("JENN_RADIO_EDGE_PRIORITY", raising=False)
        monkeypatch.delenv("JENN_RADIO_EDGE_HEALTH_URL", raising=False)
        cfg = WatcherConfig.from_env()
        assert cfg.edge_priority is True
        assert cfg.edge_health_url == "http://localhost:8080/mesh/status"


# ── Granular Provisioning Log Tests ────────────────────────────


class TestGranularLogs:
    def _get_log_actions(self, db) -> list[str]:
        """Get all provisioning log actions from the database."""
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT action FROM provisioning_log ORDER BY id"
            ).fetchall()
        return [r["action"] for r in rows]

    def test_radio_detected_logged(self, watcher, mock_db):
        """poll_once logs radio_detected when a new radio is found."""
        port = _make_port_info("/dev/ttyUSB0", 0x10C4, 0xEA60)
        output = "Node number: !new12345\nHardware: HELTEC_V3\nFirmware version: 2.5.6\n"
        with patch.object(watcher, "_edge_needs_radio", return_value=False):
            with patch("serial.tools.list_ports.comports", return_value=[port]):
                with patch.object(watcher, "is_port_in_use", return_value=False):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock(returncode=0, stdout=output, stderr="")
                        with patch("time.sleep"):
                            watcher.poll_once()

        assert "radio_detected" in self._get_log_actions(mock_db)

    def test_provision_failed_on_bad_info(self, watcher, mock_db):
        """provision_device logs provision_failed when device info can't be read."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error")
            watcher.provision_device("/dev/ttyUSB0")

        assert "provision_failed" in self._get_log_actions(mock_db)

    def test_edge_yield_logged(self, watcher, mock_db):
        """poll_once logs edge_yield when yielding to JennEdge."""
        with patch.object(watcher, "_edge_needs_radio", return_value=True):
            watcher.poll_once()

        assert "edge_yield" in self._get_log_actions(mock_db)
