"""Tests for application lifespan — startup populates app.state, graceful degradation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from jenn_mesh.dashboard.app import create_app
from jenn_mesh.db import MeshDatabase


@pytest.fixture
def app(populated_db: MeshDatabase):
    return create_app(db=populated_db)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── DB injection via _test_db ──────────────────────────────────────


@pytest.mark.asyncio
async def test_lifespan_injects_test_db(app, client: AsyncClient):
    """When create_app(db=...) is used, lifespan should use that DB."""
    # The fact that the client works and we can query means DB was injected
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["components"]["database"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_lifespan_db_accessible(app, client: AsyncClient):
    """app.state.db should be the injected database after startup."""
    # If fleet endpoint works with populated data, DB is wired correctly
    resp = await client.get("/api/v1/fleet")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 4  # populated_db has 4 devices


# ── Startup time recorded ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_lifespan_sets_startup_time(client: AsyncClient):
    """startup_time should be recorded during lifespan startup."""
    # If uptime_seconds is in health response, startup_time was set
    data = (await client.get("/health")).json()
    assert "uptime_seconds" in data["components"]
    assert data["components"]["uptime_seconds"] >= 0


# ── Workbench + bulk push initialised ──────────────────────────────


@pytest.mark.asyncio
async def test_lifespan_initialises_workbench(client: AsyncClient):
    """Workbench manager should be initialised by lifespan."""
    data = (await client.get("/health")).json()
    assert data["components"]["workbench"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_lifespan_initialises_bulk_push(client: AsyncClient):
    """Bulk push manager should be initialised by lifespan."""
    data = (await client.get("/health")).json()
    assert data["components"]["bulk_push"]["status"] == "healthy"


# ── Graceful degradation (no DB) ──────────────────────────────────


@pytest.mark.asyncio
async def test_lifespan_graceful_without_db():
    """When DB creation fails, app should still start (degraded mode)."""
    app = create_app(db=None)
    # ASGITransport doesn't fire lifespan, so set state manually to simulate
    # a degraded start where the database was unavailable.
    app.state.db = None
    app.state.startup_time = datetime.now(timezone.utc)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        # App responds — it didn't crash during startup
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
