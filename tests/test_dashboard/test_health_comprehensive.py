"""Tests for the comprehensive health endpoint — component checks, degradation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from jenn_mesh.dashboard.app import create_app
from jenn_mesh.db import SCHEMA_VERSION, MeshDatabase


@pytest.fixture
def app(populated_db: MeshDatabase):
    return create_app(db=populated_db)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Top-level response shape ───────────────────────────────────────


@pytest.mark.asyncio
async def test_health_returns_required_keys(client: AsyncClient):
    """Health response must include status, version, service, schema_version, components."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    for key in ("status", "version", "service", "schema_version", "components"):
        assert key in data, f"Missing top-level key: {key}"


@pytest.mark.asyncio
async def test_health_service_name(client: AsyncClient):
    """Service should identify as jenn-mesh."""
    data = (await client.get("/health")).json()
    assert data["service"] == "jenn-mesh"


@pytest.mark.asyncio
async def test_health_schema_version(client: AsyncClient):
    """Schema version should match the DB module constant."""
    data = (await client.get("/health")).json()
    assert data["schema_version"] == SCHEMA_VERSION


# ── Healthy state (all components up) ──────────────────────────────


@pytest.mark.asyncio
async def test_health_overall_healthy(client: AsyncClient):
    """With populated DB, overall status should be healthy."""
    data = (await client.get("/health")).json()
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_database_component(client: AsyncClient):
    """Database component should report healthy with schema_version."""
    comps = (await client.get("/health")).json()["components"]
    assert comps["database"]["status"] == "healthy"
    assert comps["database"]["schema_version"] == SCHEMA_VERSION


@pytest.mark.asyncio
async def test_health_workbench_component(client: AsyncClient):
    """Workbench component should report healthy when initialised."""
    comps = (await client.get("/health")).json()["components"]
    assert comps["workbench"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_bulk_push_component(client: AsyncClient):
    """Bulk push component should report healthy when initialised."""
    comps = (await client.get("/health")).json()["components"]
    assert comps["bulk_push"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_uptime_present(client: AsyncClient):
    """Uptime should be a non-negative number."""
    comps = (await client.get("/health")).json()["components"]
    assert "uptime_seconds" in comps
    assert comps["uptime_seconds"] >= 0


# ── Degraded state (DB missing) ───────────────────────────────────


@pytest.mark.asyncio
async def test_health_degraded_no_db():
    """When DB is None, status should be degraded, database unavailable."""
    app = create_app(db=None)
    # ASGITransport doesn't fire lifespan; set state manually to simulate
    # a degraded start where the database was unavailable.
    app.state.db = None
    app.state.startup_time = datetime.now(timezone.utc)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["components"]["database"]["status"] == "unavailable"


# ── Version in response ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_version_present(client: AsyncClient):
    """Version should be a non-empty string."""
    data = (await client.get("/health")).json()
    assert isinstance(data["version"], str)
    assert len(data["version"]) > 0
