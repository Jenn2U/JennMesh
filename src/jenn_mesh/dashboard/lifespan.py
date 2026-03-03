"""Application lifespan — startup / shutdown lifecycle for JennMesh dashboard.

Follows JennSentry's ``@asynccontextmanager`` lifespan pattern from
``jenn_sentry/dashboard/dependencies.py``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import FastAPI

from jenn_mesh import __version__
from jenn_mesh.dashboard.logging_config import configure_logging
from jenn_mesh.db import MeshDatabase

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage dashboard startup and shutdown.

    Startup:
        1. Configure logging
        2. Initialise MeshDatabase (use injected test DB if present)
        3. Create WorkbenchManager + BulkPushManager singletons
        4. Record startup time (used by /health)

    Shutdown:
        1. Log graceful shutdown
    """
    configure_logging()

    # If create_app(db=...) already populated state (test path), skip DB init.
    # httpx ASGITransport doesn't fire lifespan events, so tests rely on
    # create_app() to set state directly. In production this code path runs.
    if not hasattr(app.state, "db"):
        db = getattr(app.state, "_test_db", None)
        if db is None:
            try:
                db = MeshDatabase()
            except Exception:
                logger.exception("Failed to initialise database — dashboard will run degraded")
                db = None
        app.state.db = db

    # Best-effort workbench init (degrade gracefully if DB unavailable)
    if not hasattr(app.state, "workbench") and getattr(app.state, "db", None) is not None:
        try:
            from jenn_mesh.core.bulk_push import BulkPushManager
            from jenn_mesh.core.workbench_manager import WorkbenchManager

            app.state.workbench = WorkbenchManager(app.state.db)
            app.state.bulk_push = BulkPushManager(app.state.db)
        except Exception:
            logger.exception("Workbench init failed — workbench features unavailable")

    # Best-effort emergency manager init
    if not hasattr(app.state, "emergency_manager") and getattr(app.state, "db", None) is not None:
        try:
            from jenn_mesh.core.emergency_manager import EmergencyBroadcastManager

            app.state.emergency_manager = EmergencyBroadcastManager(db=app.state.db)
        except Exception:
            logger.exception("Emergency manager init failed — emergency features unavailable")

    # Best-effort recovery manager init
    if not hasattr(app.state, "recovery_manager") and getattr(app.state, "db", None) is not None:
        try:
            from jenn_mesh.core.recovery_manager import RecoveryManager

            app.state.recovery_manager = RecoveryManager(db=app.state.db)
        except Exception:
            logger.exception("Recovery manager init failed — recovery features unavailable")

    if not hasattr(app.state, "startup_time"):
        app.state.startup_time = datetime.now(timezone.utc)

    logger.info("JennMesh dashboard started (v%s)", __version__)

    yield

    logger.info("JennMesh dashboard shutting down")
