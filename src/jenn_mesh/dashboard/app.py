"""FastAPI dashboard application factory for JennMesh."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from jenn_mesh import __version__
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
        db: Optional MeshDatabase instance (creates default if omitted).

    Returns:
        Configured FastAPI app.
    """
    root_path = os.environ.get("ROOT_PATH", "")

    app = FastAPI(
        title="JennMesh Dashboard",
        description="Meshtastic fleet management dashboard",
        version=__version__,
        root_path=root_path,
    )

    # Initialize database
    if db is None:
        db = MeshDatabase()

    # Store db in app state for route access
    app.state.db = db

    # Mount static files
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Set up Jinja2 templates
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.state.templates = templates

    # Initialize workbench singletons
    from jenn_mesh.core.bulk_push import BulkPushManager
    from jenn_mesh.core.workbench_manager import WorkbenchManager

    app.state.workbench = WorkbenchManager(db)
    app.state.bulk_push = BulkPushManager(db)

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
    from jenn_mesh.dashboard.routes.workbench import router as workbench_router

    app.include_router(health_router)
    app.include_router(fleet_router, prefix="/api/v1")
    app.include_router(config_router, prefix="/api/v1")
    app.include_router(provision_router, prefix="/api/v1")
    app.include_router(locator_router, prefix="/api/v1")
    app.include_router(topology_router, prefix="/api/v1")
    app.include_router(firmware_router, prefix="/api/v1")
    app.include_router(baselines_router, prefix="/api/v1")
    app.include_router(scoring_router, prefix="/api/v1")
    app.include_router(workbench_router, prefix="/api/v1")

    # Dashboard HTML page
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

    # Add no-cache middleware for API routes
    app.add_middleware(_NoCacheAPIMiddleware)

    return app
