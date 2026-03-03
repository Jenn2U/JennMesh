"""Heartbeat API routes — mesh heartbeat data and fleet mesh status."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(tags=["heartbeat"])


@router.get("/heartbeat/{node_id}")
async def get_device_heartbeat(request: Request, node_id: str) -> dict:
    """Get the latest heartbeat and history summary for a device."""
    db = request.app.state.db
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    device = db.get_device(node_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    latest = db.get_latest_heartbeat(node_id)
    history = db.get_heartbeat_history(node_id, limit=20)

    return {
        "node_id": node_id,
        "mesh_status": device.get("mesh_status", "unknown"),
        "last_mesh_heartbeat": device.get("last_mesh_heartbeat"),
        "latest_heartbeat": latest,
        "heartbeat_count": len(history),
        "recent_history": history,
    }


@router.get("/heartbeat/recent/all")
async def get_recent_heartbeats(
    request: Request,
    minutes: int = Query(default=10, ge=1, le=1440),
) -> dict:
    """Get all heartbeats received in the last N minutes."""
    db = request.app.state.db
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    heartbeats = db.get_recent_heartbeats(minutes=minutes)
    return {
        "minutes": minutes,
        "count": len(heartbeats),
        "heartbeats": heartbeats,
    }


@router.get("/fleet/mesh-status")
async def fleet_mesh_status(request: Request) -> dict:
    """Get fleet-wide mesh reachability grouping."""
    db = request.app.state.db
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    devices = db.list_devices()

    reachable = []
    unreachable = []
    unknown = []

    for d in devices:
        status = d.get("mesh_status", "unknown")
        entry = {
            "node_id": d["node_id"],
            "long_name": d.get("long_name", ""),
            "mesh_status": status,
            "last_mesh_heartbeat": d.get("last_mesh_heartbeat"),
        }
        if status == "reachable":
            reachable.append(entry)
        elif status == "unreachable":
            unreachable.append(entry)
        else:
            unknown.append(entry)

    return {
        "reachable_count": len(reachable),
        "unreachable_count": len(unreachable),
        "unknown_count": len(unknown),
        "reachable": reachable,
        "unreachable": unreachable,
        "unknown": unknown,
    }
