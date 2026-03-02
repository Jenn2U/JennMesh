"""Health check endpoint for the JennMesh dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Request

from jenn_mesh import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(request: Request) -> dict:
    """Dashboard health check."""
    db = request.app.state.db
    try:
        with db.connection() as conn:
            conn.execute("SELECT 1")
        db_status = "healthy"
    except Exception:
        db_status = "degraded"

    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "version": __version__,
        "database": db_status,
        "service": "jenn-mesh",
    }
