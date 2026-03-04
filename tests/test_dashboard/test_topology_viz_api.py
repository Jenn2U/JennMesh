"""Tests for topology visualization page and extended topology API."""

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


# ── Topology Page ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_topology_page_exists(client: AsyncClient):
    """GET /topology returns 200 (either HTML or JSON fallback)."""
    resp = await client.get("/topology")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_topology_page_html_content(client: AsyncClient):
    """Topology page should contain topology-specific content."""
    resp = await client.get("/topology")
    # Either HTML template or JSON fallback
    content = resp.text
    assert "topology" in content.lower()


# ── Topology API supports visualization ──────────────────────────────


@pytest.mark.asyncio
async def test_topology_api_has_spof_field(client: AsyncClient):
    """Topology API must include single_points_of_failure for viz."""
    resp = await client.get("/api/v1/topology")
    assert resp.status_code == 200
    data = resp.json()
    assert "single_points_of_failure" in data
    assert isinstance(data["single_points_of_failure"], list)


@pytest.mark.asyncio
async def test_topology_api_node_structure(client: AsyncClient):
    """Each node in topology API has fields needed by D3 viz."""
    resp = await client.get("/api/v1/topology")
    data = resp.json()
    if data["total_nodes"] > 0:
        node = data["nodes"][0]
        assert "node_id" in node
        assert "display_name" in node
        assert "role" in node
        assert "is_online" in node
        assert "latitude" in node
        assert "longitude" in node
        assert "neighbor_count" in node


@pytest.mark.asyncio
async def test_topology_api_edge_structure(client: AsyncClient):
    """Each edge in topology API has fields needed by D3 viz."""
    resp = await client.get("/api/v1/topology")
    data = resp.json()
    if data["total_edges"] > 0:
        edge = data["edges"][0]
        assert "from_node" in edge
        assert "to_node" in edge
        assert "snr" in edge
        assert "rssi" in edge


@pytest.mark.asyncio
async def test_topology_api_connectivity_fields(client: AsyncClient):
    """Topology API must include connectivity summary for stats bar."""
    resp = await client.get("/api/v1/topology")
    data = resp.json()
    assert "total_nodes" in data
    assert "total_edges" in data
    assert "connected_components" in data
    assert "is_fully_connected" in data
    assert "has_spof" in data


@pytest.mark.asyncio
async def test_topology_spof_endpoint(client: AsyncClient):
    """SPOF endpoint returns list of articulation points."""
    resp = await client.get("/api/v1/topology/spof")
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert "nodes" in data


@pytest.mark.asyncio
async def test_topology_components_endpoint(client: AsyncClient):
    """Components endpoint returns connected component groups."""
    resp = await client.get("/api/v1/topology/components")
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert "components" in data


@pytest.mark.asyncio
async def test_topology_isolated_endpoint(client: AsyncClient):
    """Isolated endpoint returns nodes with no edges."""
    resp = await client.get("/api/v1/topology/isolated")
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert "nodes" in data


@pytest.mark.asyncio
async def test_topology_node_detail(client: AsyncClient):
    """Per-node topology returns edges for sidebar detail."""
    resp = await client.get("/api/v1/topology/!aaa11111")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node_id"] == "!aaa11111"
    assert "edges" in data


@pytest.mark.asyncio
async def test_topology_node_not_found(client: AsyncClient):
    """Nonexistent node returns 404."""
    resp = await client.get("/api/v1/topology/!nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_topology_empty_db(db: MeshDatabase):
    """Topology with empty DB returns zero counts."""
    app = create_app(db=db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/topology")
        data = resp.json()
        assert data["total_nodes"] == 0
        assert data["total_edges"] == 0
        assert data["single_points_of_failure"] == []
