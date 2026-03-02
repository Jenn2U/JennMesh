"""Workbench manager — server-side singleton radio session for interactive config."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Optional

import yaml

from jenn_mesh.core.config_manager import ConfigManager
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.device import ConfigHash
from jenn_mesh.models.workbench import (
    ApplyResult,
    ConfigDiff,
    ConfigDiffEntry,
    ConfigSection,
    ConnectionMethod,
    ConnectionRequest,
    RadioConfig,
    SaveTemplateRequest,
    SaveTemplateResult,
    WorkbenchStatus,
)

logger = logging.getLogger(__name__)

# Config sections from localConfig (core radio settings)
LOCAL_CONFIG_SECTIONS = [
    "device",
    "lora",
    "position",
    "power",
    "display",
    "bluetooth",
    "network",
    "security",
]

# Config sections from moduleConfig (optional modules)
MODULE_CONFIG_SECTIONS = [
    "mqtt",
    "telemetry",
    "serial",
    "external_notification",
    "range_test",
    "store_forward",
    "canned_message",
]


class WorkbenchManager:
    """Server-side radio workbench session manager.

    Maintains a single connected radio session. All operations
    (read, edit, apply, save) operate on this active session.

    Only one radio can be connected at a time. Connecting a new
    radio auto-disconnects the previous one.
    """

    def __init__(self, db: MeshDatabase, configs_dir: Optional[Path] = None):
        self._db = db
        if configs_dir is not None:
            self._configs_dir = configs_dir
        else:
            # Use the module-level CONFIGS_DIR from config_manager
            from jenn_mesh.core.config_manager import CONFIGS_DIR

            self._configs_dir = CONFIGS_DIR
        self._interface: Any = None
        self._connection_method: Optional[ConnectionMethod] = None
        self._connection_address: Optional[str] = None
        self._last_read_config: Optional[RadioConfig] = None
        self._lock = threading.Lock()

    # ── Connection lifecycle ──────────────────────────────────────

    def connect(self, request: ConnectionRequest) -> WorkbenchStatus:
        """Connect to a radio. Disconnects any existing session first.

        Args:
            request: Connection parameters (method + address).

        Returns:
            WorkbenchStatus reflecting the new connection state.
        """
        with self._lock:
            # Disconnect existing session if any
            if self._interface is not None:
                self._disconnect_internal()

            try:
                interface = self._create_interface(request)
                self._interface = interface
                self._connection_method = request.method
                self._connection_address = (
                    request.port or request.host or request.ble_address or "auto"
                )
                self._last_read_config = None

                return self._build_status()

            except Exception as e:
                logger.error("Workbench connect failed: %s", e)
                self._interface = None
                self._connection_method = None
                self._connection_address = None
                return WorkbenchStatus(
                    connected=False,
                    error=f"Failed to connect: {e}",
                )

    def disconnect(self) -> WorkbenchStatus:
        """Disconnect the current radio session."""
        with self._lock:
            self._disconnect_internal()
            return WorkbenchStatus(connected=False)

    def get_status(self) -> WorkbenchStatus:
        """Return current connection status and radio info."""
        with self._lock:
            if self._interface is None:
                return WorkbenchStatus(connected=False)
            return self._build_status()

    @property
    def is_connected(self) -> bool:
        """Check if a radio is currently connected."""
        return self._interface is not None

    # ── Config read ───────────────────────────────────────────────

    def read_config(self) -> RadioConfig:
        """Read full structured config from connected radio.

        Reads localConfig (device, lora, position, power, display,
        bluetooth, network, security) and moduleConfig (mqtt, telemetry,
        serial, etc.) from the meshtastic interface.

        Returns:
            RadioConfig with named sections, raw YAML, and config hash.

        Raises:
            RuntimeError: If not connected to any radio.
        """
        with self._lock:
            if self._interface is None:
                raise RuntimeError("Not connected to any radio")

            sections: list[ConfigSection] = []

            # Read localConfig sections
            local_config = getattr(getattr(self._interface, "localNode", None), "localConfig", None)
            if local_config:
                for section_name in LOCAL_CONFIG_SECTIONS:
                    proto = getattr(local_config, section_name, None)
                    if proto is not None:
                        fields = self._protobuf_to_dict(proto)
                        sections.append(ConfigSection(name=section_name, fields=fields))

            # Read moduleConfig sections
            module_config = getattr(
                getattr(self._interface, "localNode", None), "moduleConfig", None
            )
            if module_config:
                for section_name in MODULE_CONFIG_SECTIONS:
                    proto = getattr(module_config, section_name, None)
                    if proto is not None:
                        fields = self._protobuf_to_dict(proto)
                        sections.append(ConfigSection(name=section_name, fields=fields))

            # Build raw YAML from sections
            config_dict = {s.name: s.fields for s in sections}
            raw_yaml = yaml.dump(config_dict, default_flow_style=False, sort_keys=False)
            config_hash = ConfigHash.compute(raw_yaml)

            result = RadioConfig(
                sections=sections,
                raw_yaml=raw_yaml,
                config_hash=config_hash,
            )
            self._last_read_config = result
            return result

    # ── Config diff ───────────────────────────────────────────────

    def compute_diff(self, proposed_sections: list[ConfigSection]) -> ConfigDiff:
        """Compare proposed config changes against the last-read config.

        Args:
            proposed_sections: Sections with edited field values.

        Returns:
            ConfigDiff listing all changed fields.

        Raises:
            RuntimeError: If no config has been read yet.
        """
        if self._last_read_config is None:
            raise RuntimeError("No config read yet — call read_config() first")

        # Build lookup: section_name → {field → value}
        current_map: dict[str, dict[str, Any]] = {}
        for section in self._last_read_config.sections:
            current_map[section.name] = section.fields

        changes: list[ConfigDiffEntry] = []
        for proposed in proposed_sections:
            current_fields = current_map.get(proposed.name, {})
            for field_name, proposed_value in proposed.fields.items():
                current_value = current_fields.get(field_name)
                if current_value != proposed_value:
                    changes.append(
                        ConfigDiffEntry(
                            section=proposed.name,
                            field=field_name,
                            current_value=current_value,
                            proposed_value=proposed_value,
                        )
                    )

        return ConfigDiff(changes=changes, change_count=len(changes))

    # ── Config apply ──────────────────────────────────────────────

    def apply_config(self, sections: list[ConfigSection]) -> ApplyResult:
        """Apply edited config sections to the connected radio.

        For each section, builds the appropriate protobuf config and calls
        setConfig()/setModuleConfig() on the local node. Performs a readback
        after all sections to verify changes were accepted.

        Args:
            sections: Config sections with field values to apply.

        Returns:
            ApplyResult with per-section success/failure and readback status.
        """
        with self._lock:
            if self._interface is None:
                return ApplyResult(success=False, error="Not connected to any radio")

            applied: list[str] = []
            failed: list[str] = []

            local_node = self._interface.localNode

            for section in sections:
                try:
                    self._apply_section(local_node, section)
                    applied.append(section.name)
                except Exception as e:
                    logger.error("Failed to apply section %s: %s", section.name, e)
                    failed.append(section.name)

            # Readback verification
            readback_matches = False
            config_hash = None
            try:
                readback = self.read_config()
                config_hash = readback.config_hash
                # Verify applied sections match
                readback_map = {s.name: s.fields for s in readback.sections}
                mismatches = 0
                for section in sections:
                    if section.name in applied:
                        rb_fields = readback_map.get(section.name, {})
                        for k, v in section.fields.items():
                            if rb_fields.get(k) != v:
                                mismatches += 1
                readback_matches = mismatches == 0
            except Exception as e:
                logger.warning("Readback verification failed: %s", e)

            success = len(failed) == 0

            # Audit trail
            node_id = self._get_node_id()
            if node_id and applied:
                self._db.log_provisioning(
                    node_id=node_id,
                    action="workbench_apply",
                    details=(
                        f"Applied sections: {', '.join(applied)}"
                        + (f"; Failed: {', '.join(failed)}" if failed else "")
                    ),
                )

            return ApplyResult(
                success=success,
                applied_sections=applied,
                failed_sections=failed,
                readback_matches=readback_matches,
                config_hash=config_hash,
                error=f"Failed sections: {', '.join(failed)}" if failed else None,
            )

    # ── Save as template ──────────────────────────────────────────

    def save_as_template(self, request: SaveTemplateRequest) -> SaveTemplateResult:
        """Save the current radio config as a new golden template.

        Reads the current config (or uses cached), serializes to YAML,
        writes to configs/ directory, and saves to the config_templates table.

        Args:
            request: Template name and optional metadata.

        Returns:
            SaveTemplateResult with success status and file path.
        """
        with self._lock:
            if self._interface is None and self._last_read_config is None:
                return SaveTemplateResult(
                    success=False,
                    template_name=request.template_name,
                    error="Not connected and no config cached",
                )

            try:
                # Use cached or read fresh
                config = self._last_read_config
                if config is None:
                    config = self.read_config()

                config_dict = {s.name: s.fields for s in config.sections}

                cm = ConfigManager(self._db, self._configs_dir)
                config_hash, yaml_path = cm.save_template_from_dict(
                    template_name=request.template_name,
                    config_dict=config_dict,
                    description=request.description,
                )

                # Audit trail
                node_id = self._get_node_id() or "workbench"
                self._db.log_provisioning(
                    node_id=node_id,
                    action="workbench_save_template",
                    role=request.template_name,
                    template_hash=config_hash,
                    details=f"Saved from workbench session: {request.description or ''}",
                )

                return SaveTemplateResult(
                    success=True,
                    template_name=request.template_name,
                    config_hash=config_hash,
                    yaml_path=yaml_path,
                )

            except ValueError as e:
                return SaveTemplateResult(
                    success=False,
                    template_name=request.template_name,
                    error=str(e),
                )
            except Exception as e:
                logger.error("Save template failed: %s", e)
                return SaveTemplateResult(
                    success=False,
                    template_name=request.template_name,
                    error=f"Save failed: {e}",
                )

    # ── Internal helpers ──────────────────────────────────────────

    def _create_interface(self, request: ConnectionRequest) -> Any:
        """Create a meshtastic interface based on the connection method."""
        import meshtastic.serial_interface
        import meshtastic.tcp_interface

        if request.method == ConnectionMethod.SERIAL:
            if request.port:
                return meshtastic.serial_interface.SerialInterface(request.port)
            return meshtastic.serial_interface.SerialInterface()

        elif request.method == ConnectionMethod.TCP:
            if not request.host:
                raise ValueError("TCP connection requires a host address")
            parts = request.host.split(":")
            hostname = parts[0]
            port = int(parts[1]) if len(parts) > 1 else 4403
            return meshtastic.tcp_interface.TCPInterface(hostname=hostname, portNumber=port)

        elif request.method == ConnectionMethod.BLE:
            # BLE support placeholder — meshtastic library handles BLE
            # via meshtastic.ble_interface if available
            raise NotImplementedError("BLE connection not yet implemented")

        raise ValueError(f"Unknown connection method: {request.method}")

    def _disconnect_internal(self) -> None:
        """Close the current interface without lock (caller holds lock)."""
        if self._interface is not None:
            try:
                self._interface.close()
            except Exception as e:
                logger.warning("Error closing workbench interface: %s", e)
        self._interface = None
        self._connection_method = None
        self._connection_address = None
        self._last_read_config = None

    def _build_status(self) -> WorkbenchStatus:
        """Build WorkbenchStatus from current interface state."""
        if self._interface is None:
            return WorkbenchStatus(connected=False)

        node_id = self._get_node_id()
        my_info = getattr(self._interface, "myInfo", None)
        local_node = getattr(self._interface, "localNode", None)

        long_name = None
        short_name = None
        hw_model = None
        firmware_version = None
        uptime_seconds = None

        if my_info:
            hw_model = getattr(my_info, "hw_model_string", None) or str(
                getattr(my_info, "hw_model", "unknown")
            )
            firmware_version = getattr(my_info, "firmware_version", None)

        # Try to read user info from nodes dict
        nodes = getattr(self._interface, "nodes", {}) or {}
        if node_id and node_id in nodes:
            user = nodes[node_id].get("user", {})
            long_name = user.get("longName")
            short_name = user.get("shortName")
            if not hw_model:
                hw_model = user.get("hwModel", "unknown")

        if local_node:
            uptime_seconds = getattr(local_node, "uptimeSeconds", None)

        return WorkbenchStatus(
            connected=True,
            method=self._connection_method,
            address=self._connection_address,
            node_id=node_id,
            long_name=long_name,
            short_name=short_name,
            hw_model=hw_model,
            firmware_version=firmware_version,
            uptime_seconds=uptime_seconds,
        )

    def _get_node_id(self) -> Optional[str]:
        """Extract the connected radio's node ID."""
        my_info = getattr(self._interface, "myInfo", None)
        if my_info:
            my_node_num = getattr(my_info, "my_node_num", None)
            if my_node_num:
                return f"!{my_node_num:08x}"
        return None

    def _protobuf_to_dict(self, proto: Any) -> dict[str, Any]:
        """Convert a protobuf message to a plain dict with snake_case keys."""
        try:
            from google.protobuf.json_format import MessageToDict

            return MessageToDict(proto, preserving_proto_field_name=True)
        except (ImportError, AttributeError):
            # Fallback: if proto is already a dict or dict-like
            if isinstance(proto, dict):
                return proto
            return {}

    def _apply_section(self, local_node: Any, section: ConfigSection) -> None:
        """Apply a single config section to the local node.

        Uses setConfig() for localConfig sections and
        setModuleConfig() for moduleConfig sections.
        """
        if section.name in LOCAL_CONFIG_SECTIONS:
            # Build a partial localConfig protobuf with just this section
            from meshtastic.protobuf import config_pb2

            config = config_pb2.Config()
            sub_config = getattr(config, section.name, None)
            if sub_config is None:
                raise ValueError(f"Unknown localConfig section: {section.name}")
            for key, value in section.fields.items():
                try:
                    setattr(sub_config, key, value)
                except (AttributeError, TypeError) as e:
                    logger.warning("Cannot set %s.%s = %r: %s", section.name, key, value, e)
            local_node.setConfig(config)

        elif section.name in MODULE_CONFIG_SECTIONS:
            from meshtastic.protobuf import module_config_pb2

            config = module_config_pb2.ModuleConfig()
            sub_config = getattr(config, section.name, None)
            if sub_config is None:
                raise ValueError(f"Unknown moduleConfig section: {section.name}")
            for key, value in section.fields.items():
                try:
                    setattr(sub_config, key, value)
                except (AttributeError, TypeError) as e:
                    logger.warning("Cannot set %s.%s = %r: %s", section.name, key, value, e)
            local_node.setModuleConfig(config)

        else:
            raise ValueError(f"Unknown config section: {section.name}")
