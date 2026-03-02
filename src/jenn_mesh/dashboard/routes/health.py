"""Health check endpoint for the JennMesh dashboard.

Returns comprehensive component health — DB, workbench, uptime, schema version.
Pattern follows JennSentry ``page_routes.py`` health endpoint.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from jenn_mesh import __version__
from jenn_mesh.db import SCHEMA_VERSION

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(request: Request) -> dict:
    """Comprehensive dashboard health check."""
    components: dict = {}
    overall = "healthy"

    # 1. Database
    db = getattr(request.app.state, "db", None)
    if db is not None:
        try:
            with db.connection() as conn:
                conn.execute("SELECT 1")
            components["database"] = {"status": "healthy", "schema_version": SCHEMA_VERSION}
        except Exception as exc:
            components["database"] = {"status": "degraded", "error": str(exc)}
            overall = "degraded"
            logger.warning("Health check: database degraded — %s", exc)
    else:
        components["database"] = {"status": "unavailable"}
        overall = "degraded"

    # 2. Workbench manager
    has_workbench = getattr(request.app.state, "workbench", None) is not None
    components["workbench"] = {"status": "healthy" if has_workbench else "unavailable"}

    # 3. Bulk push manager
    has_bulk_push = getattr(request.app.state, "bulk_push", None) is not None
    components["bulk_push"] = {"status": "healthy" if has_bulk_push else "unavailable"}

    # 4. Uptime
    startup_time = getattr(request.app.state, "startup_time", None)
    if startup_time is not None:
        uptime = (datetime.now(timezone.utc) - startup_time).total_seconds()
        components["uptime_seconds"] = round(uptime, 1)

    return {
        "status": overall,
        "version": __version__,
        "service": "jenn-mesh",
        "schema_version": SCHEMA_VERSION,
        "components": components,
    }
