"""Tests for provisioning advisor API routes."""

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


@pytest.mark.asyncio
async def test_advisor_recommend(client: AsyncClient):
    """POST /advisor/recommend returns deployment advice."""
    resp = await client.post(
        "/api/v1/advisor/recommend",
        json={"terrain": "urban", "num_nodes": 5, "power_source": "battery"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "deterministic"
    assert len(data["recommended_roles"]) == 5
    assert data["power_settings"]
    assert data["channel_config"]
    assert isinstance(data["deployment_order"], list)


@pytest.mark.asyncio
async def test_advisor_recommend_defaults(client: AsyncClient):
    """POST /advisor/recommend with empty body uses defaults."""
    resp = await client.post("/api/v1/advisor/recommend", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["recommended_roles"]) == 3  # default num_nodes


@pytest.mark.asyncio
async def test_advisor_status(client: AsyncClient):
    """GET /advisor/status returns availability info."""
    resp = await client.get("/api/v1/advisor/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["ollama_available"] is False
