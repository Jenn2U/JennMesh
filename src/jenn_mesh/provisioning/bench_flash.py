"""Bench provisioning — detect USB radio, apply golden config, register device."""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from jenn_mesh.core.channel_manager import ChannelManager
from jenn_mesh.core.config_manager import ConfigManager
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.device import ConfigHash, DeviceRole
from jenn_mesh.provisioning.security import SecuritySetup

logger = logging.getLogger(__name__)


@dataclass
class ProvisioningResult:
    """Result of a bench provisioning operation."""

    success: bool
    node_id: Optional[str] = None
    role: Optional[str] = None
    config_hash: Optional[str] = None
    message: str = ""


class BenchProvisioner:
    """Interactive bench flash tool for new Meshtastic radios."""

    def __init__(
        self,
        db: MeshDatabase,
        config_manager: ConfigManager,
        channel_manager: ChannelManager,
        security: SecuritySetup,
    ):
        self.db = db
        self.config_manager = config_manager
        self.channel_manager = channel_manager
        self.security = security

    def detect_serial_port(self) -> Optional[str]:
        """Auto-detect a connected Meshtastic radio's serial port."""
        try:
            result = subprocess.run(
                ["meshtastic", "--info"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                # The meshtastic CLI auto-detects; if it works, use default
                return "auto"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Try common serial ports
        common_ports = [
            "/dev/ttyUSB0",
            "/dev/ttyACM0",
            "/dev/tty.usbserial",
            "/dev/tty.usbmodem",
        ]
        for port in common_ports:
            if Path(port).exists():
                return port

        return None

    def read_current_config(self, port: str = "auto") -> Optional[str]:
        """Export the current config from a connected device."""
        cmd = ["meshtastic", "--export-config"]
        if port != "auto":
            cmd.extend(["--port", port])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return result.stdout
            logger.error("Failed to export config: %s", result.stderr)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.error("Cannot read device config: %s", e)
        return None

    def read_node_info(self, port: str = "auto") -> Optional[dict]:
        """Read basic node info (node_id, hardware, firmware) from device."""
        cmd = ["meshtastic", "--info"]
        if port != "auto":
            cmd.extend(["--port", port])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                info: dict = {}
                for line in result.stdout.splitlines():
                    if "Node number:" in line or "Owner:" in line or "Hardware:" in line:
                        parts = line.split(":", 1)
                        if len(parts) == 2:
                            info[parts[0].strip().lower()] = parts[1].strip()
                return info if info else None
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.error("Cannot read node info: %s", e)
        return None

    def apply_golden_config(
        self,
        role: DeviceRole,
        port: str = "auto",
        long_name: Optional[str] = None,
        short_name: Optional[str] = None,
    ) -> ProvisioningResult:
        """Apply a golden config template to a connected device.

        Steps:
            1. Load golden template for the specified role
            2. Inject fleet admin key and channel PSKs
            3. Apply via meshtastic --configure
            4. Set device name if provided
            5. Register in local SQLite registry
            6. Log provisioning action

        Args:
            role: Device role to provision as.
            port: Serial port (or "auto" for auto-detect).
            long_name: Optional device name to set.
            short_name: Optional 4-char short name.

        Returns:
            ProvisioningResult with success status and details.
        """
        # 1. Load golden template
        template_key = ConfigManager.role_to_filename(role)
        template_yaml = self.config_manager.get_template(template_key)
        if template_yaml is None:
            return ProvisioningResult(
                success=False,
                message=f"No golden config template found for role: {template_key}",
            )

        # 2. Inject admin key
        admin_key = self.security.load_admin_key()
        if admin_key:
            template_yaml = self.security.inject_admin_key_into_config(
                template_yaml, admin_key.public_key_b64
            )

        # 3. Inject channel PSKs
        template_yaml = self._inject_channel_psks(template_yaml)

        # 4. Write temp config and apply
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
            tmp.write(template_yaml)
            tmp_path = tmp.name

        try:
            cmd = ["meshtastic", "--configure", tmp_path]
            if port != "auto":
                cmd.extend(["--port", port])

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if result.returncode != 0:
                return ProvisioningResult(
                    success=False,
                    message=f"Failed to apply config: {result.stderr}",
                )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return ProvisioningResult(success=False, message=f"Cannot apply config: {e}")
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        # 5. Set device name
        if long_name:
            self._set_device_name(port, long_name, short_name)
            time.sleep(1)  # Allow device to process

        # 6. Read node info for registration
        node_info = self.read_node_info(port)
        node_id = node_info.get("node number", "unknown") if node_info else "unknown"
        config_hash = ConfigHash.compute(template_yaml)

        # 7. Register device
        self.db.upsert_device(
            node_id=node_id,
            long_name=long_name,
            short_name=short_name,
            role=role.value,
        )

        # 8. Update template assignment
        with self.db.connection() as conn:
            conn.execute(
                "UPDATE devices SET template_role = ?, template_hash = ?, config_hash = ? "
                "WHERE node_id = ?",
                (template_key, config_hash, config_hash, node_id),
            )

        # 9. Log provisioning
        self.db.log_provisioning(
            node_id=node_id,
            action="bench_provision",
            role=template_key,
            template_hash=config_hash,
            details=f"Role: {role.value}, Name: {long_name or 'unnamed'}",
        )

        return ProvisioningResult(
            success=True,
            node_id=node_id,
            role=role.value,
            config_hash=config_hash,
            message=f"Successfully provisioned {node_id} as {role.value}",
        )

    def verify_config(self, port: str = "auto") -> Optional[str]:
        """Export and return the current device config for verification."""
        return self.read_current_config(port)

    def _set_device_name(
        self,
        port: str,
        long_name: str,
        short_name: Optional[str] = None,
    ) -> None:
        """Set device long_name and short_name via CLI."""
        cmd = ["meshtastic", "--set-owner", long_name]
        if short_name:
            cmd.extend(["--set-owner-short", short_name])
        if port != "auto":
            cmd.extend(["--port", port])

        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.warning("Could not set device name: %s", e)

    def _inject_channel_psks(self, config_yaml: str) -> str:
        """Replace placeholder channel PSKs with fleet channel set."""
        channel_set = self.channel_manager.get_channel_set()
        config = yaml.safe_load(config_yaml)

        # Meshtastic config uses channel_0, channel_1, etc. or a channels list
        # We inject PSKs into the MQTT password and channel definitions
        if channel_set.channels:
            primary = channel_set.get_primary()
            if primary and "mqtt" in config:
                # Use primary channel PSK as MQTT password (simplified)
                config["mqtt"]["password"] = primary.psk[:32]

        return yaml.dump(config, default_flow_style=False, sort_keys=False)
