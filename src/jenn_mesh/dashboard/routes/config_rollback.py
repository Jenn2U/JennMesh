"""Config rollback API routes — snapshots, manual rollback, and status."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["config-rollback"])


class RollbackConfirmBody(BaseModel):
    """Request body for manual rollback — requires explicit confirmation."""

    confirmed: bool = False


@router.get("/config-rollback/snapshots")
async def list_snapshots(
    request: Request,
    node_id: str | None = None,
    limit: int = 50,
) -> dict:
    """List recent config snapshots, optionally filtered by node_id."""
    manager = getattr(request.app.state, "config_rollback_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Config rollback system unavailable")

    if node_id:
        snapshots = manager.get_node_history(node_id, limit=limit)
    else:
        snapshots = manager.db.get_recent_snapshots(limit=limit)

    return {"count": len(snapshots), "snapshots": snapshots}


@router.get("/config-rollback/snapshot/{snapshot_id}")
async def get_snapshot(request: Request, snapshot_id: int) -> dict:
    """Get details of a specific config snapshot."""
    manager = getattr(request.app.state, "config_rollback_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Config rollback system unavailable")

    snapshot = manager.get_snapshot(snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"Snapshot {snapshot_id} not found")
    return snapshot


@router.post("/config-rollback/snapshot/{snapshot_id}/rollback")
async def manual_rollback(
    request: Request,
    snapshot_id: int,
    body: RollbackConfirmBody,
) -> dict:
    """Manually roll back a node to a specific snapshot's yaml_before.

    Requires ``confirmed: true`` — this pushes config to a live mesh node.
    """
    manager = getattr(request.app.state, "config_rollback_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Config rollback system unavailable")

    if not body.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Manual rollback requires explicit confirmation. Set confirmed=true.",
        )

    result = manager.manual_rollback(snapshot_id)
    if "error" in result and not result.get("success"):
        raise HTTPException(status_code=422, detail=result["error"])
    return result


@router.get("/config-rollback/status")
async def rollback_status(request: Request) -> dict:
    """Summary of rollback system state — monitoring count, breakdowns."""
    manager = getattr(request.app.state, "config_rollback_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Config rollback system unavailable")
    return manager.get_rollback_status()
