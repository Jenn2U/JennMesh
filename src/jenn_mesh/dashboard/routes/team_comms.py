"""Team communication API endpoints.

Provides send/receive team messages, message history, and delivery status.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["team-comms"])


def _get_manager(request: Request):
    """Get or lazily create TeamCommsManager."""
    manager = getattr(request.app.state, "team_comms_manager", None)
    if manager is not None:
        return manager
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    from jenn_mesh.core.team_comms_manager import TeamCommsManager

    manager = TeamCommsManager(db=db)
    request.app.state.team_comms_manager = manager
    return manager


# ── Request/Response models ───────────────────────────────────────────


class SendMessageRequest(BaseModel):
    channel: str = Field(
        default="broadcast",
        description="Message channel: broadcast, team, or direct",
    )
    sender: str = Field(default="dashboard", description="Sender identifier")
    message: str = Field(description="Message text (max 220 chars)")
    recipient: str | None = Field(
        default=None,
        description="Target node_id (direct) or team name (team)",
    )
    confirmed: bool = Field(
        default=False,
        description="Safety gate: must be True to send",
    )


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/team-comms/send")
async def send_message(request: Request, body: SendMessageRequest) -> dict:
    """Send a team communication message over the mesh."""
    if not body.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Team messages require explicit confirmation. Set confirmed=True.",
        )
    manager = _get_manager(request)
    try:
        msg = manager.send_message(
            channel=body.channel,
            sender=body.sender,
            message=body.message,
            recipient=body.recipient,
        )
        return {
            "status": "ok",
            "message_id": msg.id,
            "delivery_status": msg.status.value,
            "wire_format": msg.wire_format,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/team-comms/messages")
async def list_messages(
    request: Request,
    channel: str | None = None,
    limit: int = 50,
    hours: int | None = None,
) -> dict:
    """List team messages with optional filters."""
    manager = _get_manager(request)
    messages = manager.list_messages(channel=channel, limit=limit, hours=hours)
    return {"status": "ok", "count": len(messages), "messages": messages}


@router.get("/team-comms/messages/{msg_id}")
async def get_message(request: Request, msg_id: int) -> dict:
    """Get a single team message by ID."""
    manager = _get_manager(request)
    msg = manager.get_message(msg_id)
    if msg is None:
        raise HTTPException(status_code=404, detail=f"Message {msg_id} not found")
    return {"status": "ok", "message": msg}


@router.post("/team-comms/messages/{msg_id}/mark-sent")
async def mark_sent(request: Request, msg_id: int) -> dict:
    """Mark a message as sent (agent ACK)."""
    manager = _get_manager(request)
    ok = manager.mark_sent(msg_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Message {msg_id} not found")
    return {"status": "ok", "message_id": msg_id, "delivery_status": "sent"}


@router.post("/team-comms/messages/{msg_id}/mark-delivered")
async def mark_delivered(request: Request, msg_id: int) -> dict:
    """Mark a message as delivered (mesh echo received)."""
    manager = _get_manager(request)
    ok = manager.mark_delivered(msg_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Message {msg_id} not found")
    return {"status": "ok", "message_id": msg_id, "delivery_status": "delivered"}
