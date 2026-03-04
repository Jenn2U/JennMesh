"""Fleet analytics API routes — trends, metrics, and dashboard summary."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from jenn_mesh.core.fleet_analytics import FleetAnalytics

router = APIRouter(tags=["analytics"])


def _get_analytics(request: Request) -> FleetAnalytics:
    """Get or create a FleetAnalytics from request state."""
    analytics = getattr(request.app.state, "fleet_analytics", None)
    if analytics is not None:
        return analytics
    db = request.app.state.db
    return FleetAnalytics(db)


@router.get("/analytics/uptime")
async def uptime_trends(
    request: Request,
    node_id: str = Query(None),
    days: int = Query(30, ge=1, le=365),
) -> dict:
    """Uptime percentage trends per node."""
    analytics = _get_analytics(request)
    trends = analytics.get_uptime_trends(node_id=node_id, days=days)
    return {"period_days": days, "nodes": trends}


@router.get("/analytics/battery")
async def battery_trends(
    request: Request,
    node_id: str = Query(None),
    days: int = Query(30, ge=1, le=365),
) -> dict:
    """Battery level trends with declining capacity detection."""
    analytics = _get_analytics(request)
    trends = analytics.get_battery_trends(node_id=node_id, days=days)
    return {"period_days": days, "nodes": trends}


@router.get("/analytics/alerts")
async def alert_frequency(
    request: Request,
    days: int = Query(30, ge=1, le=365),
) -> dict:
    """Alert frequency grouped by type and severity."""
    analytics = _get_analytics(request)
    return analytics.get_alert_frequency(days=days)


@router.get("/analytics/messages")
async def message_volume(
    request: Request,
    days: int = Query(7, ge=1, le=90),
) -> dict:
    """Telemetry message volume per node."""
    analytics = _get_analytics(request)
    volumes = analytics.get_message_volume(days=days)
    return {"period_days": days, "nodes": volumes}


@router.get("/analytics/summary")
async def dashboard_summary(request: Request) -> dict:
    """All-in-one analytics dashboard summary."""
    analytics = _get_analytics(request)
    return analytics.get_dashboard_summary()
