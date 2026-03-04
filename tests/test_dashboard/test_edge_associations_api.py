"""Tests for edge association API endpoints."""

from __future__ import annotations

import tempfile
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from jenn_mesh.dashboard.app import create_app
from jenn_mesh.db import MeshDatabase


@pytest.fixture
def db() -> MeshDatabase:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return MeshDatabase(db_path=tmp.name)


@pytest.fixture
def client(db: MeshDatabase) -> AsyncClient:
    app = create_app(db=db)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── Create ──────────────────────────────────────────────────────────


class TestCreateAssociationAPI:
    @pytest.mark.asyncio
    async def test_create_basic(self, client):
        resp = await client.post(
            "/api/v1/edge-associations",
            json={"edge_device_id": "edge-001", "node_id": "!abc123"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["edge_device_id"] == "edge-001"
        assert data["node_id"] == "!abc123"

    @pytest.mark.asyncio
    async def test_create_with_details(self, client):
        resp = await client.post(
            "/api/v1/edge-associations",
            json={
                "edge_device_id": "edge-002",
                "node_id": "!def456",
                "edge_hostname": "pi4-field-02",
                "edge_ip": "10.10.50.22",
                "association_type": "usb-connected",
            },
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_duplicate_edge(self, client):
        await client.post(
            "/api/v1/edge-associations",
            json={"edge_device_id": "edge-001", "node_id": "!abc"},
        )
        resp = await client.post(
            "/api/v1/edge-associations",
            json={"edge_device_id": "edge-001", "node_id": "!def"},
        )
        assert resp.status_code == 400


# ── List ────────────────────────────────────────────────────────────


class TestListAssociationsAPI:
    @pytest.mark.asyncio
    async def test_list_empty(self, client):
        resp = await client.get("/api/v1/edge-associations")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    @pytest.mark.asyncio
    async def test_list_after_create(self, client):
        await client.post(
            "/api/v1/edge-associations",
            json={"edge_device_id": "a", "node_id": "!1"},
        )
        await client.post(
            "/api/v1/edge-associations",
            json={"edge_device_id": "b", "node_id": "!2"},
        )
        resp = await client.get("/api/v1/edge-associations")
        assert resp.json()["count"] == 2


# ── Lookup ──────────────────────────────────────────────────────────


class TestLookupAPI:
    @pytest.mark.asyncio
    async def test_by_edge(self, client):
        await client.post(
            "/api/v1/edge-associations",
            json={"edge_device_id": "edge-001", "node_id": "!abc"},
        )
        resp = await client.get("/api/v1/edge-associations/by-edge/edge-001")
        assert resp.status_code == 200
        assert resp.json()["association"]["node_id"] == "!abc"

    @pytest.mark.asyncio
    async def test_by_edge_not_found(self, client):
        resp = await client.get("/api/v1/edge-associations/by-edge/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_by_node(self, client):
        await client.post(
            "/api/v1/edge-associations",
            json={"edge_device_id": "edge-001", "node_id": "!abc"},
        )
        resp = await client.get("/api/v1/edge-associations/by-node/!abc")
        assert resp.status_code == 200
        assert resp.json()["association"]["edge_device_id"] == "edge-001"

    @pytest.mark.asyncio
    async def test_by_node_not_found(self, client):
        resp = await client.get("/api/v1/edge-associations/by-node/!nonexistent")
        assert resp.status_code == 404


# ── Update / Delete ─────────────────────────────────────────────────


class TestUpdateDeleteAPI:
    @pytest.mark.asyncio
    async def test_update(self, client):
        await client.post(
            "/api/v1/edge-associations",
            json={"edge_device_id": "edge-001", "node_id": "!abc"},
        )
        resp = await client.put(
            "/api/v1/edge-associations/edge-001",
            json={"edge_hostname": "new-host"},
        )
        assert resp.status_code == 200
        get = await client.get("/api/v1/edge-associations/by-edge/edge-001")
        assert get.json()["association"]["edge_hostname"] == "new-host"

    @pytest.mark.asyncio
    async def test_update_no_fields(self, client):
        await client.post(
            "/api/v1/edge-associations",
            json={"edge_device_id": "edge-001", "node_id": "!abc"},
        )
        resp = await client.put(
            "/api/v1/edge-associations/edge-001", json={}
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_update_nonexistent(self, client):
        resp = await client.put(
            "/api/v1/edge-associations/nope",
            json={"edge_hostname": "x"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete(self, client):
        await client.post(
            "/api/v1/edge-associations",
            json={"edge_device_id": "edge-001", "node_id": "!abc"},
        )
        resp = await client.delete("/api/v1/edge-associations/edge-001")
        assert resp.status_code == 200
        get = await client.get("/api/v1/edge-associations/by-edge/edge-001")
        assert get.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, client):
        resp = await client.delete("/api/v1/edge-associations/nope")
        assert resp.status_code == 404


# ── Combined status ─────────────────────────────────────────────────


class TestCombinedStatusAPI:
    @pytest.mark.asyncio
    async def test_status_not_found(self, client):
        resp = await client.get(
            "/api/v1/edge-associations/status/nonexistent"
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_status_with_device(self, client, db):
        db.upsert_device(
            "!abc123",
            long_name="Radio-1",
            battery_level=75,
            signal_rssi=-85,
            signal_snr=10.0,
            mesh_status="reachable",
            last_seen=datetime.utcnow().isoformat(),
        )
        await client.post(
            "/api/v1/edge-associations",
            json={"edge_device_id": "edge-001", "node_id": "!abc123"},
        )
        resp = await client.get(
            "/api/v1/edge-associations/status/edge-001"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["radio_online"] is True
        assert data["radio_battery"] == 75


# ── Stale update ────────────────────────────────────────────────────


class TestStaleAPI:
    @pytest.mark.asyncio
    async def test_update_stale(self, client):
        resp = await client.post("/api/v1/edge-associations/update-stale")
        assert resp.status_code == 200
        assert resp.json()["stale_count"] == 0
