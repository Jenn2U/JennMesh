"""Config queue models — store-and-forward outbox for offline radio config pushes."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

# ── Backoff constants ──────────────────────────────────────────────────
INITIAL_RETRY_DELAY_SECONDS = 60  # 1 minute
MAX_RETRY_DELAY_SECONDS = 1920  # 32 minutes
BACKOFF_MULTIPLIER = 2
DEFAULT_MAX_RETRIES = 10
RETRY_LOOP_INTERVAL_SECONDS = 30  # How often the background loop checks


class ConfigQueueStatus(str, Enum):
    """Lifecycle states for a queued config push."""

    PENDING = "pending"
    RETRYING = "retrying"
    DELIVERED = "delivered"
    FAILED_PERMANENT = "failed_permanent"
    CANCELLED = "cancelled"


class ConfigQueueEntry(BaseModel):
    """A queued config push waiting for delivery to an offline radio."""

    id: Optional[int] = Field(default=None, description="DB-assigned ID")
    target_node_id: str = Field(description="Meshtastic node ID (e.g., '!a1b2c3d4')")
    template_role: str = Field(description="Golden template role name (e.g., 'relay-node')")
    config_hash: str = Field(description="SHA-256 of the YAML content at enqueue time")
    yaml_content: str = Field(description="Full YAML config payload")
    status: ConfigQueueStatus = Field(default=ConfigQueueStatus.PENDING)
    retry_count: int = Field(default=0, ge=0)
    max_retries: int = Field(default=DEFAULT_MAX_RETRIES, ge=1)
    last_error: Optional[str] = Field(default=None)
    source_push_id: Optional[str] = Field(
        default=None,
        description="BulkPush push_id that originated this entry",
    )
    created_at: Optional[str] = Field(default=None)
    next_retry_at: Optional[str] = Field(default=None)
    last_retry_at: Optional[str] = Field(default=None)
    delivered_at: Optional[str] = Field(default=None)
    escalated_at: Optional[str] = Field(default=None)


def compute_next_retry_delay(retry_count: int) -> int:
    """Compute exponential backoff delay in seconds.

    Schedule: 1m, 2m, 4m, 8m, 16m, 32m, 32m, 32m, ...
    """
    delay = INITIAL_RETRY_DELAY_SECONDS * (BACKOFF_MULTIPLIER**retry_count)
    return min(delay, MAX_RETRY_DELAY_SECONDS)
