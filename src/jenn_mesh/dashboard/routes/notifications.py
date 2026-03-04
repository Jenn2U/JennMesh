"""Notification channel and rule management API endpoints.

Provides CRUD for notification channels (Slack, Teams, Email, Webhook),
notification rules (alert routing), and test-fire endpoint.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["notifications"])


def _get_dispatcher(request: Request):
    """Get or lazily create NotificationDispatcher."""
    dispatcher = getattr(request.app.state, "notification_dispatcher", None)
    if dispatcher is not None:
        return dispatcher
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    from jenn_mesh.core.notification_dispatcher import NotificationDispatcher

    wh_mgr = getattr(request.app.state, "webhook_manager", None)
    dispatcher = NotificationDispatcher(db=db, webhook_manager=wh_mgr)
    request.app.state.notification_dispatcher = dispatcher
    return dispatcher


# ── Request models ────────────────────────────────────────────────────


class CreateChannelRequest(BaseModel):
    name: str = Field(description="Channel name")
    channel_type: str = Field(description="slack, teams, email, or webhook")
    config_json: str = Field(default="{}", description="Channel-specific config JSON")
    is_active: bool = Field(default=True)


class UpdateChannelRequest(BaseModel):
    name: str | None = None
    channel_type: str | None = None
    config_json: str | None = None
    is_active: bool | None = None


class CreateRuleRequest(BaseModel):
    name: str = Field(description="Rule name")
    alert_types: list[str] = Field(default_factory=list)
    severities: list[str] = Field(default_factory=list)
    channel_ids: list[int] = Field(default_factory=list)
    is_active: bool = Field(default=True)


class UpdateRuleRequest(BaseModel):
    name: str | None = None
    alert_types: list[str] | None = None
    severities: list[str] | None = None
    channel_ids: list[int] | None = None
    is_active: bool | None = None


class TestNotificationRequest(BaseModel):
    channel_id: int = Field(description="Channel ID to test")
    alert_type: str = Field(default="test", description="Test alert type")
    severity: str = Field(default="info", description="Test severity")


# ── Channel endpoints ─────────────────────────────────────────────────


@router.get("/notifications/channels")
async def list_channels(request: Request, active_only: bool = False) -> dict:
    """List all notification channels."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    channels = db.list_notification_channels(active_only=active_only)
    return {"channels": channels, "count": len(channels)}


@router.post("/notifications/channels")
async def create_channel(request: Request, body: CreateChannelRequest) -> dict:
    """Create a notification channel."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    ch_id = db.create_notification_channel(
        name=body.name,
        channel_type=body.channel_type,
        config_json=body.config_json,
    )
    ch = db.get_notification_channel(ch_id)
    return ch or {"id": ch_id}


@router.get("/notifications/channels/{channel_id}")
async def get_channel(request: Request, channel_id: int) -> dict:
    """Get a single notification channel."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    ch = db.get_notification_channel(channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    return ch


@router.put("/notifications/channels/{channel_id}")
async def update_channel(
    request: Request, channel_id: int, body: UpdateChannelRequest
) -> dict:
    """Update a notification channel."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    updated = db.update_notification_channel(channel_id, **updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Channel not found")
    return db.get_notification_channel(channel_id) or {"id": channel_id}


@router.delete("/notifications/channels/{channel_id}")
async def delete_channel(request: Request, channel_id: int) -> dict:
    """Delete a notification channel."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    deleted = db.delete_notification_channel(channel_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Channel not found")
    return {"status": "deleted", "id": channel_id}


# ── Test fire ─────────────────────────────────────────────────────────


@router.post("/notifications/test")
async def test_notification(request: Request, body: TestNotificationRequest) -> dict:
    """Test-fire a notification to a specific channel."""
    dispatcher = _get_dispatcher(request)
    data = {"node_id": "test-node", "message": "JennMesh notification test"}

    # Override get_channels_for_alert to target only the specified channel
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    ch = db.get_notification_channel(body.channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    # Direct delivery to the specific channel
    ch_type = ch.get("channel_type", "")
    config = json.loads(ch.get("config_json", "{}"))
    try:
        if ch_type == "slack":
            dispatcher._send_slack(config, body.alert_type, body.severity, data)
        elif ch_type == "teams":
            dispatcher._send_teams(config, body.alert_type, body.severity, data)
        elif ch_type == "email":
            dispatcher._send_email(config, body.alert_type, body.severity, data)
        elif ch_type == "webhook":
            dispatcher._send_webhook(config, body.alert_type, body.severity, data)
        else:
            return {"status": "error", "error": f"Unknown channel type: {ch_type}"}
        return {"status": "success", "channel_id": body.channel_id, "channel_type": ch_type}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ── Rule endpoints ────────────────────────────────────────────────────


@router.get("/notifications/rules")
async def list_rules(request: Request, active_only: bool = False) -> dict:
    """List all notification rules."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    rules = db.list_notification_rules(active_only=active_only)
    # Parse JSON string fields for response
    for rule in rules:
        for field in ("alert_types", "severities", "channel_ids"):
            if isinstance(rule.get(field), str):
                rule[field] = json.loads(rule[field])
    return {"rules": rules, "count": len(rules)}


@router.post("/notifications/rules")
async def create_rule(request: Request, body: CreateRuleRequest) -> dict:
    """Create a notification rule."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    rule_id = db.create_notification_rule(
        name=body.name,
        alert_types=json.dumps(body.alert_types),
        severities=json.dumps(body.severities),
        channel_ids=json.dumps(body.channel_ids),
    )
    rule = db.get_notification_rule(rule_id)
    if rule:
        for field in ("alert_types", "severities", "channel_ids"):
            if isinstance(rule.get(field), str):
                rule[field] = json.loads(rule[field])
    return rule or {"id": rule_id}


@router.put("/notifications/rules/{rule_id}")
async def update_rule(
    request: Request, rule_id: int, body: UpdateRuleRequest
) -> dict:
    """Update a notification rule."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    updates = body.model_dump(exclude_none=True)
    # Serialize lists to JSON strings for DB
    for field in ("alert_types", "severities", "channel_ids"):
        if field in updates and isinstance(updates[field], list):
            updates[field] = json.dumps(updates[field])
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    updated = db.update_notification_rule(rule_id, **updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Rule not found")
    rule = db.get_notification_rule(rule_id)
    if rule:
        for field in ("alert_types", "severities", "channel_ids"):
            if isinstance(rule.get(field), str):
                rule[field] = json.loads(rule[field])
    return rule or {"id": rule_id}


@router.delete("/notifications/rules/{rule_id}")
async def delete_rule(request: Request, rule_id: int) -> dict:
    """Delete a notification rule."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    deleted = db.delete_notification_rule(rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"status": "deleted", "id": rule_id}
