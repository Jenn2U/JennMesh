"""Tests for environmental telemetry API routes."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from jenn_mesh.core.env_telemetry import EnvTelemetryManager
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


def _seed_env_data(db: MeshDatabase, manager: EnvTelemetryManager) -> None:
    """Seed environmental telemetry data."""
    manager.ingest_reading("!aaa11111", temperature=22.5, humidity=55.0, pressure=1013.0)
    manager.ingest_reading("!bbb22222", temperature=28.0, humidity=70.0, pressure=1010.0)


@pytest.mark.asyncio
async def test_node_env_history_empty(client: AsyncClient):
    """GET /environment/{node_id} with no data returns empty."""
    resp = await client.get("/api/v1/environment/!aaa11111")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node_id"] == "!aaa11111"
    assert data["count"] == 0


@pytest.mark.asyncio
async def test_node_env_history_with_data(populated_db: MeshDatabase):
    """GET /environment/{node_id} returns readings."""
    mgr = EnvTelemetryManager(db=populated_db)
    _seed_env_data(populated_db, mgr)
    app = create_app(db=populated_db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/environment/!aaa11111")
        data = resp.json()
        assert data["count"] == 1
        assert data["readings"][0]["temperature"] == 22.5


@pytest.mark.asyncio
async def test_fleet_env_summary(populated_db: MeshDatabase):
    """GET /environment/fleet/summary returns fleet-wide aggregation."""
    mgr = EnvTelemetryManager(db=populated_db)
    _seed_env_data(populated_db, mgr)
    app = create_app(db=populated_db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/environment/fleet/summary")
        data = resp.json()
        assert data["node_count"] == 2
        assert data["avg_temperature"] == 25.2  # (22.5 + 28.0) / 2 ≈ 25.25 → 25.2


@pytest.mark.asyncio
async def test_get_thresholds(client: AsyncClient):
    """GET /environment/thresholds returns default thresholds."""
    resp = await client.get("/api/v1/environment/thresholds")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 4
    metrics = {t["metric"] for t in data["thresholds"]}
    assert "temperature" in metrics


@pytest.mark.asyncio
async def test_update_thresholds(client: AsyncClient):
    """PUT /environment/thresholds updates configuration."""
    new = {
        "thresholds": [
            {"metric": "temperature", "min_value": -10, "max_value": 50, "enabled": True}
        ]
    }
    resp = await client.put("/api/v1/environment/thresholds", json=new)
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["thresholds"][0]["max_value"] == 50


@pytest.mark.asyncio
async def test_env_alerts_empty(client: AsyncClient):
    """GET /environment/alerts with no breaches returns empty."""
    resp = await client.get("/api/v1/environment/alerts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
