"""Tests for recovery command API endpoints."""

from __future__ import annotations

import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

from jenn_mesh.dashboard.app import create_app
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.recovery import generate_nonce


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


class TestSendRecoveryCommand:
    """Tests for POST /api/v1/recovery/send."""

    @pytest.mark.asyncio
    async def test_send_reboot_success(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/recovery/send",
            json={
                "target_node_id": "!a1b2c3d4",
                "command_type": "reboot",
                "confirmed": True,
                "sender": "operator-1",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["command_id"] > 0
        assert data["target_node_id"] == "!a1b2c3d4"
        assert data["command_type"] == "reboot"
        assert data["status"] == "pending"
        assert data["sender"] == "operator-1"
        assert data["nonce"] is not None

    @pytest.mark.asyncio
    async def test_send_restart_service_success(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/recovery/send",
            json={
                "target_node_id": "!deadbeef",
                "command_type": "restart_service",
                "args": "jennedge",
                "confirmed": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["command_type"] == "restart_service"
        assert data["args"] == "jennedge"

    @pytest.mark.asyncio
    async def test_send_system_status_success(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/recovery/send",
            json={
                "target_node_id": "!deadbeef",
                "command_type": "system_status",
                "confirmed": True,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["command_type"] == "system_status"

    @pytest.mark.asyncio
    async def test_requires_confirmation(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/recovery/send",
            json={
                "target_node_id": "!a1b2c3d4",
                "command_type": "reboot",
                "confirmed": False,
            },
        )
        assert resp.status_code == 400
        assert "confirmation" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_missing_confirmed_defaults_false(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/recovery/send",
            json={
                "target_node_id": "!a1b2c3d4",
                "command_type": "reboot",
            },
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_command_type(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/recovery/send",
            json={
                "target_node_id": "!a1b2c3d4",
                "command_type": "format_disk",
                "confirmed": True,
            },
        )
        assert resp.status_code == 422
        assert "Invalid command type" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_invalid_service_name(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/recovery/send",
            json={
                "target_node_id": "!a1b2c3d4",
                "command_type": "restart_service",
                "args": "nginx",
                "confirmed": True,
            },
        )
        assert resp.status_code == 422
        assert "Invalid service" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_invalid_node_id(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/recovery/send",
            json={
                "target_node_id": "bad_id",
                "command_type": "reboot",
                "confirmed": True,
            },
        )
        assert resp.status_code == 422
        assert "Invalid target_node_id" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_rate_limit_same_node(self, client: AsyncClient) -> None:
        """Sending two commands to the same node rapidly returns 429."""
        await client.post(
            "/api/v1/recovery/send",
            json={
                "target_node_id": "!a1b2c3d4",
                "command_type": "system_status",
                "confirmed": True,
            },
        )
        resp = await client.post(
            "/api/v1/recovery/send",
            json={
                "target_node_id": "!a1b2c3d4",
                "command_type": "system_status",
                "confirmed": True,
            },
        )
        assert resp.status_code == 429
        assert "Rate limited" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_rate_limit_different_nodes_ok(self, client: AsyncClient) -> None:
        """Two commands to different nodes should not be rate limited."""
        resp1 = await client.post(
            "/api/v1/recovery/send",
            json={
                "target_node_id": "!a1b2c3d4",
                "command_type": "system_status",
                "confirmed": True,
            },
        )
        resp2 = await client.post(
            "/api/v1/recovery/send",
            json={
                "target_node_id": "!deadbeef",
                "command_type": "system_status",
                "confirmed": True,
            },
        )
        assert resp1.status_code == 200
        assert resp2.status_code == 200


class TestListRecoveryCommands:
    """Tests for GET /api/v1/recovery/commands."""

    @pytest.mark.asyncio
    async def test_list_empty(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/recovery/commands")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["commands"] == []

    @pytest.mark.asyncio
    async def test_list_after_creating(self, client: AsyncClient) -> None:
        await client.post(
            "/api/v1/recovery/send",
            json={
                "target_node_id": "!a1b2c3d4",
                "command_type": "reboot",
                "confirmed": True,
            },
        )
        resp = await client.get("/api/v1/recovery/commands")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["commands"][0]["command_type"] == "reboot"

    @pytest.mark.asyncio
    async def test_list_filter_by_node(self, client: AsyncClient) -> None:
        await client.post(
            "/api/v1/recovery/send",
            json={
                "target_node_id": "!node1111",
                "command_type": "system_status",
                "confirmed": True,
            },
        )
        await client.post(
            "/api/v1/recovery/send",
            json={
                "target_node_id": "!node2222",
                "command_type": "reboot",
                "confirmed": True,
            },
        )
        resp = await client.get("/api/v1/recovery/commands?target_node_id=!node1111")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["commands"][0]["target_node_id"] == "!node1111"

    @pytest.mark.asyncio
    async def test_list_with_limit(self, client: AsyncClient, db: MeshDatabase) -> None:
        """Inject commands directly to avoid rate limit, then check limit query param."""
        for i in range(5):
            db.create_recovery_command(
                target_node_id=f"!node{i:04d}",
                command_type="system_status",
                args="",
                nonce=generate_nonce(),
                sender="test",
                expires_at="2030-01-01T00:05:00",
            )
        resp = await client.get("/api/v1/recovery/commands?limit=3")
        assert resp.status_code == 200
        assert resp.json()["count"] == 3


class TestGetRecoveryCommand:
    """Tests for GET /api/v1/recovery/command/{command_id}."""

    @pytest.mark.asyncio
    async def test_get_command(self, client: AsyncClient) -> None:
        create_resp = await client.post(
            "/api/v1/recovery/send",
            json={
                "target_node_id": "!a1b2c3d4",
                "command_type": "reboot",
                "confirmed": True,
            },
        )
        command_id = create_resp.json()["command_id"]

        resp = await client.get(f"/api/v1/recovery/command/{command_id}")
        assert resp.status_code == 200
        assert resp.json()["command_type"] == "reboot"
        assert resp.json()["target_node_id"] == "!a1b2c3d4"

    @pytest.mark.asyncio
    async def test_get_command_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/recovery/command/9999")
        assert resp.status_code == 404


class TestNodeRecoveryStatus:
    """Tests for GET /api/v1/recovery/status/{node_id}."""

    @pytest.mark.asyncio
    async def test_status_empty(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/recovery/status/!unknown")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_id"] == "!unknown"
        assert data["total_commands"] == 0
        assert data["pending_commands"] == 0

    @pytest.mark.asyncio
    async def test_status_with_command(self, client: AsyncClient) -> None:
        await client.post(
            "/api/v1/recovery/send",
            json={
                "target_node_id": "!a1b2c3d4",
                "command_type": "reboot",
                "confirmed": True,
            },
        )
        resp = await client.get("/api/v1/recovery/status/!a1b2c3d4")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_commands"] == 1
        assert data["pending_commands"] == 1
        assert data["last_command_status"] == "pending"


class TestHealthEndpointRecoveryComponent:
    """Test that /health includes the recovery_commands component."""

    @pytest.mark.asyncio
    async def test_health_includes_recovery_commands(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        components = data["components"]
        assert "recovery_commands" in components
        rc = components["recovery_commands"]
        assert rc["status"] == "healthy"
        assert "recent_count" in rc
        assert "pending_count" in rc

    @pytest.mark.asyncio
    async def test_health_recovery_after_command(
        self, client: AsyncClient, db: MeshDatabase
    ) -> None:
        db.create_recovery_command(
            target_node_id="!a1b2c3d4",
            command_type="reboot",
            args="",
            nonce=generate_nonce(),
            sender="test",
            expires_at="2030-01-01T00:05:00",
        )
        resp = await client.get("/health")
        data = resp.json()
        rc = data["components"]["recovery_commands"]
        assert rc["recent_count"] >= 1
        assert rc["pending_count"] >= 1
        assert rc["last_command_time"] is not None
