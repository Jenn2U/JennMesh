"""Application lifespan — startup / shutdown lifecycle for JennMesh dashboard.

Follows JennSentry's ``@asynccontextmanager`` lifespan pattern from
``jenn_sentry/dashboard/dependencies.py``.
"""

from __future__ import annotations

import asyncio
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

    # Best-effort config queue manager init
    if (
        not hasattr(app.state, "config_queue_manager")
        and getattr(app.state, "db", None) is not None
    ):
        try:
            from jenn_mesh.core.config_queue_manager import ConfigQueueManager

            app.state.config_queue_manager = ConfigQueueManager(db=app.state.db)
        except Exception:
            logger.exception("Config queue init failed — queue features unavailable")

    # Best-effort config rollback manager init (before bulk_push and drift so they can use it)
    if (
        not hasattr(app.state, "config_rollback_manager")
        and getattr(app.state, "db", None) is not None
    ):
        try:
            from jenn_mesh.core.config_rollback import ConfigRollbackManager

            app.state.config_rollback_manager = ConfigRollbackManager(db=app.state.db)
        except Exception:
            logger.exception("Config rollback init failed — rollback features unavailable")

    # Best-effort workbench init (degrade gracefully if DB unavailable)
    if not hasattr(app.state, "workbench") and getattr(app.state, "db", None) is not None:
        try:
            from jenn_mesh.core.bulk_push import BulkPushManager
            from jenn_mesh.core.workbench_manager import WorkbenchManager

            app.state.workbench = WorkbenchManager(app.state.db)
            config_queue = getattr(app.state, "config_queue_manager", None)
            rollback_mgr = getattr(app.state, "config_rollback_manager", None)
            app.state.bulk_push = BulkPushManager(
                app.state.db, config_queue=config_queue, rollback_manager=rollback_mgr
            )
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

    # Best-effort drift remediation manager init (wires config_queue for retry-on-fail)
    if (
        not hasattr(app.state, "drift_remediation_manager")
        and getattr(app.state, "db", None) is not None
    ):
        try:
            from jenn_mesh.core.drift_remediation import DriftRemediationManager

            config_queue = getattr(app.state, "config_queue_manager", None)
            rollback_mgr = getattr(app.state, "config_rollback_manager", None)
            app.state.drift_remediation_manager = DriftRemediationManager(
                db=app.state.db, config_queue=config_queue, rollback_manager=rollback_mgr
            )
        except Exception:
            logger.exception("Drift remediation init failed — remediation features unavailable")

    # Best-effort failover manager init
    if not hasattr(app.state, "failover_manager") and getattr(app.state, "db", None) is not None:
        try:
            from jenn_mesh.core.failover_manager import FailoverManager

            app.state.failover_manager = FailoverManager(db=app.state.db)
        except Exception:
            logger.exception("Failover manager init failed — failover features unavailable")

    # Best-effort mesh watchdog init
    if not hasattr(app.state, "mesh_watchdog") and getattr(app.state, "db", None) is not None:
        try:
            from jenn_mesh.core.mesh_watchdog import MeshWatchdog, is_watchdog_enabled

            if is_watchdog_enabled():
                app.state.mesh_watchdog = MeshWatchdog(db=app.state.db)
            else:
                logger.info("Mesh watchdog disabled via MESH_WATCHDOG_ENABLED")
        except Exception:
            logger.exception("Mesh watchdog init failed — watchdog features unavailable")

    # Best-effort sync relay manager init
    if not hasattr(app.state, "sync_relay_manager") and getattr(app.state, "db", None) is not None:
        try:
            from jenn_mesh.core.sync_relay_manager import SyncRelayManager

            app.state.sync_relay_manager = SyncRelayManager(db=app.state.db)
        except Exception:
            logger.exception("Sync relay init failed — sync relay features unavailable")

    if not hasattr(app.state, "startup_time"):
        app.state.startup_time = datetime.now(timezone.utc)

    # Start config queue retry loop if manager available
    _retry_task = None
    cq_manager = getattr(app.state, "config_queue_manager", None)
    if cq_manager is not None:
        try:
            from jenn_mesh.core.config_queue_manager import retry_loop_task

            _retry_task = asyncio.create_task(retry_loop_task(cq_manager))
            logger.info("Config queue retry loop started")
        except Exception:
            logger.exception("Failed to start config queue retry loop")

    # Start mesh watchdog loop if watchdog available
    _watchdog_task = None
    wd_manager = getattr(app.state, "mesh_watchdog", None)
    if wd_manager is not None:
        try:
            from jenn_mesh.core.mesh_watchdog import watchdog_loop_task

            _watchdog_task = asyncio.create_task(watchdog_loop_task(wd_manager))
            logger.info("Mesh watchdog loop started")
        except Exception:
            logger.exception("Failed to start mesh watchdog loop")

    logger.info("JennMesh dashboard started (v%s)", __version__)

    yield

    # Cancel mesh watchdog loop
    if _watchdog_task is not None:
        _watchdog_task.cancel()
        try:
            await _watchdog_task
        except asyncio.CancelledError:
            pass
        logger.info("Mesh watchdog loop stopped")

    # Cancel config queue retry loop
    if _retry_task is not None:
        _retry_task.cancel()
        try:
            await _retry_task
        except asyncio.CancelledError:
            pass
        logger.info("Config queue retry loop stopped")

    logger.info("JennMesh dashboard shutting down")
