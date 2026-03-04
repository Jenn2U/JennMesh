"""Edge association API endpoints.

Provides CRUD for JennEdge ↔ mesh radio cross-references and combined status queries.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["edge-associations"])


def _get_manager(request: Request):
    """Get or lazily create EdgeAssociationManager."""
    manager = getattr(request.app.state, "edge_association_manager", None)
    if manager is not None:
        return manager
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    from jenn_mesh.core.edge_association_manager import EdgeAssociationManager

    manager = EdgeAssociationManager(db=db)
    request.app.state.edge_association_manager = manager
    return manager


# ── Request/Response models ───────────────────────────────────────────


class CreateAssociationRequest(BaseModel):
    edge_device_id: str = Field(description="JennEdge device identifier")
    node_id: str = Field(description="Mesh radio node_id")
    edge_hostname: str | None = Field(default=None, description="Device hostname")
    edge_ip: str | None = Field(default=None, description="Device IP address")
    association_type: str = Field(
        default="co-located",
        description="Type: co-located, usb-connected, bluetooth",
    )


class UpdateAssociationRequest(BaseModel):
    node_id: str | None = None
    edge_hostname: str | None = None
    edge_ip: str | None = None
    association_type: str | None = None
    status: str | None = None


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/edge-associations")
async def create_association(request: Request, body: CreateAssociationRequest) -> dict:
    """Create a new edge-to-radio association."""
    manager = _get_manager(request)
    try:
        assoc = manager.create_association(
            edge_device_id=body.edge_device_id,
            node_id=body.node_id,
            edge_hostname=body.edge_hostname,
            edge_ip=body.edge_ip,
            association_type=body.association_type,
        )
        return {
            "status": "ok",
            "association_id": assoc.id,
            "edge_device_id": assoc.edge_device_id,
            "node_id": assoc.node_id,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/edge-associations")
async def list_associations(request: Request, status: str | None = None) -> dict:
    """List all edge-radio associations."""
    manager = _get_manager(request)
    associations = manager.list_associations(status=status)
    return {
        "status": "ok",
        "count": len(associations),
        "associations": associations,
    }


@router.get("/edge-associations/by-edge/{edge_device_id}")
async def get_by_edge(request: Request, edge_device_id: str) -> dict:
    """Get association for a JennEdge device."""
    manager = _get_manager(request)
    assoc = manager.get_by_edge(edge_device_id)
    if assoc is None:
        raise HTTPException(
            status_code=404,
            detail=f"No association for edge device '{edge_device_id}'",
        )
    return {"status": "ok", "association": assoc}


@router.get("/edge-associations/by-node/{node_id}")
async def get_by_node(request: Request, node_id: str) -> dict:
    """Get association for a mesh radio node."""
    manager = _get_manager(request)
    assoc = manager.get_by_node(node_id)
    if assoc is None:
        raise HTTPException(
            status_code=404,
            detail=f"No association for node '{node_id}'",
        )
    return {"status": "ok", "association": assoc}


@router.get("/edge-associations/status/{edge_device_id}")
async def get_combined_status(request: Request, edge_device_id: str) -> dict:
    """Get combined edge + radio status for cross-reference display.

    The key query: "Edge node X is offline — but its radio is still transmitting"
    """
    manager = _get_manager(request)
    status = manager.get_combined_status(edge_device_id)
    if status is None:
        raise HTTPException(
            status_code=404,
            detail=f"No association for edge device '{edge_device_id}'",
        )
    return {
        "status": "ok",
        "edge_device_id": status.edge_device_id,
        "edge_hostname": status.edge_hostname,
        "node_id": status.node_id,
        "radio_online": status.radio_online,
        "radio_battery": status.radio_battery,
        "radio_signal_rssi": status.radio_signal_rssi,
        "radio_signal_snr": status.radio_signal_snr,
        "radio_latitude": status.radio_latitude,
        "radio_longitude": status.radio_longitude,
        "radio_last_seen": (str(status.radio_last_seen) if status.radio_last_seen else None),
        "mesh_status": status.mesh_status,
        "association_status": status.association_status.value,
    }


@router.put("/edge-associations/{edge_device_id}")
async def update_association(
    request: Request,
    edge_device_id: str,
    body: UpdateAssociationRequest,
) -> dict:
    """Update an edge association."""
    manager = _get_manager(request)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    ok = manager.update_association(edge_device_id, **updates)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"No association for edge device '{edge_device_id}'",
        )
    return {"status": "ok", "edge_device_id": edge_device_id}


@router.delete("/edge-associations/{edge_device_id}")
async def delete_association(request: Request, edge_device_id: str) -> dict:
    """Delete an edge-radio association."""
    manager = _get_manager(request)
    ok = manager.delete_association(edge_device_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"No association for edge device '{edge_device_id}'",
        )
    return {"status": "ok", "edge_device_id": edge_device_id}


@router.post("/edge-associations/update-stale")
async def update_stale(request: Request) -> dict:
    """Mark stale associations (radio not seen > 1 hour)."""
    manager = _get_manager(request)
    count = manager.update_stale_associations()
    return {"status": "ok", "stale_count": count}
