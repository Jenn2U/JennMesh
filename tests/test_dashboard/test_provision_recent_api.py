"""Tests for GET /provision/recent endpoint — toast + badge data source."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
async def test_recent_empty(client: AsyncClient, populated_db: MeshDatabase):
    """No recent events returns empty list with zero counts."""
    resp = await client.get("/api/v1/provision/recent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["active_count"] == 0
    assert data["entries"] == []


@pytest.mark.asyncio
async def test_recent_with_events(client: AsyncClient, populated_db: MeshDatabase):
    """Recent events within 5-minute window are returned."""
    populated_db.log_provisioning(
        node_id="!new12345",
        action="radio_detected",
        operator="radio-watcher",
        details="port=/dev/ttyUSB0 vid=0x10C4",
    )
    populated_db.log_provisioning(
        node_id="!new12345",
        action="provision_complete",
        role="CLIENT",
        operator="radio-watcher",
        details="hw=heltec_v3 fw=2.5.6",
    )

    resp = await client.get("/api/v1/provision/recent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    assert data["entries"][0]["action"] in ("radio_detected", "provision_complete")


@pytest.mark.asyncio
async def test_recent_active_count(client: AsyncClient, populated_db: MeshDatabase):
    """active_count only counts in-progress actions."""
    populated_db.log_provisioning(
        node_id="!new12345",
        action="radio_detected",
        operator="radio-watcher",
    )
    populated_db.log_provisioning(
        node_id="!new12345",
        action="erase_started",
        operator="radio-watcher",
    )
    populated_db.log_provisioning(
        node_id="!old99999",
        action="provision_complete",
        role="CLIENT",
        operator="radio-watcher",
    )

    resp = await client.get("/api/v1/provision/recent")
    data = resp.json()
    # radio_detected + erase_started are active; provision_complete is not
    assert data["active_count"] == 2
    assert data["count"] == 3


@pytest.mark.asyncio
async def test_recent_excludes_old_events(client: AsyncClient, populated_db: MeshDatabase):
    """Events older than 5 minutes are excluded."""
    # Insert an event with a timestamp >5 min ago directly
    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    with populated_db.connection() as conn:
        conn.execute(
            "INSERT INTO provisioning_log (node_id, action, operator, timestamp) VALUES (?, ?, ?, ?)",
            ("!old", "provision_complete", "radio-watcher", old_ts),
        )

    resp = await client.get("/api/v1/provision/recent")
    data = resp.json()
    assert data["count"] == 0


@pytest.mark.asyncio
async def test_recent_edge_yield_event(client: AsyncClient, populated_db: MeshDatabase):
    """Edge yield events are returned but not counted as active."""
    populated_db.log_provisioning(
        node_id="",
        action="edge_yield",
        operator="radio-watcher",
        details="Yielding radio priority to JennEdge",
    )

    resp = await client.get("/api/v1/provision/recent")
    data = resp.json()
    assert data["count"] == 1
    assert data["active_count"] == 0
    assert data["entries"][0]["action"] == "edge_yield"
