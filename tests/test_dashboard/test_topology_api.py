"""Tests for topology API routes."""

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


# ── Full topology ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_topology(client: AsyncClient):
    resp = await client.get("/api/v1/topology")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_nodes"] == 4
    assert data["total_edges"] == 3
    assert data["connected_components"] == 2
    assert data["is_fully_connected"] is False
    assert isinstance(data["nodes"], list)
    assert isinstance(data["edges"], list)


@pytest.mark.asyncio
async def test_topology_empty_db(db: MeshDatabase):
    app = create_app(db=db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/topology")
        data = resp.json()
        assert data["total_nodes"] == 0
        assert data["total_edges"] == 0


# ── Single Points of Failure ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_spof(client: AsyncClient):
    resp = await client.get("/api/v1/topology/spof")
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert "nodes" in data
    assert "!bbb22222" in data["nodes"]


# ── Connected Components ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_components(client: AsyncClient):
    resp = await client.get("/api/v1/topology/components")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert isinstance(data["components"], list)


# ── Isolated Nodes ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_isolated(client: AsyncClient):
    resp = await client.get("/api/v1/topology/isolated")
    assert resp.status_code == 200
    data = resp.json()
    assert "!ddd44444" in data["nodes"]


# ── Node Topology ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_node_topology(client: AsyncClient):
    resp = await client.get("/api/v1/topology/!bbb22222")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node_id"] == "!bbb22222"
    assert data["display_name"] == "Gateway-Edge1"
    assert data["neighbor_count"] > 0
    assert isinstance(data["edges"], list)


@pytest.mark.asyncio
async def test_node_topology_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/topology/!zzz99999")
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
