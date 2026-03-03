"""Failover API routes — assess, execute, revert, and monitor automated failovers."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from jenn_mesh.models.failover import (
    FailoverCancelRequest,
    FailoverExecuteRequest,
    FailoverRevertRequest,
)

router = APIRouter(tags=["failover"])


# ------------------------------------------------------------------
# Static paths FIRST — FastAPI matches routes in registration order.
# /failover/active and /failover/check-recoveries must resolve
# before the {node_id} / {event_id} path-parameter routes.
# ------------------------------------------------------------------


@router.get("/failover/active")
async def list_active_failovers(request: Request) -> dict:
    """List all currently active failover events."""
    manager = getattr(request.app.state, "failover_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Failover system unavailable")

    events = manager.list_active_failovers()
    return {"count": len(events), "events": events}


@router.post("/failover/check-recoveries")
async def check_recoveries(request: Request) -> dict:
    """Check if any failed nodes have recovered and auto-revert their failovers.

    For each active failover where the failed node is now online,
    compensations are automatically reverted to restore original config.
    """
    manager = getattr(request.app.state, "failover_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Failover system unavailable")

    result = manager.check_recoveries()
    return result


# ------------------------------------------------------------------
# Path-parameter routes — {node_id} routes for assess / execute / status
# ------------------------------------------------------------------


@router.get("/failover/{node_id}/assess")
async def assess_failover_impact(request: Request, node_id: str) -> dict:
    """Assess what would happen if *node_id* fails.

    Returns dependent nodes, compensation candidates, and suggested
    compensations. Does NOT modify any state — read-only assessment.
    """
    manager = getattr(request.app.state, "failover_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Failover system unavailable")

    try:
        assessment = manager.assess_failover_impact(node_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return assessment


@router.post("/failover/{node_id}/execute")
async def execute_failover(request: Request, node_id: str, body: FailoverExecuteRequest) -> dict:
    """Execute automated failover for a failed relay node.

    Requires ``confirmed: true`` — this applies config changes to
    live mesh nodes via RemoteAdmin.
    """
    manager = getattr(request.app.state, "failover_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Failover system unavailable")

    if not body.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Failover execution requires explicit confirmation. Set confirmed=true.",
        )

    try:
        result = manager.execute_failover(node_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return result


@router.get("/failover/{node_id}/status")
async def get_failover_status(request: Request, node_id: str) -> dict:
    """Get failover status for a specific device.

    Returns active failover events, compensation details, and recent activity.
    """
    manager = getattr(request.app.state, "failover_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Failover system unavailable")

    return manager.get_failover_status(node_id)


# ------------------------------------------------------------------
# {event_id} routes — revert / cancel by failover event ID
# ------------------------------------------------------------------


@router.post("/failover/{event_id}/revert")
async def revert_failover(request: Request, event_id: int, body: FailoverRevertRequest) -> dict:
    """Revert all compensations for a failover event.

    Pushes original config values back to compensation nodes.
    Requires ``confirmed: true``.
    """
    manager = getattr(request.app.state, "failover_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Failover system unavailable")

    if not body.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Failover revert requires explicit confirmation. Set confirmed=true.",
        )

    try:
        result = manager.revert_failover(event_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return result


@router.post("/failover/{event_id}/cancel")
async def cancel_failover(request: Request, event_id: int, body: FailoverCancelRequest) -> dict:
    """Cancel a failover event without reverting compensations.

    Marks the event as cancelled — no config changes are pushed.
    Requires ``confirmed: true``.
    """
    manager = getattr(request.app.state, "failover_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Failover system unavailable")

    if not body.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Failover cancellation requires explicit confirmation. Set confirmed=true.",
        )

    try:
        result = manager.cancel_failover(event_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return result
