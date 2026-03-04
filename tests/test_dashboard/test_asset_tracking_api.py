"""Tests for asset tracking API endpoints."""

from __future__ import annotations

import tempfile

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


# ── Register ────────────────────────────────────────────────────────


class TestRegisterAssetAPI:
    @pytest.mark.asyncio
    async def test_register_vehicle(self, client):
        resp = await client.post(
            "/api/v1/assets",
            json={
                "name": "Truck-01",
                "asset_type": "vehicle",
                "node_id": "!abc123",
                "zone": "Zone-A",
                "team": "Alpha",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["asset_id"] is not None
        assert data["name"] == "Truck-01"

    @pytest.mark.asyncio
    async def test_register_invalid_type(self, client):
        resp = await client.post(
            "/api/v1/assets",
            json={"name": "Bad", "asset_type": "invalid", "node_id": "!abc"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_register_empty_node(self, client):
        resp = await client.post(
            "/api/v1/assets",
            json={"name": "Bad", "asset_type": "vehicle", "node_id": ""},
        )
        assert resp.status_code == 400


# ── CRUD ────────────────────────────────────────────────────────────


class TestAssetCRUDAPI:
    @pytest.mark.asyncio
    async def test_list_empty(self, client):
        resp = await client.get("/api/v1/assets")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    @pytest.mark.asyncio
    async def test_list_after_register(self, client):
        await client.post(
            "/api/v1/assets",
            json={"name": "A", "asset_type": "vehicle", "node_id": "!a"},
        )
        await client.post(
            "/api/v1/assets",
            json={"name": "B", "asset_type": "equipment", "node_id": "!b"},
        )
        resp = await client.get("/api/v1/assets")
        assert resp.json()["count"] == 2

    @pytest.mark.asyncio
    async def test_list_by_type(self, client):
        await client.post(
            "/api/v1/assets",
            json={"name": "V", "asset_type": "vehicle", "node_id": "!v"},
        )
        await client.post(
            "/api/v1/assets",
            json={"name": "E", "asset_type": "equipment", "node_id": "!e"},
        )
        resp = await client.get("/api/v1/assets?asset_type=vehicle")
        assert resp.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_get_asset(self, client):
        create = await client.post(
            "/api/v1/assets",
            json={"name": "T", "asset_type": "vehicle", "node_id": "!abc"},
        )
        asset_id = create.json()["asset_id"]
        resp = await client.get(f"/api/v1/assets/{asset_id}")
        assert resp.status_code == 200
        assert resp.json()["asset"]["name"] == "T"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, client):
        resp = await client.get("/api/v1/assets/9999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_asset(self, client):
        create = await client.post(
            "/api/v1/assets",
            json={"name": "Old", "asset_type": "vehicle", "node_id": "!abc"},
        )
        asset_id = create.json()["asset_id"]
        resp = await client.put(
            f"/api/v1/assets/{asset_id}",
            json={"name": "New"},
        )
        assert resp.status_code == 200
        get = await client.get(f"/api/v1/assets/{asset_id}")
        assert get.json()["asset"]["name"] == "New"

    @pytest.mark.asyncio
    async def test_update_no_fields(self, client):
        create = await client.post(
            "/api/v1/assets",
            json={"name": "T", "asset_type": "vehicle", "node_id": "!abc"},
        )
        asset_id = create.json()["asset_id"]
        resp = await client.put(f"/api/v1/assets/{asset_id}", json={})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_asset(self, client):
        create = await client.post(
            "/api/v1/assets",
            json={"name": "D", "asset_type": "vehicle", "node_id": "!abc"},
        )
        asset_id = create.json()["asset_id"]
        resp = await client.delete(f"/api/v1/assets/{asset_id}")
        assert resp.status_code == 200
        get = await client.get(f"/api/v1/assets/{asset_id}")
        assert get.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, client):
        resp = await client.delete("/api/v1/assets/9999")
        assert resp.status_code == 404


# ── By-node lookup ──────────────────────────────────────────────────


class TestByNodeAPI:
    @pytest.mark.asyncio
    async def test_get_by_node(self, client):
        await client.post(
            "/api/v1/assets",
            json={"name": "Node-T", "asset_type": "vehicle", "node_id": "!target"},
        )
        resp = await client.get("/api/v1/assets/by-node/!target")
        assert resp.status_code == 200
        assert resp.json()["asset"]["name"] == "Node-T"

    @pytest.mark.asyncio
    async def test_by_node_not_found(self, client):
        resp = await client.get("/api/v1/assets/by-node/!nonexistent")
        assert resp.status_code == 404


# ── Trail ───────────────────────────────────────────────────────────


class TestTrailAPI:
    @pytest.mark.asyncio
    async def test_trail_empty(self, client):
        create = await client.post(
            "/api/v1/assets",
            json={"name": "T", "asset_type": "vehicle", "node_id": "!abc"},
        )
        asset_id = create.json()["asset_id"]
        resp = await client.get(f"/api/v1/assets/{asset_id}/trail")
        assert resp.status_code == 200
        data = resp.json()
        assert data["position_count"] == 0
        assert data["total_distance_m"] == 0.0

    @pytest.mark.asyncio
    async def test_trail_nonexistent_asset(self, client):
        resp = await client.get("/api/v1/assets/9999/trail")
        assert resp.status_code == 404


# ── Status update ───────────────────────────────────────────────────


class TestUpdateStatusesAPI:
    @pytest.mark.asyncio
    async def test_update_statuses_empty(self, client):
        resp = await client.post("/api/v1/assets/update-statuses")
        assert resp.status_code == 200
        assert resp.json()["assets_updated"] == 0
