"""Tests for global error handlers — HTTPException, validation, unhandled."""

from __future__ import annotations

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


# ── HTTPException (404) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_404_returns_json_detail(client: AsyncClient):
    """HTTPException 404 should return JSON with 'detail' and 'status_code'."""
    resp = await client.get("/api/v1/fleet/!nonexistent")
    assert resp.status_code == 404
    data = resp.json()
    assert data["detail"] == "Device not found"
    assert data["status_code"] == 404


@pytest.mark.asyncio
async def test_404_config_template(client: AsyncClient):
    """Config template not found → 404."""
    resp = await client.get("/api/v1/config/templates/no-such-role")
    assert resp.status_code == 404
    data = resp.json()
    assert "Template not found" in data["detail"]


@pytest.mark.asyncio
async def test_404_baseline(client: AsyncClient):
    """Baseline not found → 404."""
    resp = await client.get("/api/v1/baselines/!nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_404_position(client: AsyncClient):
    """Position not found → 404."""
    resp = await client.get("/api/v1/positions/!ddd44444")
    assert resp.status_code == 404
    data = resp.json()
    assert "No position data" in data["detail"]


@pytest.mark.asyncio
async def test_404_topology_node(client: AsyncClient):
    """Topology node not found → 404."""
    resp = await client.get("/api/v1/topology/!zzz99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_404_firmware_device(client: AsyncClient):
    """Firmware status for unknown device → 404."""
    resp = await client.get("/api/v1/firmware/status/!nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_404_health_score(client: AsyncClient):
    """Health score for unknown device → 404."""
    resp = await client.get("/api/v1/health/scores/!nonexistent")
    assert resp.status_code == 404


# ── Validation errors (422) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalid_endpoint_returns_404(client: AsyncClient):
    """Unknown route → 404 (FastAPI default)."""
    resp = await client.get("/api/v1/no-such-endpoint")
    assert resp.status_code == 404


# ── JSON content type ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_error_responses_are_json(client: AsyncClient):
    """Error responses should have application/json content type."""
    resp = await client.get("/api/v1/fleet/!nonexistent")
    assert resp.status_code == 404
    assert "application/json" in resp.headers.get("content-type", "")
