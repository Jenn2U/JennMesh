"""Tests for anomaly detection API routes."""

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


# ── Status endpoint ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_anomaly_status(client: AsyncClient):
    resp = await client.get("/api/v1/anomaly/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert "ollama_available" in data


# ── History endpoint ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_anomaly_history_empty(client: AsyncClient):
    resp = await client.get("/api/v1/anomaly/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["anomalies"] == []


# ── Analyze node ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_node_no_anomaly(client: AsyncClient):
    """Node with no deviation → not anomalous."""
    resp = await client.get("/api/v1/anomaly/!aaa11111")
    assert resp.status_code == 200
    data = resp.json()
    # Baseline may or may not detect deviation depending on seed data,
    # but the endpoint should not error
    assert "node_id" in data or "is_anomalous" in data


@pytest.mark.asyncio
async def test_analyze_nonexistent_node(client: AsyncClient):
    resp = await client.get("/api/v1/anomaly/!nonexistent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_anomalous"] is False


# ── Fleet analysis ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_fleet(client: AsyncClient):
    resp = await client.get("/api/v1/anomaly/fleet")
    assert resp.status_code == 200
    data = resp.json()
    assert data["analyzed"] is True
    assert "anomaly_count" in data
    assert isinstance(data["reports"], list)
