"""FastAPI dashboard application factory for JennMesh."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from jenn_mesh import __version__
from jenn_mesh.dashboard.error_handlers import register_error_handlers
from jenn_mesh.dashboard.lifespan import lifespan
from jenn_mesh.dashboard.middleware import (
    RateLimitMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
    configure_cors,
)
from jenn_mesh.db import MeshDatabase

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


class _NoCacheAPIMiddleware:
    """Prevent Front Door from caching API responses."""

    def __init__(self, app: object) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: object, send: object) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/api/"):
            original_send = send

            async def send_with_no_cache(message: dict) -> None:
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"cache-control", b"no-store"))
                    message["headers"] = headers
                await original_send(message)

            await self.app(scope, receive, send_with_no_cache)
        else:
            await self.app(scope, receive, send)


def create_app(db: Optional[MeshDatabase] = None) -> FastAPI:
    """Create the JennMesh dashboard FastAPI application.

    Args:
        db: Optional MeshDatabase instance for testing.
            When provided, the lifespan will use this DB instead of creating one.

    Returns:
        Configured FastAPI app.
    """
    root_path = os.environ.get("ROOT_PATH", "")

    app = FastAPI(
        title="JennMesh Dashboard",
        description="Meshtastic fleet management dashboard",
        version=__version__,
        root_path=root_path,
        lifespan=lifespan,
    )

    # Inject test DB — set state directly because httpx ASGITransport
    # does NOT fire ASGI lifespan events, so the lifespan won't run in tests.
    # In production the lifespan handles this; in tests we do it here.
    if db is not None:
        app.state._test_db = db
        app.state.db = db
        try:
            from jenn_mesh.core.config_queue_manager import ConfigQueueManager

            app.state.config_queue_manager = ConfigQueueManager(db=db)
        except Exception:
            pass  # graceful degradation — config queue features unavailable
        try:
            from jenn_mesh.core.config_rollback import ConfigRollbackManager

            app.state.config_rollback_manager = ConfigRollbackManager(db=db)
        except Exception:
            pass  # graceful degradation — config rollback features unavailable
        try:
            from jenn_mesh.core.bulk_push import BulkPushManager
            from jenn_mesh.core.workbench_manager import WorkbenchManager

            app.state.workbench = WorkbenchManager(db)
            config_queue = getattr(app.state, "config_queue_manager", None)
            rollback_mgr = getattr(app.state, "config_rollback_manager", None)
            app.state.bulk_push = BulkPushManager(
                db, config_queue=config_queue, rollback_manager=rollback_mgr
            )
        except Exception:
            pass  # graceful degradation — workbench features unavailable
        try:
            from jenn_mesh.core.emergency_manager import EmergencyBroadcastManager

            app.state.emergency_manager = EmergencyBroadcastManager(db=db)
        except Exception:
            pass  # graceful degradation — emergency features unavailable
        try:
            from jenn_mesh.core.recovery_manager import RecoveryManager

            app.state.recovery_manager = RecoveryManager(db=db)
        except Exception:
            pass  # graceful degradation — recovery features unavailable
        try:
            from jenn_mesh.core.drift_remediation import DriftRemediationManager

            config_queue = getattr(app.state, "config_queue_manager", None)
            rollback_mgr = getattr(app.state, "config_rollback_manager", None)
            app.state.drift_remediation_manager = DriftRemediationManager(
                db=db, config_queue=config_queue, rollback_manager=rollback_mgr
            )
        except Exception:
            pass  # graceful degradation — drift remediation features unavailable
        try:
            from jenn_mesh.core.failover_manager import FailoverManager

            app.state.failover_manager = FailoverManager(db=db)
        except Exception:
            pass  # graceful degradation — failover features unavailable
        try:
            from jenn_mesh.core.mesh_watchdog import MeshWatchdog

            app.state.mesh_watchdog = MeshWatchdog(db=db)
        except Exception:
            pass  # graceful degradation — watchdog features unavailable
        try:
            from jenn_mesh.core.sync_relay_manager import SyncRelayManager

            app.state.sync_relay_manager = SyncRelayManager(db=db)
        except Exception:
            pass  # graceful degradation — sync relay features unavailable
        try:
            from jenn_mesh.core.geofencing import GeofencingManager

            app.state.geofencing_manager = GeofencingManager(db=db)
        except Exception:
            pass  # graceful degradation — geofencing features unavailable
        try:
            from jenn_mesh.core.anomaly_detector import AnomalyDetector

            app.state.anomaly_detector = AnomalyDetector(db=db)
        except Exception:
            pass  # graceful degradation — anomaly detection features unavailable
        try:
            from jenn_mesh.core.alert_summarizer import AlertSummarizer

            app.state.alert_summarizer = AlertSummarizer(db=db)
        except Exception:
            pass  # graceful degradation — alert summarization unavailable
        app.state.startup_time = datetime.now(timezone.utc)

    # --- Error handlers ---
    register_error_handlers(app)

    # --- Middleware stack (outermost first) ---
    # CORS must be added before custom middleware
    configure_cors(app)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(_NoCacheAPIMiddleware)

    # Mount static files
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Set up Jinja2 templates
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.state.templates = templates

    # Register routes
    from jenn_mesh.dashboard.routes.baselines import router as baselines_router
    from jenn_mesh.dashboard.routes.config import router as config_router
    from jenn_mesh.dashboard.routes.fleet import router as fleet_router
    from jenn_mesh.dashboard.routes.firmware import router as firmware_router
    from jenn_mesh.dashboard.routes.health import router as health_router
    from jenn_mesh.dashboard.routes.locator import router as locator_router
    from jenn_mesh.dashboard.routes.provision import router as provision_router
    from jenn_mesh.dashboard.routes.scoring import router as scoring_router
    from jenn_mesh.dashboard.routes.topology import router as topology_router
    from jenn_mesh.dashboard.routes.emergency import router as emergency_router
    from jenn_mesh.dashboard.routes.heartbeat import router as heartbeat_router
    from jenn_mesh.dashboard.routes.config_queue import router as config_queue_router
    from jenn_mesh.dashboard.routes.recovery import router as recovery_router
    from jenn_mesh.dashboard.routes.workbench import router as workbench_router
    from jenn_mesh.dashboard.routes.failover import router as failover_router
    from jenn_mesh.dashboard.routes.watchdog import router as watchdog_router
    from jenn_mesh.dashboard.routes.config_rollback import router as config_rollback_router
    from jenn_mesh.dashboard.routes.sync_relay import router as sync_relay_router
    from jenn_mesh.dashboard.routes.geofencing import router as geofencing_router
    from jenn_mesh.dashboard.routes.anomaly import router as anomaly_router
    from jenn_mesh.dashboard.routes.alert_summary import router as alert_summary_router

    app.include_router(health_router)
    # Heartbeat router before fleet router — /fleet/mesh-status must match
    # before /fleet/{node_id} (FastAPI matches routes in registration order)
    app.include_router(heartbeat_router, prefix="/api/v1")
    app.include_router(fleet_router, prefix="/api/v1")
    app.include_router(config_router, prefix="/api/v1")
    app.include_router(provision_router, prefix="/api/v1")
    app.include_router(locator_router, prefix="/api/v1")
    app.include_router(topology_router, prefix="/api/v1")
    app.include_router(firmware_router, prefix="/api/v1")
    app.include_router(baselines_router, prefix="/api/v1")
    app.include_router(scoring_router, prefix="/api/v1")
    app.include_router(workbench_router, prefix="/api/v1")
    app.include_router(emergency_router, prefix="/api/v1")
    app.include_router(recovery_router, prefix="/api/v1")
    app.include_router(config_queue_router, prefix="/api/v1")
    app.include_router(failover_router, prefix="/api/v1")
    app.include_router(watchdog_router, prefix="/api/v1")
    app.include_router(config_rollback_router, prefix="/api/v1")
    app.include_router(sync_relay_router, prefix="/api/v1")
    app.include_router(geofencing_router, prefix="/api/v1")
    app.include_router(anomaly_router, prefix="/api/v1")
    app.include_router(alert_summary_router, prefix="/api/v1")

    # Dashboard HTML pages
    @app.get("/")
    async def dashboard_home(request: Request) -> object:
        """Serve the main dashboard page."""
        if TEMPLATES_DIR.exists():
            return templates.TemplateResponse(
                "index.html",
                {"request": request, "version": __version__},
            )
        return JSONResponse(
            {
                "service": "JennMesh Dashboard",
                "version": __version__,
                "status": "running",
                "docs": f"{root_path}/docs",
            }
        )

    @app.get("/topology")
    async def topology_page(request: Request) -> object:
        """Serve the topology visualization page."""
        topo_template = TEMPLATES_DIR / "topology.html"
        if topo_template.exists():
            return templates.TemplateResponse(
                "topology.html",
                {"request": request, "version": __version__},
            )
        return JSONResponse(
            {
                "page": "topology",
                "message": "Topology visualization template not found",
                "api": f"{root_path}/api/v1/topology",
            }
        )

    return app
