"""Shared API response models for JennMesh dashboard endpoints."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PaginatedResponse(BaseModel):
    """Standard paginated response wrapper."""

    count: int = Field(description="Number of items in this page")
    limit: int = Field(description="Maximum items per page")
    offset: int = Field(default=0, description="Offset from the start of the result set")


class StatusResponse(BaseModel):
    """Generic status response for operations."""

    status: str = Field(description="Operation status (e.g. 'ok', 'error')")
    message: Optional[str] = Field(default=None, description="Human-readable detail")


class ConfirmRequest(BaseModel):
    """Confirmation gate for irreversible operations.

    All destructive endpoints require ``confirmed: true`` in the request
    body before proceeding.  This prevents accidental deletes from
    curl one-liners or misfired scripts.
    """

    confirmed: bool = Field(default=False, description="Must be true to proceed")
