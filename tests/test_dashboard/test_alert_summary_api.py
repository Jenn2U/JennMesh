"""Tests for alert summary API routes."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from jenn_mesh.dashboard.app import create_app
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.fleet import ALERT_SEVERITY_MAP, AlertType


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
async def test_summary_status(client: AsyncClient):
    resp = await client.get("/api/v1/alerts/summary/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert "ollama_available" in data
    assert "active_alert_count" in data


# ── Fleet summary ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fleet_summary_empty(client: AsyncClient):
    """No alerts → normal operation."""
    resp = await client.get("/api/v1/alerts/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["alert_count"] == 0
    assert "normally" in data["summary"].lower()


@pytest.mark.asyncio
async def test_fleet_summary_with_alerts(populated_db: MeshDatabase):
    """With alerts seeded, summary returns breakdown."""
    populated_db.create_alert(
        node_id="!aaa11111",
        alert_type=AlertType.LOW_BATTERY.value,
        severity=ALERT_SEVERITY_MAP[AlertType.LOW_BATTERY].value,
        message="Low battery",
    )
    app = create_app(db=populated_db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/alerts/summary")
        data = resp.json()
        assert data["alert_count"] >= 1
        assert data["source"] == "rule-based"
        assert "breakdown" in data


# ── Per-node summary ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_node_summary_no_alerts(client: AsyncClient):
    resp = await client.get("/api/v1/alerts/summary/!aaa11111")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node_id"] == "!aaa11111"
    assert data["alert_count"] == 0


@pytest.mark.asyncio
async def test_node_summary_nonexistent(client: AsyncClient):
    resp = await client.get("/api/v1/alerts/summary/!nonexistent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["alert_count"] == 0
