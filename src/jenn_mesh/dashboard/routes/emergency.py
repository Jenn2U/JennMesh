"""Emergency broadcast API routes — send, list, and track emergency alerts."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from jenn_mesh.models.emergency import EMERGENCY_CHANNEL_INDEX

router = APIRouter(tags=["emergency"])


class EmergencyBroadcastRequest(BaseModel):
    """Request body for sending an emergency broadcast."""

    type: str = Field(description="Emergency type (evacuation, network_down, etc.)")
    message: str = Field(description="Human-readable emergency message")
    confirmed: bool = Field(
        default=False,
        description="Must be true — safety gate for irreversible action",
    )
    sender: str = Field(default="dashboard", description="Who is initiating the broadcast")
    channel_index: int = Field(
        default=EMERGENCY_CHANNEL_INDEX,
        description="Meshtastic channel (3 = Emergency)",
    )


@router.post("/emergency/broadcast")
async def send_emergency_broadcast(request: Request, body: EmergencyBroadcastRequest) -> dict:
    """Send an emergency broadcast to all field radios via LoRa mesh.

    Requires `confirmed: true` in the request body — this is an irreversible action
    that sends a message to all radios in the fleet on the Emergency channel.
    """
    manager = getattr(request.app.state, "emergency_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Emergency broadcast system unavailable")

    if not body.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Emergency broadcasts require explicit confirmation. Set confirmed=true.",
        )

    try:
        broadcast = manager.create_broadcast(
            broadcast_type=body.type,
            message=body.message,
            sender=body.sender,
            confirmed=body.confirmed,
            channel_index=body.channel_index,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {
        "broadcast_id": broadcast.id,
        "type": broadcast.broadcast_type.value,
        "message": broadcast.message,
        "status": broadcast.status.value,
        "channel_index": broadcast.channel_index,
        "created_at": broadcast.created_at.isoformat(),
    }


@router.get("/emergency/broadcasts")
async def list_emergency_broadcasts(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """List emergency broadcast history, most recent first."""
    manager = getattr(request.app.state, "emergency_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Emergency broadcast system unavailable")

    broadcasts = manager.list_broadcasts(limit=limit)
    return {
        "count": len(broadcasts),
        "limit": limit,
        "broadcasts": broadcasts,
    }


@router.get("/emergency/broadcast/{broadcast_id}")
async def get_emergency_broadcast(request: Request, broadcast_id: int) -> dict:
    """Get a specific emergency broadcast by ID."""
    manager = getattr(request.app.state, "emergency_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Emergency broadcast system unavailable")

    broadcast = manager.get_broadcast(broadcast_id)
    if broadcast is None:
        raise HTTPException(status_code=404, detail="Broadcast not found")

    return broadcast


@router.get("/emergency/status")
async def fleet_emergency_status(request: Request) -> dict:
    """Get fleet-level emergency broadcast status.

    Returns active broadcast count, last broadcast time, and recent broadcasts.
    """
    manager = getattr(request.app.state, "emergency_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Emergency broadcast system unavailable")

    return manager.get_fleet_emergency_status()
