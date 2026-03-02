"""Remote admin — PKC-authenticated remote configuration over mesh."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RemoteAdminResult:
    """Result of a remote admin command."""

    success: bool
    node_id: str
    command: str
    output: str = ""
    error: str = ""


class RemoteAdmin:
    """Execute PKC-authenticated admin commands on remote mesh nodes.

    Firmware 2.5+ supports remote admin via --dest flag with PKC
    authentication. The sending node must have the admin private key
    matching the public key stored on the target device.
    """

    def __init__(self, port: str = "auto"):
        """Initialize remote admin interface.

        Args:
            port: Local radio serial port for sending admin commands.
        """
        self.port = port

    def get_remote_config(self, dest_node_id: str) -> RemoteAdminResult:
        """Export configuration from a remote node."""
        return self._run_remote_cmd(
            dest_node_id, ["--export-config"], "export-config"
        )

    def set_remote_config(
        self, dest_node_id: str, key: str, value: str
    ) -> RemoteAdminResult:
        """Set a single config value on a remote node."""
        return self._run_remote_cmd(
            dest_node_id, ["--set", key, value], f"set {key}={value}"
        )

    def apply_remote_config(
        self, dest_node_id: str, config_path: str
    ) -> RemoteAdminResult:
        """Apply a full YAML config to a remote node."""
        return self._run_remote_cmd(
            dest_node_id, ["--configure", config_path], "configure"
        )

    def reboot_remote(self, dest_node_id: str) -> RemoteAdminResult:
        """Reboot a remote node."""
        return self._run_remote_cmd(
            dest_node_id, ["--reboot"], "reboot"
        )

    def factory_reset_remote(self, dest_node_id: str) -> RemoteAdminResult:
        """Factory reset a remote node (use with extreme caution)."""
        return self._run_remote_cmd(
            dest_node_id, ["--factory-reset"], "factory-reset"
        )

    def _run_remote_cmd(
        self,
        dest_node_id: str,
        args: list[str],
        cmd_label: str,
    ) -> RemoteAdminResult:
        """Execute a meshtastic CLI command targeting a remote node."""
        cmd = ["meshtastic", "--dest", dest_node_id] + args
        if self.port != "auto":
            cmd.extend(["--port", self.port])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,  # Remote commands travel over mesh, can be slow
            )
            success = result.returncode == 0
            return RemoteAdminResult(
                success=success,
                node_id=dest_node_id,
                command=cmd_label,
                output=result.stdout if success else "",
                error=result.stderr if not success else "",
            )
        except subprocess.TimeoutExpired:
            return RemoteAdminResult(
                success=False,
                node_id=dest_node_id,
                command=cmd_label,
                error="Command timed out (mesh propagation delay?)",
            )
        except FileNotFoundError:
            return RemoteAdminResult(
                success=False,
                node_id=dest_node_id,
                command=cmd_label,
                error="meshtastic CLI not found",
            )
