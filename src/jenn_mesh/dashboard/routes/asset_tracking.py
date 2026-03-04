"""Asset tracking API endpoints.

Provides CRUD for tracked assets, position trails, and fleet-wide asset status.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["asset-tracking"])


def _get_tracker(request: Request):
    """Get or lazily create AssetTracker."""
    tracker = getattr(request.app.state, "asset_tracker", None)
    if tracker is not None:
        return tracker
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    from jenn_mesh.core.asset_tracker import AssetTracker

    tracker = AssetTracker(db=db)
    request.app.state.asset_tracker = tracker
    return tracker


# ── Request/Response models ───────────────────────────────────────────


class RegisterAssetRequest(BaseModel):
    name: str = Field(description="Human-readable asset name")
    asset_type: str = Field(description="Type: vehicle, equipment, personnel, etc.")
    node_id: str = Field(description="Associated mesh radio node_id")
    zone: str | None = Field(default=None, description="Assigned zone/area")
    team: str | None = Field(default=None, description="Assigned team")
    project: str | None = Field(default=None, description="Assigned project")
    metadata: dict | None = Field(default=None, description="Extra metadata")


class UpdateAssetRequest(BaseModel):
    name: str | None = None
    asset_type: str | None = None
    node_id: str | None = None
    zone: str | None = None
    team: str | None = None
    project: str | None = None
    status: str | None = None


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/assets")
async def register_asset(request: Request, body: RegisterAssetRequest) -> dict:
    """Register a new trackable asset."""
    tracker = _get_tracker(request)
    try:
        asset = tracker.register_asset(
            name=body.name,
            asset_type=body.asset_type,
            node_id=body.node_id,
            zone=body.zone,
            team=body.team,
            project=body.project,
            metadata=body.metadata,
        )
        return {
            "status": "ok",
            "asset_id": asset.id,
            "name": asset.name,
            "node_id": asset.node_id,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/assets")
async def list_assets(
    request: Request,
    asset_type: str | None = None,
    zone: str | None = None,
    team: str | None = None,
    status: str | None = None,
) -> dict:
    """List tracked assets with optional filters."""
    tracker = _get_tracker(request)
    assets = tracker.list_assets(
        asset_type=asset_type, zone=zone, team=team, status=status
    )
    return {"status": "ok", "count": len(assets), "assets": assets}


@router.get("/assets/{asset_id}")
async def get_asset(request: Request, asset_id: int) -> dict:
    """Get a single asset by ID."""
    tracker = _get_tracker(request)
    asset = tracker.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")
    return {"status": "ok", "asset": asset}


@router.put("/assets/{asset_id}")
async def update_asset(
    request: Request, asset_id: int, body: UpdateAssetRequest
) -> dict:
    """Update asset fields."""
    tracker = _get_tracker(request)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    ok = tracker.update_asset(asset_id, **updates)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")
    return {"status": "ok", "asset_id": asset_id}


@router.delete("/assets/{asset_id}")
async def delete_asset(request: Request, asset_id: int) -> dict:
    """Delete an asset."""
    tracker = _get_tracker(request)
    ok = tracker.delete_asset(asset_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")
    return {"status": "ok", "asset_id": asset_id}


@router.get("/assets/by-node/{node_id}")
async def get_asset_by_node(request: Request, node_id: str) -> dict:
    """Get asset by associated mesh node_id."""
    tracker = _get_tracker(request)
    asset = tracker.get_asset_by_node(node_id)
    if asset is None:
        raise HTTPException(
            status_code=404, detail=f"No asset associated with node {node_id}"
        )
    return {"status": "ok", "asset": asset}


@router.get("/assets/{asset_id}/trail")
async def get_asset_trail(
    request: Request,
    asset_id: int,
    hours: int = 24,
    limit: int = 500,
) -> dict:
    """Get position trail for an asset with speed and heading."""
    tracker = _get_tracker(request)
    asset = tracker.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")

    trail = tracker.get_trail(
        node_id=asset["node_id"], hours=hours, limit=limit
    )
    return {
        "status": "ok",
        "asset_id": trail.asset_id,
        "asset_name": trail.asset_name,
        "node_id": trail.node_id,
        "positions": [
            {
                "latitude": p.latitude,
                "longitude": p.longitude,
                "altitude": p.altitude,
                "speed_mps": p.speed_mps,
                "heading_deg": p.heading_deg,
                "timestamp": str(p.timestamp) if p.timestamp else None,
            }
            for p in trail.positions
        ],
        "total_distance_m": trail.total_distance_m,
        "avg_speed_mps": trail.avg_speed_mps,
        "time_span_hours": trail.time_span_hours,
        "position_count": len(trail.positions),
    }


@router.post("/assets/update-statuses")
async def update_asset_statuses(request: Request) -> dict:
    """Update all asset statuses based on node activity."""
    tracker = _get_tracker(request)
    changed = tracker.update_asset_statuses()
    return {"status": "ok", "assets_updated": changed}
