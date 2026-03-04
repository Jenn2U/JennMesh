"""Tests for lost node AI reasoning API routes."""

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
async def test_ai_reasoning_known_node(client: AsyncClient):
    """GET /locate/{node_id}/ai-reasoning returns reasoning for known node."""
    resp = await client.get("/api/v1/locate/!ccc33333/ai-reasoning")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node_id"] == "!ccc33333"
    assert data["source"] == "deterministic"
    assert data["confidence"] in ("low", "medium", "high")
    assert data["probable_location"]
    assert data["reasoning"]
    assert isinstance(data["search_recommendations"], list)


@pytest.mark.asyncio
async def test_ai_reasoning_unknown_node(client: AsyncClient):
    """GET /locate/{node_id}/ai-reasoning handles unknown node gracefully."""
    resp = await client.get("/api/v1/locate/!unknown999/ai-reasoning")
    assert resp.status_code == 200
    data = resp.json()
    assert data["confidence"] == "low"


@pytest.mark.asyncio
async def test_ai_status(client: AsyncClient):
    """GET /locate/ai/status returns reasoner availability."""
    resp = await client.get("/api/v1/locate/ai/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["ollama_available"] is False
