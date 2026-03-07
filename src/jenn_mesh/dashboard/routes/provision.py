"""Provisioning API routes — bench flash status, provisioning log."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request

router = APIRouter(tags=["provision"])

# Actions that indicate an in-progress provisioning operation
_ACTIVE_ACTIONS = frozenset({"radio_detected", "erase_started", "flash_started", "config_applied"})


@router.get("/provision/recent")
async def provisioning_recent(request: Request) -> dict:
    """Get recent provisioning events (last 5 minutes).

    Used by the dashboard to drive toast notifications and the
    Provision tab badge counter.
    """
    db = request.app.state.db
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM provisioning_log WHERE timestamp >= ? ORDER BY timestamp DESC",
            (cutoff,),
        ).fetchall()

    entries = [dict(r) for r in rows]
    active_count = sum(1 for e in entries if e.get("action") in _ACTIVE_ACTIONS)

    return {
        "count": len(entries),
        "active_count": active_count,
        "entries": entries,
    }


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
