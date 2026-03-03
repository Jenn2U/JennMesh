"""Tests for heartbeat API endpoints and mesh-status enrichment on fleet endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from jenn_mesh.dashboard.app import create_app
from jenn_mesh.db import MeshDatabase


@pytest.fixture
def db(tmp_path):
    return MeshDatabase(db_path=str(tmp_path / "api_mesh.db"))


@pytest.fixture
def app(db):
    return create_app(db=db)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── GET /api/v1/heartbeat/{node_id} ────────────────────────────────────


class TestDeviceHeartbeatEndpoint:
    @pytest.mark.anyio
    async def test_returns_heartbeat_for_device(self, client, db):
        now = datetime.utcnow()
        db.upsert_device("!aaa11111", long_name="Relay", mesh_status="reachable")
        db.add_heartbeat(
            node_id="!aaa11111",
            uptime_seconds=3600,
            services_json='[{"name":"edge","status":"ok"}]',
            battery=80,
            rssi=-85,
            snr=10.5,
            timestamp=now.isoformat(),
        )

        resp = await client.get("/api/v1/heartbeat/!aaa11111")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_id"] == "!aaa11111"
        assert data["mesh_status"] == "reachable"
        assert data["latest_heartbeat"] is not None
        assert data["heartbeat_count"] >= 1

    @pytest.mark.anyio
    async def test_404_for_unknown_device(self, client, db):
        resp = await client.get("/api/v1/heartbeat/!unknown")
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_device_with_no_heartbeats(self, client, db):
        db.upsert_device("!aaa11111", long_name="Relay")
        resp = await client.get("/api/v1/heartbeat/!aaa11111")
        assert resp.status_code == 200
        data = resp.json()
        assert data["latest_heartbeat"] is None
        assert data["heartbeat_count"] == 0


# ── GET /api/v1/heartbeat/recent/all ────────────────────────────────────


class TestRecentHeartbeatsEndpoint:
    @pytest.mark.anyio
    async def test_returns_recent_heartbeats(self, client, db):
        db.upsert_device("!aaa11111")
        db.add_heartbeat(
            node_id="!aaa11111",
            uptime_seconds=100,
            services_json="[]",
            battery=50,
            timestamp=datetime.utcnow().isoformat(),
        )

        resp = await client.get("/api/v1/heartbeat/recent/all")
        assert resp.status_code == 200
        data = resp.json()
        assert data["minutes"] == 10
        assert data["count"] >= 1

    @pytest.mark.anyio
    async def test_empty_when_no_heartbeats(self, client, db):
        resp = await client.get("/api/v1/heartbeat/recent/all")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["heartbeats"] == []

    @pytest.mark.anyio
    async def test_custom_minutes_parameter(self, client, db):
        resp = await client.get("/api/v1/heartbeat/recent/all?minutes=60")
        assert resp.status_code == 200
        assert resp.json()["minutes"] == 60


# ── GET /api/v1/fleet/mesh-status ───────────────────────────────────────


class TestFleetMeshStatusEndpoint:
    @pytest.mark.anyio
    async def test_groups_devices_by_mesh_status(self, client, db):
        db.upsert_device("!aaa11111", long_name="A", mesh_status="reachable")
        db.upsert_device("!bbb22222", long_name="B", mesh_status="unreachable")
        db.upsert_device("!ccc33333", long_name="C", mesh_status="unknown")

        resp = await client.get("/api/v1/fleet/mesh-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["reachable_count"] == 1
        assert data["unreachable_count"] == 1
        assert data["unknown_count"] == 1
        assert len(data["reachable"]) == 1
        assert data["reachable"][0]["node_id"] == "!aaa11111"

    @pytest.mark.anyio
    async def test_empty_fleet(self, client, db):
        resp = await client.get("/api/v1/fleet/mesh-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["reachable_count"] == 0
        assert data["unreachable_count"] == 0
        assert data["unknown_count"] == 0


# ── Fleet endpoint mesh enrichment ──────────────────────────────────────


class TestFleetEndpointsMeshFields:
    @pytest.mark.anyio
    async def test_fleet_list_includes_mesh_fields(self, client, db):
        now = datetime.utcnow()
        recent = (now - timedelta(minutes=2)).isoformat()
        db.upsert_device(
            "!aaa11111",
            long_name="Relay",
            role="ROUTER",
            hw_model="heltec_v3",
            firmware_version="2.5.6",
            last_seen=recent,
            mesh_status="reachable",
            last_mesh_heartbeat=recent,
        )

        resp = await client.get("/api/v1/fleet")
        assert resp.status_code == 200
        devices = resp.json()["devices"]
        assert len(devices) == 1
        assert devices[0]["mesh_status"] == "reachable"
        assert devices[0]["last_mesh_heartbeat"] is not None

    @pytest.mark.anyio
    async def test_fleet_health_includes_mesh_count(self, client, db):
        now = datetime.utcnow()
        recent = (now - timedelta(minutes=2)).isoformat()
        db.upsert_device("!aaa11111", last_seen=recent, mesh_status="reachable")
        db.upsert_device("!bbb22222", last_seen=recent, mesh_status="unknown")

        resp = await client.get("/api/v1/fleet/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mesh_reachable_count"] == 1

    @pytest.mark.anyio
    async def test_device_detail_includes_mesh_fields(self, client, db):
        now = datetime.utcnow()
        recent = (now - timedelta(minutes=2)).isoformat()
        db.upsert_device(
            "!aaa11111",
            long_name="Relay",
            role="ROUTER",
            hw_model="heltec_v3",
            firmware_version="2.5.6",
            last_seen=recent,
            mesh_status="reachable",
            last_mesh_heartbeat=recent,
        )

        resp = await client.get("/api/v1/fleet/!aaa11111")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mesh_status"] == "reachable"
        assert data["last_mesh_heartbeat"] is not None


# ── /health endpoint mesh component ─────────────────────────────────────


class TestHealthEndpointMeshComponent:
    @pytest.mark.anyio
    async def test_health_includes_mesh_heartbeats_component(self, client, db):
        db.upsert_device("!aaa11111")
        db.add_heartbeat(
            node_id="!aaa11111",
            uptime_seconds=100,
            services_json="[]",
            battery=50,
            timestamp=datetime.utcnow().isoformat(),
        )

        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "mesh_heartbeats" in data["components"]
        assert data["components"]["mesh_heartbeats"]["status"] == "healthy"
        assert data["components"]["mesh_heartbeats"]["recent_count"] >= 1
