"""API tests for Sprint 3 endpoints (MESH-020, MESH-021, MESH-022)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from jenn_mesh.core.baselines import BaselineManager
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


# ── Firmware (MESH-021) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_firmware_status(client: AsyncClient):
    resp = await client.get("/api/v1/firmware/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 4
    assert "devices" in data


@pytest.mark.asyncio
async def test_firmware_status_single_device(client: AsyncClient):
    resp = await client.get("/api/v1/firmware/status/!aaa11111")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node_id"] == "!aaa11111"
    assert "status" in data or "hw_model" in data


@pytest.mark.asyncio
async def test_firmware_compatibility_matrix(client: AsyncClient):
    resp = await client.get("/api/v1/firmware/compatibility")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 8
    assert all("hw_model" in e and "firmware_version" in e for e in data["entries"])


@pytest.mark.asyncio
async def test_firmware_compatibility_by_hw(client: AsyncClient):
    resp = await client.get("/api/v1/firmware/compatibility/heltec_v3")
    assert resp.status_code == 200
    data = resp.json()
    assert data["hw_model"] == "heltec_v3"
    assert len(data["versions"]) >= 2


@pytest.mark.asyncio
async def test_firmware_upgradeable(client: AsyncClient):
    resp = await client.get("/api/v1/firmware/upgradeable")
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert "devices" in data


# ── Baselines (MESH-020) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_baselines_list(client: AsyncClient):
    resp = await client.get("/api/v1/baselines")
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert "baselines" in data


@pytest.mark.asyncio
async def test_baselines_single_node(populated_db: MeshDatabase):
    """Precompute baseline then fetch via API."""
    manager = BaselineManager(populated_db)
    manager.recompute_baseline("!aaa11111")
    app = create_app(db=populated_db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/baselines/!aaa11111")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_id"] == "!aaa11111"
        assert data["sample_count"] >= 10


@pytest.mark.asyncio
async def test_baselines_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/baselines/!nonexistent")
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_baselines_deviations_fleet(client: AsyncClient):
    resp = await client.get("/api/v1/baselines/deviations")
    assert resp.status_code == 200
    data = resp.json()
    assert "count" in data
    assert "deviations" in data


@pytest.mark.asyncio
async def test_baselines_deviations_node(client: AsyncClient):
    resp = await client.get("/api/v1/baselines/!aaa11111/deviations")
    assert resp.status_code == 200
    data = resp.json()
    # Either a deviation report or error (no baseline precomputed)
    assert "node_id" in data or "error" in data


# ── Health Scoring (MESH-022) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_scores_fleet(client: AsyncClient):
    resp = await client.get("/api/v1/health/scores")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 4
    assert all("overall_score" in s and "grade" in s for s in data["scores"])


@pytest.mark.asyncio
async def test_health_score_single_device(client: AsyncClient):
    resp = await client.get("/api/v1/health/scores/!aaa11111")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node_id"] == "!aaa11111"
    assert 0.0 <= data["overall_score"] <= 100.0
    assert data["grade"] in ("healthy", "degraded", "critical")
    assert "factors" in data


@pytest.mark.asyncio
async def test_health_score_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/health/scores/!nonexistent")
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_health_summary(client: AsyncClient):
    resp = await client.get("/api/v1/health/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 4
    assert "healthy" in data
    assert "average_score" in data


# ── Fleet list includes health scores (MESH-022 integration) ────────


@pytest.mark.asyncio
async def test_fleet_list_includes_health(client: AsyncClient):
    resp = await client.get("/api/v1/fleet")
    assert resp.status_code == 200
    data = resp.json()
    for device in data["devices"]:
        assert "health_score" in device
        assert "health_grade" in device
