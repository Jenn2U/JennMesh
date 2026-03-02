"""Tests for JennMesh Dashboard API routes using FastAPI TestClient."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from jenn_mesh.dashboard.app import create_app
from jenn_mesh.db import MeshDatabase


@pytest.fixture
def app(populated_db: MeshDatabase):
    """FastAPI app wired to the populated test database."""
    return create_app(db=populated_db)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Health ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["service"] == "jenn-mesh"
    assert "version" in data


# ── Fleet ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fleet_list(client: AsyncClient):
    resp = await client.get("/api/v1/fleet")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 4
    assert len(data["devices"]) == 4


@pytest.mark.asyncio
async def test_fleet_health(client: AsyncClient):
    resp = await client.get("/api/v1/fleet/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_devices"] == 4
    assert data["online_count"] == 2
    assert "health_score" in data


@pytest.mark.asyncio
async def test_fleet_device_detail(client: AsyncClient):
    resp = await client.get("/api/v1/fleet/!aaa11111")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node_id"] == "!aaa11111"
    assert data["long_name"] == "Relay-HQ"
    assert data["role"] == "ROUTER"


@pytest.mark.asyncio
async def test_fleet_device_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/fleet/!nonexistent")
    assert resp.status_code == 404
    data = resp.json()
    assert "detail" in data


@pytest.mark.asyncio
async def test_fleet_alerts_active(client: AsyncClient):
    resp = await client.get("/api/v1/fleet/alerts/active")
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert "alerts" in data


# ── Config ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_config_templates_list(client: AsyncClient):
    resp = await client.get("/api/v1/config/templates")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 4
    roles = {t["role"] for t in data["templates"]}
    assert "relay-node" in roles
    assert "edge-gateway" in roles


@pytest.mark.asyncio
async def test_config_template_detail(client: AsyncClient):
    resp = await client.get("/api/v1/config/templates/relay-node")
    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "relay-node"
    assert "yaml_content" in data
    assert "hash" in data


@pytest.mark.asyncio
async def test_config_template_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/config/templates/nonexistent-role")
    assert resp.status_code == 404
    data = resp.json()
    assert "detail" in data


@pytest.mark.asyncio
async def test_config_drift_report(client: AsyncClient):
    resp = await client.get("/api/v1/config/drift")
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert "drifted_devices" in data


# ── Provisioning ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_provision_log_empty(client: AsyncClient):
    resp = await client.get("/api/v1/provision/log")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0


@pytest.mark.asyncio
async def test_provision_log_with_data(populated_db: MeshDatabase):
    populated_db.log_provisioning("!aaa11111", "flash", role="relay", operator="test")
    app = create_app(db=populated_db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/provision/log")
        data = resp.json()
        assert data["count"] == 1

        resp2 = await client.get("/api/v1/provision/log/!aaa11111")
        data2 = resp2.json()
        assert data2["node_id"] == "!aaa11111"
        assert data2["count"] == 1


# ── Locator ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_locate_known_node(client: AsyncClient):
    resp = await client.get("/api/v1/locate/!aaa11111")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_found"] is True
    assert data["confidence"] == "high"
    assert data["last_known_position"]["latitude"] == 30.2672


@pytest.mark.asyncio
async def test_locate_unknown_node(client: AsyncClient):
    resp = await client.get("/api/v1/locate/!zzz99999")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_found"] is False


@pytest.mark.asyncio
async def test_positions_all(client: AsyncClient):
    resp = await client.get("/api/v1/positions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 3  # 3 devices have GPS coords


@pytest.mark.asyncio
async def test_positions_single_node(client: AsyncClient):
    resp = await client.get("/api/v1/positions/!aaa11111")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node_id"] == "!aaa11111"
    assert data["latitude"] == 30.2672


@pytest.mark.asyncio
async def test_positions_node_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/positions/!ddd44444")
    assert resp.status_code == 404
    data = resp.json()
    assert "detail" in data


# ── No-Cache Middleware ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_no_cache_header(client: AsyncClient):
    """API routes should include Cache-Control: no-store for Front Door."""
    resp = await client.get("/api/v1/fleet")
    assert "no-store" in resp.headers.get("cache-control", "")


@pytest.mark.asyncio
async def test_non_api_no_cache_header_absent(client: AsyncClient):
    """Non-API routes should NOT have the no-cache header."""
    resp = await client.get("/health")
    assert "no-store" not in resp.headers.get("cache-control", "")
