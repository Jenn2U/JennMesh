"""Bulk fleet operations API endpoints.

Provides preview (dry-run), execute, progress tracking, cancellation,
and operation history.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["bulk-ops"])


def _get_manager(request: Request):
    """Get or lazily create BulkOperationManager."""
    manager = getattr(request.app.state, "bulk_operation_manager", None)
    if manager is not None:
        return manager
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    from jenn_mesh.core.bulk_operation_manager import BulkOperationManager

    bulk_push = getattr(request.app.state, "bulk_push", None)
    manager = BulkOperationManager(db=db, bulk_push=bulk_push)
    request.app.state.bulk_operation_manager = manager
    return manager


# ── Request models ────────────────────────────────────────────────────


class TargetFilterRequest(BaseModel):
    node_ids: list[str] | None = None
    hardware_model: str | None = None
    firmware_version: str | None = None
    role: str | None = None
    mesh_status: str | None = None
    all_devices: bool = False


class BulkOperationRequest(BaseModel):
    operation_type: str = Field(description="config_push, reboot, psk_rotation, firmware_update, factory_reset")
    target_filter: TargetFilterRequest = Field(default_factory=TargetFilterRequest)
    config_template_id: int | None = None
    parameters: dict = Field(default_factory=dict)
    dry_run: bool = Field(default=True, description="Preview only (default True)")
    confirmed: bool = Field(default=False, description="Must be True to execute")


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/bulk-ops/preview")
async def preview_operation(request: Request, body: BulkOperationRequest) -> dict:
    """Preview a bulk operation — shows which devices would be affected."""
    manager = _get_manager(request)
    req = body.model_dump()
    req["target_filter"] = body.target_filter.model_dump(exclude_none=True)
    return manager.preview(req)


@router.post("/bulk-ops/execute")
async def execute_operation(request: Request, body: BulkOperationRequest) -> dict:
    """Execute a bulk operation (requires dry_run=False, confirmed=True)."""
    manager = _get_manager(request)
    if body.dry_run:
        raise HTTPException(
            status_code=400,
            detail="Cannot execute with dry_run=True — use /bulk-ops/preview",
        )
    if not body.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Bulk execution requires confirmed=True for safety",
        )
    req = body.model_dump()
    req["target_filter"] = body.target_filter.model_dump(exclude_none=True)
    result = manager.execute(req)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/bulk-ops/{operation_id}")
async def get_operation_progress(request: Request, operation_id: int) -> dict:
    """Get progress/status of a bulk operation."""
    manager = _get_manager(request)
    progress = manager.get_progress(operation_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="Operation not found")
    # Parse JSON fields
    for field in ("target_node_ids", "results_json"):
        if isinstance(progress.get(field), str):
            try:
                progress[field] = json.loads(progress[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return progress


@router.post("/bulk-ops/{operation_id}/cancel")
async def cancel_operation(request: Request, operation_id: int) -> dict:
    """Cancel a running or pending bulk operation."""
    manager = _get_manager(request)
    result = manager.cancel(operation_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/bulk-ops")
async def list_operations(
    request: Request, limit: int = 50, status: str | None = None
) -> dict:
    """List bulk operations with optional status filter."""
    manager = _get_manager(request)
    ops = manager.list_operations(limit=limit, status=status)
    # Parse JSON fields
    for op in ops:
        for field in ("target_node_ids", "results_json"):
            if isinstance(op.get(field), str):
                try:
                    op[field] = json.loads(op[field])
                except (json.JSONDecodeError, TypeError):
                    pass
    return {"operations": ops, "count": len(ops)}
