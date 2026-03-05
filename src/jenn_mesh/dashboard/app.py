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


OPENAPI_TAGS = [
    {"name": "health", "description": "Service health and readiness checks"},
    {"name": "fleet", "description": "Device registry, status, and fleet overview"},
    {"name": "topology", "description": "Mesh network graph, connectivity, and SPOF analysis"},
    {"name": "config", "description": "Golden config templates and drift detection"},
    {"name": "alerts", "description": "Fleet health alerts and AI summaries"},
    {
        "name": "monitoring",
        "description": "Watchdog, baselines, health scoring, and encryption audit",
    },
    {"name": "geofencing", "description": "Virtual boundary zones and breach tracking"},
    {"name": "coverage", "description": "Signal coverage mapping and dead zone detection"},
    {"name": "analytics", "description": "Fleet trends, uptime, battery, and message volume"},
    {"name": "ai", "description": "Ollama-powered anomaly detection, provisioning, and reasoning"},
    {
        "name": "environment",
        "description": "Environmental sensor telemetry (temp, humidity, pressure)",
    },
    {"name": "admin", "description": "Recovery, failover, workbench, provisioning, sync relay"},
    {"name": "webhooks", "description": "External system webhook notifications"},
    {"name": "notifications", "description": "Multi-channel alert routing (Slack, Teams, Email)"},
    {"name": "bulk-ops", "description": "Batch fleet operations with dry-run preview"},
    {"name": "team-comms", "description": "Team text messaging through LoRa mesh"},
    {"name": "tak", "description": "TAK/ATAK Cursor on Target gateway integration"},
    {
        "name": "asset-tracking",
        "description": "Vehicle, equipment, and personnel GPS tracking via mesh",
    },
    {"name": "edge-associations", "description": "JennEdge device ↔ mesh radio cross-reference"},
    {"name": "fleet-query", "description": "Natural language fleet queries via Ollama"},
    {"name": "crews", "description": "CrewAI multi-agent orchestration"},
]


