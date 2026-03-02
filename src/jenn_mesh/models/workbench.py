"""Radio Workbench models — connection, config, diff, and bulk push."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

# ── Connection ────────────────────────────────────────────────────────


class ConnectionMethod(str, Enum):
    """How to connect to the workbench radio."""

    SERIAL = "serial"
    TCP = "tcp"
    BLE = "ble"


class ConnectionRequest(BaseModel):
    """Request to connect the workbench to a radio."""

    method: ConnectionMethod
    port: Optional[str] = Field(None, description="Serial port, e.g. /dev/ttyUSB0")
    host: Optional[str] = Field(None, description="TCP host:port, e.g. 10.10.50.100:4403")
    ble_address: Optional[str] = Field(None, description="BLE address or name")


class WorkbenchStatus(BaseModel):
    """Current state of the workbench session."""

    connected: bool = False
    method: Optional[ConnectionMethod] = None
    address: Optional[str] = None
    node_id: Optional[str] = None
    long_name: Optional[str] = None
    short_name: Optional[str] = None
    hw_model: Optional[str] = None
    firmware_version: Optional[str] = None
    uptime_seconds: Optional[int] = None
    error: Optional[str] = None


# ── Config ────────────────────────────────────────────────────────────


class ConfigSection(BaseModel):
    """One section of a Meshtastic config (e.g. device, lora, mqtt)."""

    name: str = Field(description="Section name: device, lora, position, power, etc.")
    fields: dict[str, Any] = Field(default_factory=dict, description="Key-value config fields")


class RadioConfig(BaseModel):
    """Full structured config read from a connected radio."""

    sections: list[ConfigSection] = Field(default_factory=list)
    raw_yaml: Optional[str] = Field(None, description="Full config as YAML string")
    config_hash: Optional[str] = Field(None, description="SHA-256 of raw YAML")


# ── Diff ──────────────────────────────────────────────────────────────


class ConfigDiffEntry(BaseModel):
    """A single config field that differs between current and proposed."""

    section: str
    field: str
    current_value: Any = None
    proposed_value: Any = None


class ConfigDiff(BaseModel):
    """Diff between current device config and proposed changes."""

    changes: list[ConfigDiffEntry] = Field(default_factory=list)
    change_count: int = 0


# ── Apply ─────────────────────────────────────────────────────────────


class ApplyRequest(BaseModel):
    """Request to apply edited config sections to the connected radio."""

    sections: list[ConfigSection] = Field(description="Config sections to apply")


class ApplyResult(BaseModel):
    """Result of applying config to connected radio."""

    success: bool
    applied_sections: list[str] = Field(default_factory=list)
    failed_sections: list[str] = Field(default_factory=list)
    readback_matches: bool = False
    config_hash: Optional[str] = None
    error: Optional[str] = None


# ── Save Template ─────────────────────────────────────────────────────


class SaveTemplateRequest(BaseModel):
    """Request to save the current workbench config as a golden template."""

    template_name: str = Field(description="Template name, e.g. 'relay-node-v2'")
    description: Optional[str] = Field(None, description="Optional description")
    base_role: Optional[str] = Field(None, description="Optional DeviceRole this derives from")


class SaveTemplateResult(BaseModel):
    """Result of saving a golden template."""

    success: bool
    template_name: str
    config_hash: str = ""
    yaml_path: Optional[str] = None
    error: Optional[str] = None


# ── Bulk Push ─────────────────────────────────────────────────────────


class PushDeviceStatus(str, Enum):
    """Status of a single device in a bulk push operation."""

    QUEUED = "queued"
    PUSHING = "pushing"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class BulkPushRequest(BaseModel):
    """Request to push a template to multiple fleet devices."""

    template_name: str = Field(description="Golden template to push")
    device_ids: list[str] = Field(description="List of target node_ids")
    dry_run: bool = Field(default=False, description="If true, only preview — no actual push")


class DevicePushEntry(BaseModel):
    """Push status for a single device in a bulk operation."""

    node_id: str
    status: PushDeviceStatus = PushDeviceStatus.QUEUED
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class BulkPushProgress(BaseModel):
    """Progress tracker for a bulk push operation."""

    push_id: str
    template_name: str
    total: int = 0
    queued: int = 0
    pushing: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    devices: list[DevicePushEntry] = Field(default_factory=list)
    is_complete: bool = False
    error: Optional[str] = None
