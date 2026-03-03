"""Failover models — automated failover event lifecycle and compensations."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class FailoverStatus(str, Enum):
    """Lifecycle states for a failover event."""

    ACTIVE = "active"
    REVERTED = "reverted"
    CANCELLED = "cancelled"
    REVERT_FAILED = "revert_failed"


class CompensationType(str, Enum):
    """Types of failover compensation applied to nearby nodes."""

    TX_POWER_INCREASE = "tx_power_increase"  # lora.tx_power (dBm)
    ROLE_CHANGE = "role_change"  # device.role (e.g. CLIENT → ROUTER_CLIENT)
    HOP_LIMIT_INCREASE = "hop_limit_increase"  # lora.hop_limit


class CompensationStatus(str, Enum):
    """Lifecycle states for an individual compensation action."""

    PENDING = "pending"
    APPLIED = "applied"
    REVERTED = "reverted"
    REVERT_FAILED = "revert_failed"


class FailoverCompensation(BaseModel):
    """A single config change applied to a compensation node during failover."""

    id: Optional[int] = Field(default=None, description="DB auto-assigned ID")
    failover_event_id: int
    comp_node_id: str = Field(description="Node that was reconfigured")
    comp_type: CompensationType
    config_key: str = Field(description="Meshtastic config key (e.g. 'lora.tx_power')")
    original_value: str = Field(description="Value before failover")
    new_value: str = Field(description="Value applied during failover")
    status: CompensationStatus = CompensationStatus.PENDING
    applied_at: Optional[datetime] = None
    reverted_at: Optional[datetime] = None
    error: Optional[str] = None


class FailoverImpactAssessment(BaseModel):
    """Impact assessment for a potential node failure."""

    failed_node_id: str
    is_spof: bool = Field(description="Whether this node is a single point of failure")
    dependent_nodes: list[str] = Field(
        default_factory=list,
        description="Nodes that would lose connectivity if this node fails",
    )
    compensation_candidates: list[dict] = Field(
        default_factory=list,
        description="Nearby online nodes that could compensate (with config details)",
    )
    suggested_compensations: list[dict] = Field(
        default_factory=list,
        description="Proposed compensation actions to apply",
    )


class FailoverEvent(BaseModel):
    """A failover event tracking the lifecycle of an automated failover."""

    id: Optional[int] = Field(default=None, description="DB auto-assigned ID")
    failed_node_id: str
    status: FailoverStatus = FailoverStatus.ACTIVE
    dependent_nodes: list[str] = Field(default_factory=list)
    operator: str = "dashboard"
    compensations: list[FailoverCompensation] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    reverted_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None


class FailoverExecuteRequest(BaseModel):
    """Request body for executing a failover."""

    confirmed: bool = Field(
        default=False,
        description="Must be true — safety gate for failover execution",
    )


class FailoverRevertRequest(BaseModel):
    """Request body for reverting a failover."""

    confirmed: bool = Field(
        default=False,
        description="Must be true — safety gate for failover revert",
    )


class FailoverCancelRequest(BaseModel):
    """Request body for cancelling a failover."""

    confirmed: bool = Field(
        default=False,
        description="Must be true — safety gate for failover cancellation",
    )
