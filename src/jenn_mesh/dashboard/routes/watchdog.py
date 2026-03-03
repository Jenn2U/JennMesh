"""Watchdog API routes — status, history, and manual trigger."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["watchdog"])


@router.get("/watchdog/status")
async def watchdog_status(request: Request) -> dict:
    """Current watchdog state — enabled checks, intervals, last run per check."""
    watchdog = getattr(request.app.state, "mesh_watchdog", None)
    if watchdog is None:
        raise HTTPException(status_code=503, detail="Mesh watchdog unavailable")
    return watchdog.get_status()


@router.get("/watchdog/history")
async def watchdog_history(
    request: Request,
    check_name: str | None = None,
    limit: int = 50,
) -> dict:
    """Recent watchdog run audit trail, optionally filtered by check name."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    runs = db.get_recent_watchdog_runs(check_name=check_name, limit=limit)
    return {"count": len(runs), "runs": runs}


@router.post("/watchdog/trigger/{check_name}")
async def trigger_check(request: Request, check_name: str) -> dict:
    """Manually trigger a specific watchdog check on-demand."""
    watchdog = getattr(request.app.state, "mesh_watchdog", None)
    if watchdog is None:
        raise HTTPException(status_code=503, detail="Mesh watchdog unavailable")

    if check_name not in watchdog._check_handlers:
        valid = sorted(watchdog._check_handlers.keys())
        raise HTTPException(
            status_code=404,
            detail=f"Unknown check '{check_name}'. Valid: {valid}",
        )

    handler = watchdog._check_handlers[check_name]
    result = watchdog._run_check(check_name, handler)
    return {"check_name": check_name, "result": result}
