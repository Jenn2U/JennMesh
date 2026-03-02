"""Config manager — golden template CRUD, drift detection, remote config push."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.device import ConfigHash, DeviceRole


CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "configs"


class ConfigManager:
    """Manages golden config templates and detects configuration drift."""

    def __init__(self, db: MeshDatabase, configs_dir: Optional[Path] = None):
        self.db = db
        self.configs_dir = configs_dir or CONFIGS_DIR

    def load_templates_from_disk(self) -> dict[str, str]:
        """Load all golden YAML templates from configs/ directory.

        Returns:
            Dict mapping role name to YAML content.
        """
        templates: dict[str, str] = {}
        if not self.configs_dir.exists():
            return templates

        for yaml_file in sorted(self.configs_dir.glob("*.yaml")):
            content = yaml_file.read_text()
            role = yaml_file.stem  # e.g., "relay-node" from "relay-node.yaml"
            templates[role] = content
            config_hash = ConfigHash.compute(content)
            self.db.save_config_template(
                role=role,
                yaml_content=content,
                config_hash=config_hash,
            )

        return templates

    def get_template(self, role: str) -> Optional[str]:
        """Get a golden config template YAML by role name."""
        row = self.db.get_config_template(role)
        if row:
            return row["yaml_content"]

        # Try loading from disk
        yaml_path = self.configs_dir / f"{role}.yaml"
        if yaml_path.exists():
            content = yaml_path.read_text()
            config_hash = ConfigHash.compute(content)
            self.db.save_config_template(role=role, yaml_content=content, config_hash=config_hash)
            return content

        return None

    def get_template_hash(self, role: str) -> Optional[str]:
        """Get the hash of a golden config template."""
        row = self.db.get_config_template(role)
        return row["config_hash"] if row else None

    def check_drift(self, node_id: str, current_config_yaml: str) -> bool:
        """Check if a device's current config has drifted from its golden template.

        Args:
            node_id: The device to check.
            current_config_yaml: The device's current exported YAML config.

        Returns:
            True if config has drifted, False if matching template.
        """
        device = self.db.get_device(node_id)
        if device is None:
            return False

        template_role = device.get("template_role")
        if template_role is None:
            return False  # No template assigned, can't detect drift

        template_hash = self.get_template_hash(template_role)
        if template_hash is None:
            return False

        current_hash = ConfigHash.compute(current_config_yaml)
        drifted = current_hash != template_hash

        # Update the device's config hash in the database
        with self.db.connection() as conn:
            conn.execute(
                """UPDATE devices SET config_hash = ?, template_hash = ?
                   WHERE node_id = ?""",
                (current_hash, template_hash, node_id),
            )

        return drifted

    def get_drift_report(self) -> list[dict]:
        """Get all devices with detected config drift."""
        devices = self.db.list_devices()
        drifted: list[dict] = []

        for device in devices:
            if device.get("config_hash") and device.get("template_hash"):
                if device["config_hash"] != device["template_hash"]:
                    drifted.append(
                        {
                            "node_id": device["node_id"],
                            "long_name": device.get("long_name", ""),
                            "role": device.get("template_role", "unknown"),
                            "device_hash": device["config_hash"],
                            "template_hash": device["template_hash"],
                        }
                    )

        return drifted

    @staticmethod
    def role_to_filename(role: DeviceRole) -> str:
        """Map a DeviceRole to the golden config filename stem."""
        mapping = {
            DeviceRole.RELAY: "relay-node",
            DeviceRole.GATEWAY: "edge-gateway",
            DeviceRole.MOBILE: "mobile-client",
            DeviceRole.SENSOR: "sensor-node",
            DeviceRole.REPEATER: "relay-node",  # Same template as relay
            DeviceRole.ROUTER_CLIENT: "relay-node",
            DeviceRole.TRACKER: "mobile-client",  # Same template as mobile
        }
        return mapping.get(role, "mobile-client")
