"""Tests for emergency broadcast API endpoints."""

from __future__ import annotations

import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

from jenn_mesh.dashboard.app import create_app
from jenn_mesh.db import MeshDatabase


@pytest.fixture
def db() -> MeshDatabase:
    """Fresh test database."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return MeshDatabase(db_path=tmp.name)


@pytest.fixture
def client(db: MeshDatabase) -> AsyncClient:
    """Async test client with the JennMesh dashboard app."""
    app = create_app(db=db)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestSendEmergencyBroadcast:
    """Tests for POST /api/v1/emergency/broadcast."""

    @pytest.mark.asyncio
    async def test_send_broadcast_success(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/emergency/broadcast",
            json={
                "type": "evacuation",
                "message": "Fire alarm. Evacuate now.",
                "confirmed": True,
                "sender": "operator-1",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["broadcast_id"] > 0
        assert data["type"] == "evacuation"
        assert data["message"] == "Fire alarm. Evacuate now."
        assert data["status"] == "pending"
        assert data["channel_index"] == 3

    @pytest.mark.asyncio
    async def test_send_broadcast_requires_confirmation(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/emergency/broadcast",
            json={
                "type": "evacuation",
                "message": "Fire alarm.",
                "confirmed": False,
            },
        )
        assert resp.status_code == 400
        assert "confirmation" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_send_broadcast_missing_confirmed(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/emergency/broadcast",
            json={
                "type": "evacuation",
                "message": "Fire alarm.",
            },
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_send_broadcast_invalid_type(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/emergency/broadcast",
            json={
                "type": "alien_invasion",
                "message": "They're here.",
                "confirmed": True,
            },
        )
        assert resp.status_code == 422
        assert "Invalid emergency type" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_send_broadcast_empty_message(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/emergency/broadcast",
            json={
                "type": "evacuation",
                "message": "   ",
                "confirmed": True,
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_send_all_emergency_types(self, client: AsyncClient) -> None:
        """Verify all 6 emergency types are accepted."""
        types = [
            "evacuation",
            "network_down",
            "severe_weather",
            "security_alert",
            "all_clear",
            "custom",
        ]
        for etype in types:
            resp = await client.post(
                "/api/v1/emergency/broadcast",
                json={
                    "type": etype,
                    "message": f"Test {etype} broadcast.",
                    "confirmed": True,
                },
            )
            assert resp.status_code == 200, f"Failed for type: {etype}"
            assert resp.json()["type"] == etype


class TestListBroadcasts:
    """Tests for GET /api/v1/emergency/broadcasts."""

    @pytest.mark.asyncio
    async def test_list_empty(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/emergency/broadcasts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["broadcasts"] == []

    @pytest.mark.asyncio
    async def test_list_after_creating(self, client: AsyncClient) -> None:
        await client.post(
            "/api/v1/emergency/broadcast",
            json={"type": "evacuation", "message": "Test.", "confirmed": True},
        )
        resp = await client.get("/api/v1/emergency/broadcasts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["broadcasts"][0]["message"] == "Test."

    @pytest.mark.asyncio
    async def test_list_with_limit(self, client: AsyncClient) -> None:
        for i in range(5):
            await client.post(
                "/api/v1/emergency/broadcast",
                json={"type": "custom", "message": f"Alert {i}.", "confirmed": True},
            )
        resp = await client.get("/api/v1/emergency/broadcasts?limit=3")
        assert resp.status_code == 200
        assert resp.json()["count"] == 3


class TestGetBroadcast:
    """Tests for GET /api/v1/emergency/broadcast/{broadcast_id}."""

    @pytest.mark.asyncio
    async def test_get_broadcast(self, client: AsyncClient) -> None:
        create_resp = await client.post(
            "/api/v1/emergency/broadcast",
            json={"type": "severe_weather", "message": "Tornado warning.", "confirmed": True},
        )
        broadcast_id = create_resp.json()["broadcast_id"]

        resp = await client.get(f"/api/v1/emergency/broadcast/{broadcast_id}")
        assert resp.status_code == 200
        assert resp.json()["broadcast_type"] == "severe_weather"
        assert resp.json()["message"] == "Tornado warning."

    @pytest.mark.asyncio
    async def test_get_broadcast_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/emergency/broadcast/9999")
        assert resp.status_code == 404


class TestFleetEmergencyStatus:
    """Tests for GET /api/v1/emergency/status."""

    @pytest.mark.asyncio
    async def test_status_empty(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/emergency/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active_broadcasts"] == 0
        assert data["last_broadcast_time"] is None

    @pytest.mark.asyncio
    async def test_status_with_active_broadcast(self, client: AsyncClient) -> None:
        await client.post(
            "/api/v1/emergency/broadcast",
            json={"type": "evacuation", "message": "Fire.", "confirmed": True},
        )
        resp = await client.get("/api/v1/emergency/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["active_broadcasts"] == 1
        assert data["last_broadcast_time"] is not None
        assert data["recent_count"] == 1


class TestHealthEndpointEmergencyComponent:
    """Test that /health includes the emergency_broadcasts component."""

    @pytest.mark.asyncio
    async def test_health_includes_emergency_broadcasts(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        components = data["components"]
        assert "emergency_broadcasts" in components
        eb = components["emergency_broadcasts"]
        assert eb["status"] == "healthy"
        assert "recent_count" in eb

    @pytest.mark.asyncio
    async def test_health_emergency_after_broadcast(
        self, client: AsyncClient, db: MeshDatabase
    ) -> None:
        db.create_emergency_broadcast("evacuation", "Test.", "operator", 3)
        resp = await client.get("/health")
        data = resp.json()
        eb = data["components"]["emergency_broadcasts"]
        assert eb["recent_count"] == 1
        assert eb["last_broadcast_time"] is not None
