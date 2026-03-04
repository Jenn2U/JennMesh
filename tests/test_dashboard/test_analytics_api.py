"""Tests for fleet analytics API routes."""

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


# ── Uptime Endpoint ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_uptime_trends(client: AsyncClient):
    """Uptime endpoint returns trend data for all nodes."""
    resp = await client.get("/api/v1/analytics/uptime")
    assert resp.status_code == 200
    data = resp.json()
    assert data["period_days"] == 30
    assert len(data["nodes"]) == 4  # populated_db has 4 devices
    node = data["nodes"][0]
    assert "node_id" in node
    assert "uptime_pct" in node


@pytest.mark.asyncio
async def test_uptime_with_filter(client: AsyncClient):
    """Uptime endpoint with node_id filter returns one node."""
    resp = await client.get("/api/v1/analytics/uptime", params={"node_id": "!aaa11111"})
    data = resp.json()
    assert len(data["nodes"]) == 1
    assert data["nodes"][0]["node_id"] == "!aaa11111"


# ── Battery Endpoint ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_battery_trends(client: AsyncClient):
    """Battery endpoint returns trend data."""
    resp = await client.get("/api/v1/analytics/battery")
    assert resp.status_code == 200
    data = resp.json()
    assert data["period_days"] == 30
    assert len(data["nodes"]) == 4


@pytest.mark.asyncio
async def test_battery_custom_days(client: AsyncClient):
    """Battery endpoint respects custom days parameter."""
    resp = await client.get("/api/v1/analytics/battery", params={"days": 7})
    data = resp.json()
    assert data["period_days"] == 7


# ── Alerts Endpoint ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_alert_frequency_empty(client: AsyncClient):
    """Alert frequency with no alerts returns zeroed counts."""
    resp = await client.get("/api/v1/analytics/alerts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_alert_frequency_with_alerts(populated_db: MeshDatabase):
    """Alert frequency groups alerts correctly."""
    populated_db.create_alert(
        node_id="!aaa11111",
        alert_type=AlertType.LOW_BATTERY.value,
        severity=ALERT_SEVERITY_MAP[AlertType.LOW_BATTERY].value,
        message="Low battery",
    )
    populated_db.create_alert(
        node_id="!bbb22222",
        alert_type=AlertType.SIGNAL_DEGRADED.value,
        severity=ALERT_SEVERITY_MAP[AlertType.SIGNAL_DEGRADED].value,
        message="Signal weak",
    )
    app = create_app(db=populated_db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/analytics/alerts")
        data = resp.json()
        assert data["total"] == 2
        assert len(data["by_type"]) == 2
        assert len(data["by_severity"]) > 0


# ── Messages Endpoint ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_message_volume(client: AsyncClient):
    """Message volume returns per-node counts."""
    resp = await client.get("/api/v1/analytics/messages")
    assert resp.status_code == 200
    data = resp.json()
    assert data["period_days"] == 7
    assert len(data["nodes"]) == 4


# ── Summary Endpoint ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dashboard_summary(client: AsyncClient):
    """Summary endpoint returns combined analytics."""
    resp = await client.get("/api/v1/analytics/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "fleet" in data
    assert "alerts" in data
    assert "roles" in data
    assert "coverage" in data
    assert data["fleet"]["total_devices"] == 4


@pytest.mark.asyncio
async def test_dashboard_summary_fleet_pct(client: AsyncClient):
    """Summary includes online percentage."""
    resp = await client.get("/api/v1/analytics/summary")
    data = resp.json()
    fleet = data["fleet"]
    assert 0 <= fleet["online_pct"] <= 100
    assert fleet["online"] + fleet["offline"] == fleet["total_devices"]
