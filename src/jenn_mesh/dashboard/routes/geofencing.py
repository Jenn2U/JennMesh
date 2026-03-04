"""Geofencing API routes — CRUD and breach queries."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from jenn_mesh.core.geofencing import GeofencingManager
from jenn_mesh.models.geofence import FenceType, GeoFence, TriggerOn

router = APIRouter(tags=["geofencing"])


def _get_manager(request: Request) -> GeofencingManager:
    """Get or create a GeofencingManager from request state."""
    mgr = getattr(request.app.state, "geofencing_manager", None)
    if mgr is not None:
        return mgr
    # Fallback: create one on the fly
    db = request.app.state.db
    return GeofencingManager(db)


@router.post("/geofences")
async def create_geofence(request: Request) -> dict:
    """Create a new geofence zone.

    Body JSON:
        name: str (required)
        fence_type: "circle" | "polygon" (default: "circle")
        center_lat: float (for circle)
        center_lon: float (for circle)
        radius_m: float (for circle)
        polygon_points: list[list[float]] (for polygon, [[lat, lon], ...])
        node_filter: list[str] | null (null = all nodes)
        trigger_on: "entry" | "exit" | "both" (default: "exit")
        enabled: bool (default: true)
    """
    body = await request.json()

    if "name" not in body:
        raise HTTPException(status_code=400, detail="'name' is required")

    try:
        fence = GeoFence(
            name=body["name"],
            fence_type=FenceType(body.get("fence_type", "circle")),
            center_lat=body.get("center_lat"),
            center_lon=body.get("center_lon"),
            radius_m=body.get("radius_m"),
            polygon_points=body.get("polygon_points"),
            node_filter=body.get("node_filter"),
            trigger_on=TriggerOn(body.get("trigger_on", "exit")),
            enabled=body.get("enabled", True),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    manager = _get_manager(request)
    fence_id = manager.create_fence(fence)
    return {"id": fence_id, "name": fence.name, "status": "created"}


@router.get("/geofences")
async def list_geofences(request: Request, enabled_only: bool = Query(False)) -> dict:
    """List all geofences, optionally filtered to enabled-only."""
    manager = _get_manager(request)
    fences = manager.list_fences(enabled_only=enabled_only)
    return {
        "count": len(fences),
        "geofences": [
            {
                "id": f.id,
                "name": f.name,
                "fence_type": f.fence_type.value,
                "center_lat": f.center_lat,
                "center_lon": f.center_lon,
                "radius_m": f.radius_m,
                "polygon_points": f.polygon_points,
                "trigger_on": f.trigger_on.value,
                "enabled": f.enabled,
            }
            for f in fences
        ],
    }


@router.get("/geofences/breaches")
async def get_breaches(
    request: Request,
    node_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    """Get recent geofence breach events, optionally filtered by node."""
    manager = _get_manager(request)
    if node_id:
        breaches = manager.get_breaches_for_node(node_id, limit=limit)
    else:
        # Get all geofence-related alerts
        db = request.app.state.db
        all_alerts = db.get_active_alerts()
        breaches = [
            a for a in all_alerts if a.get("alert_type") in ("geofence_breach", "geofence_dwell")
        ][:limit]

    return {"count": len(breaches), "breaches": breaches}


@router.get("/geofences/{fence_id}")
async def get_geofence(request: Request, fence_id: int) -> dict:
    """Get a single geofence by ID."""
    manager = _get_manager(request)
    fence = manager.get_fence(fence_id)
    if fence is None:
        raise HTTPException(status_code=404, detail="Geofence not found")

    return {
        "id": fence.id,
        "name": fence.name,
        "fence_type": fence.fence_type.value,
        "center_lat": fence.center_lat,
        "center_lon": fence.center_lon,
        "radius_m": fence.radius_m,
        "polygon_points": fence.polygon_points,
        "node_filter": fence.node_filter,
        "trigger_on": fence.trigger_on.value,
        "enabled": fence.enabled,
    }


@router.put("/geofences/{fence_id}")
async def update_geofence(request: Request, fence_id: int) -> dict:
    """Update a geofence's properties."""
    body = await request.json()
    manager = _get_manager(request)

    updated = manager.update_fence(fence_id, body)
    if not updated:
        raise HTTPException(status_code=404, detail="Geofence not found")

    return {"id": fence_id, "status": "updated"}


@router.delete("/geofences/{fence_id}")
async def delete_geofence(request: Request, fence_id: int) -> dict:
    """Delete a geofence. Requires confirmed=true in body or query."""
    # Check for confirmation in query or body
    confirmed = False
    try:
        body = await request.json()
        confirmed = body.get("confirmed", False)
    except Exception:
        pass

    if not confirmed:
        confirmed_q = request.query_params.get("confirmed", "false")
        confirmed = confirmed_q.lower() in ("true", "1", "yes")

    if not confirmed:
        raise HTTPException(
            status_code=400,
            detail="Deletion requires 'confirmed: true'",
        )

    manager = _get_manager(request)
    deleted = manager.delete_fence(fence_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Geofence not found")

    return {"id": fence_id, "status": "deleted"}
