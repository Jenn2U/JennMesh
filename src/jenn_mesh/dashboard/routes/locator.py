"""Locator API routes — lost node queries, GPS position data."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from jenn_mesh.locator.finder import LostNodeFinder
from jenn_mesh.locator.tracker import PositionTracker
from jenn_mesh.models.location import LostNodeQuery

router = APIRouter(tags=["locator"])


@router.get("/locate/{node_id}")
async def locate_node(
    request: Request,
    node_id: str,
    radius: float = Query(5000.0, description="Search radius in meters"),
    max_age: float = Query(72.0, description="Max position age in hours"),
) -> dict:
    """Locate a lost mesh node or edge device.

    Returns last known GPS position, nearby active nodes, and confidence level.
    """
    db = request.app.state.db
    finder = LostNodeFinder(db)

    result = finder.locate(
        LostNodeQuery(
            target_node_id=node_id,
            search_radius_meters=radius,
            max_age_hours=max_age,
        )
    )

    return {
        "target_node_id": result.target_node_id,
        "is_found": result.is_found,
        "confidence": result.confidence,
        "last_known_position": (
            {
                "latitude": result.last_known_position.latitude,
                "longitude": result.last_known_position.longitude,
                "altitude": result.last_known_position.altitude,
                "timestamp": result.last_known_position.timestamp.isoformat(),
            }
            if result.last_known_position
            else None
        ),
        "position_age_hours": result.position_age_hours,
        "nearby_nodes": [
            {
                "node_id": n.node_id,
                "distance_meters": n.distance_meters,
                "is_online": n.is_online,
                "latitude": n.position.latitude,
                "longitude": n.position.longitude,
            }
            for n in result.nearby_nodes
        ],
        "associated_edge_node": result.associated_edge_node,
    }


@router.get("/positions")
async def all_positions(request: Request) -> dict:
    """Get latest GPS positions for all devices (fleet map data)."""
    db = request.app.state.db
    tracker = PositionTracker(db)
    positions = tracker.get_all_latest_positions()

    return {
        "count": len(positions),
        "positions": [
            {
                "node_id": p.node_id,
                "latitude": p.latitude,
                "longitude": p.longitude,
                "altitude": p.altitude,
                "timestamp": p.timestamp.isoformat(),
            }
            for p in positions
        ],
    }


@router.get("/positions/{node_id}")
async def node_position(request: Request, node_id: str) -> dict:
    """Get latest position for a specific node."""
    db = request.app.state.db
    tracker = PositionTracker(db)
    pos = tracker.get_latest_position(node_id)

    if pos is None:
        raise HTTPException(status_code=404, detail="No position data")

    age = tracker.get_position_age_hours(node_id)
    return {
        "node_id": pos.node_id,
        "latitude": pos.latitude,
        "longitude": pos.longitude,
        "altitude": pos.altitude,
        "timestamp": pos.timestamp.isoformat(),
        "age_hours": round(age, 2) if age else None,
        "source": pos.source,
    }
