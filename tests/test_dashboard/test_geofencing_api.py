"""Tests for geofencing API routes."""

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


# ── Create geofence ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_circle_geofence(client: AsyncClient):
    resp = await client.post(
        "/api/v1/geofences",
        json={
            "name": "HQ Zone",
            "fence_type": "circle",
            "center_lat": 30.2672,
            "center_lon": -97.7431,
            "radius_m": 500.0,
            "trigger_on": "exit",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "HQ Zone"
    assert data["status"] == "created"
    assert "id" in data


@pytest.mark.asyncio
async def test_create_geofence_missing_name(client: AsyncClient):
    resp = await client.post(
        "/api/v1/geofences",
        json={"fence_type": "circle", "center_lat": 30.0, "center_lon": -97.0},
    )
    assert resp.status_code == 400


# ── List geofences ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_geofences_empty(client: AsyncClient):
    resp = await client.get("/api/v1/geofences")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["geofences"] == []


@pytest.mark.asyncio
async def test_list_geofences_with_data(client: AsyncClient):
    await client.post(
        "/api/v1/geofences",
        json={"name": "Zone A", "center_lat": 30.0, "center_lon": -97.0, "radius_m": 100},
    )
    await client.post(
        "/api/v1/geofences",
        json={"name": "Zone B", "center_lat": 31.0, "center_lon": -96.0, "radius_m": 200},
    )
    resp = await client.get("/api/v1/geofences")
    data = resp.json()
    assert data["count"] == 2


# ── Get single geofence ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_geofence(client: AsyncClient):
    create_resp = await client.post(
        "/api/v1/geofences",
        json={"name": "Test Zone", "center_lat": 30.0, "center_lon": -97.0, "radius_m": 100},
    )
    fence_id = create_resp.json()["id"]

    resp = await client.get(f"/api/v1/geofences/{fence_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test Zone"


@pytest.mark.asyncio
async def test_get_geofence_not_found(client: AsyncClient):
    resp = await client.get("/api/v1/geofences/9999")
    assert resp.status_code == 404


# ── Update geofence ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_geofence(client: AsyncClient):
    create_resp = await client.post(
        "/api/v1/geofences",
        json={"name": "Old Name", "center_lat": 30.0, "center_lon": -97.0, "radius_m": 100},
    )
    fence_id = create_resp.json()["id"]

    resp = await client.put(f"/api/v1/geofences/{fence_id}", json={"name": "New Name"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "updated"


@pytest.mark.asyncio
async def test_update_nonexistent_geofence(client: AsyncClient):
    resp = await client.put("/api/v1/geofences/9999", json={"name": "X"})
    assert resp.status_code == 404


# ── Delete geofence ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_geofence_with_confirmation(client: AsyncClient):
    create_resp = await client.post(
        "/api/v1/geofences",
        json={"name": "Doomed", "center_lat": 30.0, "center_lon": -97.0, "radius_m": 100},
    )
    fence_id = create_resp.json()["id"]

    resp = await client.delete(f"/api/v1/geofences/{fence_id}", params={"confirmed": "true"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_delete_without_confirmation(client: AsyncClient):
    create_resp = await client.post(
        "/api/v1/geofences",
        json={"name": "Safe", "center_lat": 30.0, "center_lon": -97.0, "radius_m": 100},
    )
    fence_id = create_resp.json()["id"]

    resp = await client.delete(f"/api/v1/geofences/{fence_id}")
    assert resp.status_code == 400
    assert "confirmed" in resp.json()["detail"].lower()


# ── Breaches ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_breaches_empty(client: AsyncClient):
    resp = await client.get("/api/v1/geofences/breaches")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0
