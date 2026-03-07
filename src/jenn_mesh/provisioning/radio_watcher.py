"""Radio auto-provisioning daemon — watches for USB radios and provisions them."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Meshtastic USB vendor IDs (hex)
MESHTASTIC_VIDS = {
    0x10C4,  # CP2102 (Silicon Labs) — Heltec V3, T-Beam
    0x1A86,  # CH9102 / CH340 (WCH) — Heltec clones, Nano G2
    0x0403,  # FTDI FT232 — Station G2, some T-Beams
}

# Hardware model strings returned by `meshtastic --info` → our HardwareModel keys
HW_MODEL_MAP: dict[str, str] = {
    "HELTEC_V3": "heltec_v3",
    "TLORA_V2_1_1P6": "heltec_v3",
    "TBEAM": "tbeam",
    "TBEAM_S3_CORE": "tbeam_s3",
    "RAK4631": "rak4631",
    "T_ECHO": "t_echo",
    "STATION_G2": "station_g2",
    "NANO_G2_ULTRA": "nano_g2",
}

# nRF52-based devices that cannot use esptool
NRF52_MODELS = {"rak4631", "t_echo"}


@dataclass
class WatcherConfig:
    """Configuration for RadioWatcher daemon."""

    poll_interval: int = 10
    default_role: str = "CLIENT"
    auto_flash: bool = True
    firmware_cache: str = ""
    db_path: str = ""
    max_retries: int = 3
    retry_backoff: tuple[int, ...] = (10, 30, 90)
    edge_priority: bool = True
    edge_health_url: str = "http://localhost:8080/mesh/status"

    @classmethod
    def from_env(cls) -> WatcherConfig:
        """Load configuration from environment variables."""
        return cls(
            poll_interval=int(os.environ.get("JENN_RADIO_POLL_INTERVAL", "10")),
            default_role=os.environ.get("JENN_RADIO_DEFAULT_ROLE", "CLIENT"),
            auto_flash=os.environ.get("JENN_RADIO_AUTO_FLASH", "true").lower() == "true",
            firmware_cache=os.environ.get("JENN_RADIO_FIRMWARE_CACHE", ""),
            db_path=os.environ.get("JENN_MESH_DB_PATH", ""),
            edge_priority=os.environ.get("JENN_RADIO_EDGE_PRIORITY", "true").lower() == "true",
            edge_health_url=os.environ.get(
                "JENN_RADIO_EDGE_HEALTH_URL", "http://localhost:8080/mesh/status"
            ),
        )


@dataclass
class ProvisionResult:
    """Result of a radio provisioning attempt."""

    success: bool
    port: str
    node_id: str = ""
    hw_model: str = ""
    firmware_version: str = ""
    message: str = ""
    duration: float = 0.0


class RadioWatcher:
    """Watches for USB Meshtastic radios and auto-provisions unregistered ones.

    Pipeline per detected device:
        1. Scan USB ports for Meshtastic VIDs
        2. Read node_id + hw_model via meshtastic --info
        3. Check MeshDatabase — if already registered, skip
        4. FirmwareTracker.is_safe_to_flash() gate
        5. FlashPipeline.erase_and_flash_esp32() (or warn for nRF52)
        6. Wait for reboot
        7. BenchProvisioner.apply_golden_config(role=default_role)
        8. Register in DB + log provisioning
    """

    def __init__(
        self,
        config: WatcherConfig,
        db: object,  # MeshDatabase — lazy import to avoid circular deps
        firmware_tracker: object,  # FirmwareTracker
        flash_pipeline: object,  # FlashPipeline
        bench_provisioner: object,  # BenchProvisioner
    ):
        self.config = config
        self.db = db
        self.firmware_tracker = firmware_tracker
        self.flash_pipeline = flash_pipeline
        self.bench = bench_provisioner
        self._known_ports: set[str] = set()
        self._running = False

    def _edge_needs_radio(self) -> bool:
        """Check if JennEdge is running but doesn't have a radio yet.

        Uses urllib (stdlib) to query JennEdge's health API so this works
        even when httpx is not installed.  Returns True when JennMesh should
        yield priority — i.e. JennEdge is up but its radio state is not
        'connected'.  Returns False (proceed normally) when JennEdge is not
        running or already has its radio.
        """
        if not self.config.edge_priority:
            return False
        try:
            import json
            import urllib.request

            req = urllib.request.Request(self.config.edge_health_url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
            state = data.get("state", "disconnected")
            if state == "connected":
                return False  # JennEdge has its radio — we can proceed
            logger.info(
                "JennEdge running but radio state=%s — yielding priority",
                state,
            )
            return True
        except Exception:
            # JennEdge not running or unreachable — proceed normally
            return False

    def scan_ports(self) -> list[dict]:
        """Scan for Meshtastic USB serial devices.

        Returns list of dicts with 'port', 'vid', 'pid', 'description'.
        Uses pyserial's cross-platform comports() for Linux/macOS/Windows.
        """
        try:
            from serial.tools.list_ports import comports
        except ImportError:
            logger.error("pyserial not installed — cannot scan USB ports")
            return []

        devices = []
        for info in comports():
            if info.vid is not None and info.vid in MESHTASTIC_VIDS:
                devices.append(
                    {
                        "port": info.device,
                        "vid": info.vid,
                        "pid": info.pid,
                        "description": info.description or "",
                    }
                )
        return devices

    def is_port_in_use(self, port: str) -> bool:
        """Check if a serial port is already opened by another process.

        Prevents conflicts with jenn-mesh-agent which holds the port open.
        """
        try:
            import serial

            s = serial.Serial()
            s.port = port
            s.open()
            s.close()
            return False
        except (serial.SerialException, OSError):
            return True
        except ImportError:
            return False

    def read_device_info(self, port: str) -> Optional[dict]:
        """Read node_id and hw_model from a connected Meshtastic device.

        Returns dict with 'node_id', 'hw_model', 'firmware_version' or None.
        """
        cmd = ["meshtastic", "--port", port, "--info"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                logger.warning("meshtastic --info failed on %s: %s", port, result.stderr[:200])
                return None

            info: dict[str, str] = {}
            for line in result.stdout.splitlines():
                line_lower = line.lower()
                if "node number:" in line_lower:
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        info["node_id"] = parts[1].strip()
                elif "hardware:" in line_lower or "hw model:" in line_lower:
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        raw = parts[1].strip()
                        info["hw_model"] = HW_MODEL_MAP.get(raw, raw.lower())
                elif "firmware version:" in line_lower or "firmware:" in line_lower:
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        ver = parts[1].strip()
                        # Strip leading 'v' if present
                        info["firmware_version"] = ver.lstrip("v")

            return info if info.get("node_id") else None
        except FileNotFoundError:
            logger.error("meshtastic CLI not found — install with: pip install meshtastic")
            return None
        except subprocess.TimeoutExpired:
            logger.warning("meshtastic --info timed out on %s", port)
            return None

    def is_registered(self, node_id: str) -> bool:
        """Check if a device is already registered in the mesh database."""
        device = self.db.get_device(node_id)
        return device is not None

    def provision_device(self, port: str) -> ProvisionResult:
        """Run the full provisioning pipeline on a single device.

        Steps: read info → check DB → flash firmware → apply config → register.
        """
        from jenn_mesh.models.device import DeviceRole
        from jenn_mesh.provisioning.firmware import DEFAULT_LATEST_VERSIONS

        start = time.monotonic()

        # Step 1: Read device info
        info = self.read_device_info(port)
        if info is None:
            self.db.log_provisioning(
                node_id="",
                action="provision_failed",
                operator="radio-watcher",
                details=f"Could not read device info on {port}",
            )
            return ProvisionResult(success=False, port=port, message="Could not read device info")

        node_id = info.get("node_id", "")
        hw_model = info.get("hw_model", "unknown")
        current_fw = info.get("firmware_version", "unknown")
        logger.info(
            "Detected radio on %s: node=%s hw=%s fw=%s",
            port,
            node_id,
            hw_model,
            current_fw,
        )

        # Step 2: Check if already registered
        if node_id and self.is_registered(node_id):
            logger.info("Radio %s already registered — skipping", node_id)
            self._known_ports.add(port)
            return ProvisionResult(
                success=True,
                port=port,
                node_id=node_id,
                hw_model=hw_model,
                message="Already registered",
            )

        # Step 3: Flash firmware (if auto_flash enabled)
        flash_result = None
        if self.config.auto_flash:
            target_version = DEFAULT_LATEST_VERSIONS.get(hw_model, "2.5.6")

            # nRF52 devices can't use esptool
            if hw_model in NRF52_MODELS:
                logger.warning(
                    "Radio %s is nRF52 (%s) — cannot auto-flash. "
                    "Use manual bench provisioning with UF2.",
                    node_id,
                    hw_model,
                )
            else:
                # Safety gate
                if not self.firmware_tracker.is_safe_to_flash(hw_model, target_version):
                    logger.warning(
                        "Firmware %s not marked COMPATIBLE for %s — skipping flash",
                        target_version,
                        hw_model,
                    )
                else:
                    logger.info(
                        "Flashing %s with firmware %s...",
                        hw_model,
                        target_version,
                    )
                    self.db.log_provisioning(
                        node_id=node_id,
                        action="erase_started",
                        operator="radio-watcher",
                        details=f"hw={hw_model} target={target_version} port={port}",
                    )
                    flash_result = self._flash_with_retry(port, hw_model, target_version)
                    if flash_result and not flash_result.success:
                        self.db.log_provisioning(
                            node_id=node_id,
                            action="provision_failed",
                            operator="radio-watcher",
                            details=f"Flash failed: {flash_result.message}",
                        )
                        return ProvisionResult(
                            success=False,
                            port=port,
                            node_id=node_id,
                            hw_model=hw_model,
                            message=f"Flash failed: {flash_result.message}",
                            duration=time.monotonic() - start,
                        )

                    # Wait for device reboot after flash
                    logger.info("Waiting for device reboot...")
                    time.sleep(5)

                    # Re-read device info after flash (node_id may change)
                    info = self.read_device_info(port)
                    if info:
                        node_id = info.get("node_id", node_id)
                        current_fw = info.get("firmware_version", current_fw)

        # Step 4: Apply golden config
        role = DeviceRole.from_meshtastic(self.config.default_role)
        logger.info("Applying golden config (role=%s) to %s...", role.value, node_id)
        config_result = self.bench.apply_golden_config(role=role, port=port)

        if not config_result.success:
            self.db.log_provisioning(
                node_id=node_id,
                action="provision_failed",
                operator="radio-watcher",
                details=f"Config failed: {config_result.message}",
            )
            return ProvisionResult(
                success=False,
                port=port,
                node_id=node_id,
                hw_model=hw_model,
                message=f"Config failed: {config_result.message}",
                duration=time.monotonic() - start,
            )

        self.db.log_provisioning(
            node_id=node_id,
            action="config_applied",
            role=role.value,
            operator="radio-watcher",
            details=f"role={role.value} port={port}",
        )

        # Step 5: Log provisioning complete
        self.db.log_provisioning(
            node_id=node_id,
            action="provision_complete",
            role=role.value,
            operator="radio-watcher",
            details=(
                f"hw={hw_model} fw={current_fw} port={port}"
                f"{' flashed' if flash_result and flash_result.success else ''}"
            ),
        )

        self._known_ports.add(port)
        duration = time.monotonic() - start
        logger.info(
            "Successfully provisioned %s as %s (%.1fs)",
            node_id,
            role.value,
            duration,
        )

        return ProvisionResult(
            success=True,
            port=port,
            node_id=node_id,
            hw_model=hw_model,
            firmware_version=current_fw,
            message=f"Provisioned as {role.value}",
            duration=duration,
        )

    def _flash_with_retry(
        self, port: str, hw_model: str, target_version: str
    ) -> Optional[ProvisionResult]:
        """Flash firmware with retry + exponential backoff."""
        backoffs = self.config.retry_backoff
        for attempt in range(self.config.max_retries):
            try:
                result = self.flash_pipeline.erase_and_flash(
                    port=port, hw_model=hw_model, target_version=target_version
                )
                if result.success:
                    return ProvisionResult(
                        success=True,
                        port=port,
                        hw_model=hw_model,
                        firmware_version=target_version,
                        message="Flash successful",
                    )
                logger.warning(
                    "Flash attempt %d/%d failed: %s",
                    attempt + 1,
                    self.config.max_retries,
                    result.message,
                )
            except Exception as e:
                logger.warning(
                    "Flash attempt %d/%d error: %s",
                    attempt + 1,
                    self.config.max_retries,
                    e,
                )

            if attempt < self.config.max_retries - 1:
                delay = backoffs[min(attempt, len(backoffs) - 1)]
                logger.info("Retrying flash in %ds...", delay)
                time.sleep(delay)

        return ProvisionResult(
            success=False,
            port=port,
            hw_model=hw_model,
            message=f"Flash failed after {self.config.max_retries} attempts",
        )

    def poll_once(self) -> list[ProvisionResult]:
        """Run a single poll cycle: scan ports → provision new radios."""
        if self._edge_needs_radio():
            logger.debug("Yielding to JennEdge radio priority")
            self.db.log_provisioning(
                node_id="",
                action="edge_yield",
                operator="radio-watcher",
                details="Yielding radio priority to JennEdge",
            )
            return []

        results = []
        devices = self.scan_ports()

        for device in devices:
            port = device["port"]

            # Skip already-known ports
            if port in self._known_ports:
                continue

            # Skip ports in use by mesh-agent
            if self.is_port_in_use(port):
                logger.debug("Port %s in use — skipping (mesh-agent may hold it)", port)
                continue

            logger.info("New radio detected on %s (VID=0x%04X)", port, device["vid"])
            self.db.log_provisioning(
                node_id="",
                action="radio_detected",
                operator="radio-watcher",
                details=f"port={port} vid=0x{device['vid']:04X}",
            )
            result = self.provision_device(port)
            results.append(result)

        # Prune known_ports for disconnected devices
        current_ports = {d["port"] for d in devices}
        removed = self._known_ports - current_ports
        if removed:
            self._known_ports -= removed
            logger.info("Removed disconnected ports: %s", removed)

        return results

    def run(self) -> None:
        """Main daemon loop — poll until stopped."""
        self._running = True
        logger.info(
            "RadioWatcher started (poll_interval=%ds, role=%s, auto_flash=%s)",
            self.config.poll_interval,
            self.config.default_role,
            self.config.auto_flash,
        )

        while self._running:
            try:
                self.poll_once()
            except Exception:
                logger.exception("Error during poll cycle")

            # Sleep in 1s increments so we can respond to stop quickly
            for _ in range(self.config.poll_interval):
                if not self._running:
                    break
                time.sleep(1)

        logger.info("RadioWatcher stopped")

    def stop(self) -> None:
        """Signal the daemon to stop gracefully."""
        self._running = False
