"""Webhook management API endpoints.

Provides CRUD for webhook registrations, test-fire endpoint verification,
and delivery history.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


def _get_manager(request: Request):
    """Get or lazily create WebhookManager."""
    manager = getattr(request.app.state, "webhook_manager", None)
    if manager is not None:
        return manager
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    from jenn_mesh.core.webhook_manager import WebhookManager

    manager = WebhookManager(db=db)
    request.app.state.webhook_manager = manager
    return manager


# ── Request/Response models ───────────────────────────────────────────


class CreateWebhookRequest(BaseModel):
    name: str = Field(description="Human-readable label")
    url: str = Field(description="HTTP POST target URL")
    secret: str = Field(default="", description="HMAC-SHA256 signing secret")
    event_types: list[str] = Field(
        default_factory=list, description="Event types to subscribe (empty=all)"
    )


class UpdateWebhookRequest(BaseModel):
    name: str | None = None
    url: str | None = None
    secret: str | None = None
    event_types: list[str] | None = None
    is_active: bool | None = None


# ── Endpoints ─────────────────────────────────────────────────────────


@router.get("/webhooks")
async def list_webhooks(request: Request, active_only: bool = False) -> dict:
    """List all registered webhooks."""
    manager = _get_manager(request)
    webhooks = manager.list_webhooks(active_only=active_only)
    # Parse event_types JSON strings for response
    for wh in webhooks:
        if isinstance(wh.get("event_types"), str):
            wh["event_types"] = json.loads(wh["event_types"])
    return {"webhooks": webhooks, "count": len(webhooks)}


@router.post("/webhooks")
async def create_webhook(request: Request, body: CreateWebhookRequest) -> dict:
    """Register a new webhook."""
    manager = _get_manager(request)
    wh = manager.create_webhook(
        name=body.name,
        url=body.url,
        secret=body.secret,
        event_types=body.event_types,
    )
    if isinstance(wh.get("event_types"), str):
        wh["event_types"] = json.loads(wh["event_types"])
    return wh


@router.get("/webhooks/{webhook_id}")
async def get_webhook(request: Request, webhook_id: int) -> dict:
    """Get a single webhook by ID."""
    manager = _get_manager(request)
    wh = manager.get_webhook(webhook_id)
    if wh is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    if isinstance(wh.get("event_types"), str):
        wh["event_types"] = json.loads(wh["event_types"])
    return wh


@router.put("/webhooks/{webhook_id}")
async def update_webhook(
    request: Request, webhook_id: int, body: UpdateWebhookRequest
) -> dict:
    """Update an existing webhook."""
    manager = _get_manager(request)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    updated = manager.update_webhook(webhook_id, **updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Webhook not found")
    wh = manager.get_webhook(webhook_id)
    if wh and isinstance(wh.get("event_types"), str):
        wh["event_types"] = json.loads(wh["event_types"])
    return wh or {"id": webhook_id, "updated": True}


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(request: Request, webhook_id: int) -> dict:
    """Delete a webhook and all its delivery history."""
    manager = _get_manager(request)
    deleted = manager.delete_webhook(webhook_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"status": "deleted", "id": webhook_id}


@router.post("/webhooks/{webhook_id}/test")
async def test_fire_webhook(request: Request, webhook_id: int) -> dict:
    """Fire a test event to verify the webhook endpoint."""
    manager = _get_manager(request)
    result = manager.test_fire(webhook_id)
    if "error" in result and result.get("status") != "error":
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/webhooks/{webhook_id}/deliveries")
async def list_webhook_deliveries(
    request: Request, webhook_id: int, limit: int = 50
) -> dict:
    """List delivery history for a specific webhook."""
    manager = _get_manager(request)
    wh = manager.get_webhook(webhook_id)
    if wh is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    deliveries = manager.db.list_webhook_deliveries(webhook_id, limit=limit)
    return {"deliveries": deliveries, "count": len(deliveries)}