def create_app(db: Optional[MeshDatabase] = None) -> FastAPI:
    """Create the JennMesh dashboard FastAPI application.

    Args:
        db: Optional MeshDatabase instance for testing.
            When provided, the lifespan will use this DB instead of creating one.

    Returns:
        Configured FastAPI app.
    """
    # Initialize OpenTelemetry (no-op if OTEL_EXPORTER_OTLP_ENDPOINT is unset)
    try:
        from jenn_mesh.dashboard.telemetry import init_telemetry

        init_telemetry()
    except Exception:
        pass  # telemetry is best-effort — never block app startup

    root_path = os.environ.get("ROOT_PATH", "")

    app = FastAPI(
        title="JennMesh Dashboard",
        description=(
            "Meshtastic LoRa radio fleet management — monitoring, "
            "provisioning, alerting, and AI-powered analytics.\n\n"
            "**Version history**: See CHANGELOG.md in the repository."
        ),
        version=__version__,
        root_path=root_path,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_tags=OPENAPI_TAGS,
        contact={"name": "Magnivation", "url": "https://jenn2u.ai"},
        license_info={"name": "Proprietary", "url": "https://jenn2u.ai/terms"},
        servers=[
            {"url": "https://mesh.jenn2u.ai", "description": "Production"},
            {"url": "http://localhost:8002", "description": "Local development"},
        ],
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
        try:
            from jenn_mesh.core.coverage_mapper import CoverageMapper

            app.state.coverage_mapper = CoverageMapper(db=db)
        except Exception:
            pass  # graceful degradation — coverage mapping unavailable
        try:
            from jenn_mesh.core.fleet_analytics import FleetAnalytics

            app.state.fleet_analytics = FleetAnalytics(db=db)
        except Exception:
            pass  # graceful degradation — fleet analytics unavailable
        try:
            from jenn_mesh.core.provisioning_advisor import ProvisioningAdvisor

            app.state.provisioning_advisor = ProvisioningAdvisor(db=db)
        except Exception:
            pass  # graceful degradation — provisioning advisor unavailable
        try:
            from jenn_mesh.core.lost_node_reasoner import LostNodeReasoner

            app.state.lost_node_reasoner = LostNodeReasoner(db=db)
        except Exception:
            pass  # graceful degradation — lost node reasoning unavailable
        try:
            from jenn_mesh.core.env_telemetry import EnvTelemetryManager

            app.state.env_telemetry_manager = EnvTelemetryManager(db=db)
        except Exception:
            pass  # graceful degradation — env telemetry unavailable
        try:
            from jenn_mesh.core.encryption_auditor import EncryptionAuditor

            app.state.encryption_auditor = EncryptionAuditor(db=db)
        except Exception:
            pass  # graceful degradation — encryption audit unavailable
        try:
            from jenn_mesh.core.team_comms_manager import TeamCommsManager

            app.state.team_comms_manager = TeamCommsManager(db=db)
        except Exception:
            pass  # graceful degradation — team comms unavailable
        try:
            from jenn_mesh.core.tak_gateway import TakGateway

            app.state.tak_gateway = TakGateway(db=db)
        except Exception:
            pass  # graceful degradation — TAK gateway unavailable
        try:
            from jenn_mesh.core.asset_tracker import AssetTracker

            app.state.asset_tracker = AssetTracker(db=db)
        except Exception:
            pass  # graceful degradation — asset tracking unavailable
        try:
            from jenn_mesh.core.edge_association_manager import EdgeAssociationManager

            app.state.edge_association_manager = EdgeAssociationManager(db=db)
        except Exception:
            pass  # graceful degradation — edge associations unavailable
        try:
            from jenn_mesh.core.fleet_query_engine import FleetQueryEngine

            app.state.fleet_query_engine = FleetQueryEngine(db=db)
        except Exception:
            pass  # graceful degradation — fleet query unavailable
        try:
            from jenn_mesh.core.webhook_manager import WebhookManager

            app.state.webhook_manager = WebhookManager(db=db)
        except Exception:
            pass  # graceful degradation — webhooks unavailable
        try:
            from jenn_mesh.core.notification_dispatcher import NotificationDispatcher

            wh_mgr = getattr(app.state, "webhook_manager", None)
            app.state.notification_dispatcher = NotificationDispatcher(
                db=db, webhook_manager=wh_mgr
            )
        except Exception:
            pass  # graceful degradation — notifications unavailable
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
    from jenn_mesh.dashboard.routes.coverage import router as coverage_router
    from jenn_mesh.dashboard.routes.analytics import router as analytics_router
    from jenn_mesh.dashboard.routes.provisioning_advisor import (
        router as provisioning_advisor_router,
    )
    from jenn_mesh.dashboard.routes.lost_node_ai import router as lost_node_ai_router
    from jenn_mesh.dashboard.routes.encryption import router as encryption_router
    from jenn_mesh.dashboard.routes.env_telemetry import router as env_telemetry_router
    from jenn_mesh.dashboard.routes.webhooks import router as webhooks_router
    from jenn_mesh.dashboard.routes.notifications import router as notifications_router
    from jenn_mesh.dashboard.routes.partitions import router as partitions_router
    from jenn_mesh.dashboard.routes.bulk_ops import router as bulk_ops_router
    from jenn_mesh.dashboard.routes.team_comms import router as team_comms_router
    from jenn_mesh.dashboard.routes.tak import router as tak_router
    from jenn_mesh.dashboard.routes.asset_tracking import router as asset_tracking_router
    from jenn_mesh.dashboard.routes.edge_associations import (
        router as edge_associations_router,
    )
    from jenn_mesh.dashboard.routes.fleet_query import router as fleet_query_router
    from jenn_mesh.dashboard.routes.crews import router as crews_router

    app.include_router(health_router, tags=["health"])
    # Heartbeat router before fleet router — /fleet/mesh-status must match
    # before /fleet/{node_id} (FastAPI matches routes in registration order)
    app.include_router(heartbeat_router, prefix="/api/v1", tags=["fleet"])
    app.include_router(fleet_router, prefix="/api/v1", tags=["fleet"])
    app.include_router(config_router, prefix="/api/v1", tags=["config"])
    app.include_router(provision_router, prefix="/api/v1", tags=["admin"])
    app.include_router(locator_router, prefix="/api/v1", tags=["fleet"])
    app.include_router(topology_router, prefix="/api/v1", tags=["topology"])
    app.include_router(firmware_router, prefix="/api/v1", tags=["config"])
    app.include_router(baselines_router, prefix="/api/v1", tags=["monitoring"])
    app.include_router(scoring_router, prefix="/api/v1", tags=["monitoring"])
    app.include_router(workbench_router, prefix="/api/v1", tags=["admin"])
    app.include_router(emergency_router, prefix="/api/v1", tags=["alerts"])
    app.include_router(recovery_router, prefix="/api/v1", tags=["admin"])
    app.include_router(config_queue_router, prefix="/api/v1", tags=["config"])
    app.include_router(failover_router, prefix="/api/v1", tags=["admin"])
    app.include_router(watchdog_router, prefix="/api/v1", tags=["monitoring"])
    app.include_router(config_rollback_router, prefix="/api/v1", tags=["config"])
    app.include_router(sync_relay_router, prefix="/api/v1", tags=["admin"])
    app.include_router(geofencing_router, prefix="/api/v1", tags=["geofencing"])
    app.include_router(anomaly_router, prefix="/api/v1", tags=["ai"])
    app.include_router(alert_summary_router, prefix="/api/v1", tags=["alerts"])
    app.include_router(coverage_router, prefix="/api/v1", tags=["coverage"])
    app.include_router(analytics_router, prefix="/api/v1", tags=["analytics"])
    app.include_router(provisioning_advisor_router, prefix="/api/v1", tags=["ai"])
    # lost_node_ai_router before locator_router: /locate/ai/status must match
    # before /locate/{node_id} would capture "ai" as a node_id
    app.include_router(lost_node_ai_router, prefix="/api/v1", tags=["ai"])
    app.include_router(env_telemetry_router, prefix="/api/v1", tags=["environment"])
    app.include_router(encryption_router, prefix="/api/v1", tags=["monitoring"])
    app.include_router(webhooks_router, prefix="/api/v1", tags=["webhooks"])
    app.include_router(notifications_router, prefix="/api/v1", tags=["notifications"])
    app.include_router(partitions_router, prefix="/api/v1", tags=["topology"])
    app.include_router(bulk_ops_router, prefix="/api/v1", tags=["bulk-ops"])
    app.include_router(team_comms_router, prefix="/api/v1", tags=["team-comms"])
    app.include_router(tak_router, prefix="/api/v1", tags=["tak"])
    app.include_router(asset_tracking_router, prefix="/api/v1", tags=["asset-tracking"])
    app.include_router(edge_associations_router, prefix="/api/v1", tags=["edge-associations"])
    app.include_router(fleet_query_router, prefix="/api/v1", tags=["fleet-query"])
    app.include_router(crews_router, prefix="/api/v1", tags=["crews"])

    # Initialize CrewAI tools (no-op if disabled or crewai not installed)
    try:
        from jenn_mesh.crews import init_crews

        init_crews(app)
    except Exception:
        pass  # graceful degradation — CrewAI orchestration unavailable

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

    @app.get("/fleet-query")
    async def fleet_query_page(request: Request) -> object:
        """Serve the fleet NL query chat page."""
        fq_template = TEMPLATES_DIR / "fleet_query.html"
        if fq_template.exists():
            return templates.TemplateResponse(
                "fleet_query.html",
                {"request": request, "version": __version__},
            )
        return JSONResponse(
            {
                "page": "fleet-query",
                "message": "Fleet query template not found",
                "api": f"{root_path}/api/v1/fleet-query/ask",
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
