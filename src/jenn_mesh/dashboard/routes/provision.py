"""Provisioning API routes — bench flash status, provisioning log."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["provision"])


@router.get("/provision/log")
async def provisioning_log(request: Request) -> dict:
    """Get the provisioning audit trail."""
    db = request.app.state.db
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM provisioning_log ORDER BY timestamp DESC LIMIT 100"
        ).fetchall()

    return {
        "count": len(rows),
        "entries": [dict(r) for r in rows],
    }


@router.get("/provision/log/{node_id}")
async def provisioning_log_for_device(request: Request, node_id: str) -> dict:
    """Get provisioning history for a specific device."""
    db = request.app.state.db
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM provisioning_log WHERE node_id = ? ORDER BY timestamp DESC",
            (node_id,),
        ).fetchall()

    return {
        "node_id": node_id,
        "count": len(rows),
        "entries": [dict(r) for r in rows],
    }
