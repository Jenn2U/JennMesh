"""Config queue API routes — list, get, retry, cancel, and device status."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["config-queue"])


class ConfirmRequest(BaseModel):
    """Request body requiring explicit confirmation."""

    confirmed: bool = Field(
        default=False,
        description="Must be true — safety gate for queue operations",
    )


@router.get("/config-queue/entries")
async def list_config_queue_entries(
    request: Request,
    target_node_id: str = Query(default=None, description="Filter by target node"),
    status: str = Query(default=None, description="Filter by status"),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """List config queue entries with optional filters."""
    manager = getattr(request.app.state, "config_queue_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Config queue system unavailable")

    entries = manager.list_entries(target_node_id=target_node_id, status=status, limit=limit)
    return {
        "count": len(entries),
        "limit": limit,
        "entries": entries,
    }


@router.get("/config-queue/entry/{entry_id}")
async def get_config_queue_entry(request: Request, entry_id: int) -> dict:
    """Get a specific config queue entry by ID."""
    manager = getattr(request.app.state, "config_queue_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Config queue system unavailable")

    entry = manager.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Config queue entry not found")

    return entry


@router.post("/config-queue/entry/{entry_id}/retry")
async def retry_config_queue_entry(request: Request, entry_id: int, body: ConfirmRequest) -> dict:
    """Manual retry — reset a failed/cancelled entry to pending.

    Requires ``confirmed: true`` in the request body.
    Does NOT reset retry_count (preserves audit trail).
    """
    manager = getattr(request.app.state, "config_queue_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Config queue system unavailable")

    if not body.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Retry requires explicit confirmation. Set confirmed=true.",
        )

    result = manager.manual_retry(entry_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Entry not found or not in a retryable state "
            "(must be failed_permanent or cancelled)",
        )

    return result


@router.post("/config-queue/entry/{entry_id}/cancel")
async def cancel_config_queue_entry(request: Request, entry_id: int, body: ConfirmRequest) -> dict:
    """Cancel a pending/retrying config queue entry.

    Requires ``confirmed: true`` in the request body.
    Cannot cancel delivered or already-cancelled entries.
    """
    manager = getattr(request.app.state, "config_queue_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Config queue system unavailable")

    if not body.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Cancel requires explicit confirmation. Set confirmed=true.",
        )

    success = manager.cancel_entry(entry_id)
    if not success:
        raise HTTPException(
            status_code=404,
            detail="Entry not found or not in a cancellable state " "(must be pending or retrying)",
        )

    entry = manager.get_entry(entry_id)
    return entry or {"id": entry_id, "status": "cancelled"}


@router.get("/config-queue/status/{node_id}")
async def get_device_queue_status(request: Request, node_id: str) -> dict:
    """Get config queue status for a specific device.

    Returns total entries, pending count, and full entry list for the device.
    """
    manager = getattr(request.app.state, "config_queue_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Config queue system unavailable")

    return manager.get_device_queue_status(node_id)
