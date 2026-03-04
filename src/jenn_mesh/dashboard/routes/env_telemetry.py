"""Environmental telemetry API routes — sensor data and threshold alerts."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from jenn_mesh.core.env_telemetry import EnvTelemetryManager

router = APIRouter(tags=["env_telemetry"])


class ThresholdUpdate(BaseModel):
    """Request body for updating environmental thresholds."""

    thresholds: list[dict] = Field(
        description="List of threshold configs with metric, min_value, max_value, enabled"
    )


def _get_manager(request: Request) -> EnvTelemetryManager:
    """Get or create an EnvTelemetryManager from request state."""
    mgr = getattr(request.app.state, "env_telemetry_manager", None)
    if mgr is not None:
        return mgr
    db = request.app.state.db
    return EnvTelemetryManager(db)


# ── Specific routes BEFORE the parameterised {node_id} route ──────


@router.get("/environment/fleet/summary")
async def fleet_env_summary(request: Request) -> dict:
    """Get fleet-wide environmental summary with latest readings per node."""
    mgr = _get_manager(request)
    return mgr.get_fleet_summary()


@router.get("/environment/thresholds")
async def get_thresholds(request: Request) -> dict:
    """Get current environmental threshold configuration."""
    mgr = _get_manager(request)
    thresholds = mgr.get_thresholds()
    return {"count": len(thresholds), "thresholds": thresholds}


@router.put("/environment/thresholds")
async def update_thresholds(request: Request, body: ThresholdUpdate) -> dict:
    """Update environmental threshold configuration."""
    mgr = _get_manager(request)
    updated = mgr.update_thresholds(body.thresholds)
    return {"count": len(updated), "thresholds": updated}


@router.get("/environment/alerts")
async def env_alerts(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """Get recent environmental threshold alerts."""
    mgr = _get_manager(request)
    alerts = mgr.get_env_alerts(limit=limit)
    return {"count": len(alerts), "alerts": alerts}


# ── Parameterised route LAST to avoid capturing "fleet", "thresholds", etc. ──


@router.get("/environment/{node_id}")
async def node_env_history(
    request: Request,
    node_id: str,
    since: str = Query(None, description="ISO timestamp filter"),
    limit: int = Query(100, ge=1, le=1000),
) -> dict:
    """Get environmental telemetry history for a specific node."""
    mgr = _get_manager(request)
    readings = mgr.get_node_readings(node_id, since=since, limit=limit)
    return {"node_id": node_id, "count": len(readings), "readings": readings}
