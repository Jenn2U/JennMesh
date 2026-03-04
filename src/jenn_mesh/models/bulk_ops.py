"""Bulk fleet operation models."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class BulkOperationType(str, Enum):
    """Types of bulk fleet operations."""

    CONFIG_PUSH = "config_push"
    FIRMWARE_UPDATE = "firmware_update"
    PSK_ROTATION = "psk_rotation"
    REBOOT = "reboot"
    FACTORY_RESET = "factory_reset"


class BulkOperationStatus(str, Enum):
    """States for a bulk operation."""

    PREVIEW = "preview"
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TargetFilter(BaseModel):
    """Filter to select target devices for bulk operations.

    Multiple filters are ANDed together: a device must match ALL
    specified criteria to be included.
    """

    node_ids: Optional[list[str]] = Field(
        default=None, description="Explicit list of node IDs"
    )
    hardware_model: Optional[str] = Field(
        default=None, description="Filter by hardware model (e.g., 'T-Beam')"
    )
    firmware_version: Optional[str] = Field(
        default=None, description="Filter by firmware version"
    )
    role: Optional[str] = Field(
        default=None, description="Filter by node role (e.g., 'router', 'client')"
    )
    mesh_status: Optional[str] = Field(
        default=None, description="Filter by mesh status (e.g., 'reachable', 'stale')"
    )
    all_devices: bool = Field(
        default=False,
        description="Target ALL registered devices (overrides other filters)",
    )


class BulkOperationRequest(BaseModel):
    """Request to preview or execute a bulk fleet operation."""

    operation_type: BulkOperationType
    target_filter: TargetFilter = Field(default_factory=TargetFilter)
    config_template_id: Optional[int] = Field(
        default=None, description="Config template ID (for config_push)"
    )
    parameters: dict = Field(
        default_factory=dict, description="Operation-specific parameters"
    )
    dry_run: bool = Field(
        default=True,
        description="Preview only — must be False with confirmed=True to execute",
    )
    confirmed: bool = Field(
        default=False,
        description="Explicit confirmation — must be True (with dry_run=False) to execute",
    )


class BulkOperationProgress(BaseModel):
    """Progress of a running bulk operation."""

    id: Optional[int] = Field(default=None)
    operation_type: str = Field(default="")
    status: str = Field(default="pending")
    total_targets: int = Field(default=0)
    completed_count: int = Field(default=0)
    failed_count: int = Field(default=0)
    skipped_count: int = Field(default=0)
    target_node_ids: list[str] = Field(default_factory=list)
    results_json: str = Field(default="{}")
    error: Optional[str] = Field(default=None)
    created_at: Optional[str] = Field(default=None)
    completed_at: Optional[str] = Field(default=None)
