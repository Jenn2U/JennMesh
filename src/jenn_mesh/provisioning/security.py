"""PKC admin key management and Managed Mode setup."""

from __future__ import annotations

import base64
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default key storage location
KEYS_DIR = Path.home() / ".jenn-mesh" / "keys"


@dataclass
class AdminKeyPair:
    """An Ed25519 admin keypair for PKC-authenticated remote admin."""

    public_key_b64: str
    private_key_path: Optional[Path] = None
    description: str = "fleet-admin"


@dataclass
class SecuritySetup:
    """Handles PKC admin key generation and Managed Mode configuration."""

    keys_dir: Path = field(default_factory=lambda: KEYS_DIR)

    def __post_init__(self) -> None:
        self.keys_dir.mkdir(parents=True, exist_ok=True)

    def generate_admin_keypair(self, name: str = "fleet-admin") -> AdminKeyPair:
        """Generate an Ed25519 keypair for PKC admin access.

        The private key is stored locally; the public key is embedded in
        golden config templates and pushed to devices.

        Args:
            name: Identifier for this key (used in filename).

        Returns:
            AdminKeyPair with base64-encoded public key and private key path.
        """
        private_path = self.keys_dir / f"{name}.key"
        public_path = self.keys_dir / f"{name}.pub"

        # Use meshtastic CLI to generate admin key if available
        try:
            result = subprocess.run(
                ["meshtastic", "--gen-admin-key"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                public_key_b64 = result.stdout.strip()
                public_path.write_text(public_key_b64)
                logger.info("Generated admin keypair via meshtastic CLI: %s", public_path)
                return AdminKeyPair(
                    public_key_b64=public_key_b64,
                    private_key_path=private_path if private_path.exists() else None,
                    description=name,
                )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.warning("meshtastic CLI not available, using openssl fallback")

        # Fallback: generate via openssl (Ed25519)
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "Ed25519", "-out", str(private_path)],
            check=True,
            capture_output=True,
            timeout=10,
        )
        result = subprocess.run(
            ["openssl", "pkey", "-in", str(private_path), "-pubout", "-outform", "DER"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            public_key_b64 = base64.b64encode(result.stdout).decode()
            public_path.write_text(public_key_b64)

        logger.info("Generated admin keypair: %s", public_path)
        return AdminKeyPair(
            public_key_b64=public_key_b64,
            private_key_path=private_path,
            description=name,
        )

    def load_admin_key(self, name: str = "fleet-admin") -> Optional[AdminKeyPair]:
        """Load an existing admin public key by name."""
        public_path = self.keys_dir / f"{name}.pub"
        private_path = self.keys_dir / f"{name}.key"

        if not public_path.exists():
            return None

        return AdminKeyPair(
            public_key_b64=public_path.read_text().strip(),
            private_key_path=private_path if private_path.exists() else None,
            description=name,
        )

    def inject_admin_key_into_config(
        self, config_yaml: str, admin_key_b64: str
    ) -> str:
        """Insert an admin public key into a golden config YAML string.

        Replaces the placeholder `admin_key: []` with the actual key.
        """
        import yaml

        config = yaml.safe_load(config_yaml)
        if "security" not in config:
            config["security"] = {}
        config["security"]["admin_key"] = [admin_key_b64]
        return yaml.dump(config, default_flow_style=False, sort_keys=False)

    @staticmethod
    def enable_managed_mode(port: str = "/dev/ttyUSB0") -> bool:
        """Enable Managed Mode on a connected device via meshtastic CLI.

        WARNING: Only call this after verifying remote admin works.
        Once enabled, local config changes are blocked.
        """
        try:
            result = subprocess.run(
                ["meshtastic", "--port", port, "--set", "security.is_managed", "true"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                logger.info("Managed Mode enabled on %s", port)
                return True
            logger.error("Failed to enable Managed Mode: %s", result.stderr)
            return False
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.error("Cannot enable Managed Mode: %s", e)
            return False
