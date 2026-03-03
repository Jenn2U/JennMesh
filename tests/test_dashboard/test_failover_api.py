"""Tests for failover API endpoints."""

from __future__ import annotations

import tempfile
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from jenn_mesh.agent.remote_admin import RemoteAdminResult
from jenn_mesh.dashboard.app import create_app
from jenn_mesh.db import MeshDatabase

# ── Fixtures & helpers ───────────────────────────────────────────────


@pytest.fixture
def db() -> MeshDatabase:
    """Fresh test database."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return MeshDatabase(db_path=tmp.name)


@pytest.fixture
def client(db: MeshDatabase) -> AsyncClient:
    """Async test client with failover manager wired."""
    app = create_app(db=db)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _seed_relay_topology(db: MeshDatabase) -> None:
    """Seed a linear A ─ B ─ C topology where B is the relay SPOF.

    B is a ROUTER (relay) with good battery, A and C are CLIENTs.
    """
    db.upsert_device("!a", long_name="Node-A", role="CLIENT")
    db.upsert_device("!b", long_name="Relay-B", role="ROUTER")
    db.upsert_device("!c", long_name="Node-C", role="CLIENT")

    # Mark all online with good battery
    for nid in ("!a", "!b", "!c"):
        with db.connection() as conn:
            conn.execute(
                "UPDATE devices SET last_seen = datetime('now'),"
                " battery_level = 80 WHERE node_id = ?",
                (nid,),
            )

    # Store edges: A↔B, B↔C
    db.upsert_topology_edge("!a", "!b", snr=10.0)
    db.upsert_topology_edge("!b", "!a", snr=10.0)
    db.upsert_topology_edge("!b", "!c", snr=8.0)
    db.upsert_topology_edge("!c", "!b", snr=8.0)


# ── Assess endpoint ─────────────────────────────────────────────────


class TestAssessEndpoint:
    """GET /api/v1/failover/{node_id}/assess"""

    @pytest.mark.asyncio
    async def test_assess_relay_spof(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_relay_topology(db)
        resp = await client.get("/api/v1/failover/!b/assess")
        assert resp.status_code == 200
        data = resp.json()
        assert data["failed_node_id"] == "!b"
        assert data["is_spof"] is True
        assert len(data["dependent_nodes"]) > 0

    @pytest.mark.asyncio
    async def test_assess_unknown_node(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/failover/!unknown/assess")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_assess_manager_unavailable(self, client: AsyncClient) -> None:
        app = client._transport.app  # type: ignore[attr-defined]
        if hasattr(app.state, "failover_manager"):
            delattr(app.state, "failover_manager")
        resp = await client.get("/api/v1/failover/!b/assess")
        assert resp.status_code == 503


# ── Execute endpoint ─────────────────────────────────────────────────


class TestExecuteEndpoint:
    """POST /api/v1/failover/{node_id}/execute"""

    @pytest.mark.asyncio
    async def test_execute_requires_confirmation(
        self, client: AsyncClient, db: MeshDatabase
    ) -> None:
        _seed_relay_topology(db)
        resp = await client.post("/api/v1/failover/!b/execute", json={"confirmed": False})
        assert resp.status_code == 400
        assert "confirmed" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_execute_success(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_relay_topology(db)
        mock_result = RemoteAdminResult(success=True, node_id="!mock", command="set", output="OK")
        with patch("jenn_mesh.core.failover_manager.RemoteAdmin") as MockAdmin:
            MockAdmin.return_value.set_remote_config.return_value = mock_result
            resp = await client.post("/api/v1/failover/!b/execute", json={"confirmed": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "active"
        assert data["failed_node_id"] == "!b"

    @pytest.mark.asyncio
    async def test_execute_manager_unavailable(self, client: AsyncClient) -> None:
        app = client._transport.app  # type: ignore[attr-defined]
        if hasattr(app.state, "failover_manager"):
            delattr(app.state, "failover_manager")
        resp = await client.post("/api/v1/failover/!b/execute", json={"confirmed": True})
        assert resp.status_code == 503


# ── Revert endpoint ──────────────────────────────────────────────────


class TestRevertEndpoint:
    """POST /api/v1/failover/{event_id}/revert"""

    @pytest.mark.asyncio
    async def test_revert_requires_confirmation(
        self, client: AsyncClient, db: MeshDatabase
    ) -> None:
        _seed_relay_topology(db)
        # First execute a failover to get an event_id
        mock_result = RemoteAdminResult(success=True, node_id="!mock", command="set", output="OK")
        with patch("jenn_mesh.core.failover_manager.RemoteAdmin") as MockAdmin:
            MockAdmin.return_value.set_remote_config.return_value = mock_result
            exec_resp = await client.post("/api/v1/failover/!b/execute", json={"confirmed": True})
        event_id = exec_resp.json()["event_id"]

        resp = await client.post(f"/api/v1/failover/{event_id}/revert", json={"confirmed": False})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_revert_success(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_relay_topology(db)
        mock_result = RemoteAdminResult(success=True, node_id="!mock", command="set", output="OK")
        with patch("jenn_mesh.core.failover_manager.RemoteAdmin") as MockAdmin:
            MockAdmin.return_value.set_remote_config.return_value = mock_result
            exec_resp = await client.post("/api/v1/failover/!b/execute", json={"confirmed": True})
            event_id = exec_resp.json()["event_id"]

            resp = await client.post(
                f"/api/v1/failover/{event_id}/revert", json={"confirmed": True}
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "reverted"

    @pytest.mark.asyncio
    async def test_revert_nonexistent_event(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/failover/9999/revert", json={"confirmed": True})
        assert resp.status_code == 404


# ── Cancel endpoint ──────────────────────────────────────────────────


class TestCancelEndpoint:
    """POST /api/v1/failover/{event_id}/cancel"""

    @pytest.mark.asyncio
    async def test_cancel_requires_confirmation(
        self, client: AsyncClient, db: MeshDatabase
    ) -> None:
        _seed_relay_topology(db)
        mock_result = RemoteAdminResult(success=True, node_id="!mock", command="set", output="OK")
        with patch("jenn_mesh.core.failover_manager.RemoteAdmin") as MockAdmin:
            MockAdmin.return_value.set_remote_config.return_value = mock_result
            exec_resp = await client.post("/api/v1/failover/!b/execute", json={"confirmed": True})
        event_id = exec_resp.json()["event_id"]

        resp = await client.post(f"/api/v1/failover/{event_id}/cancel", json={"confirmed": False})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_cancel_success(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_relay_topology(db)
        mock_result = RemoteAdminResult(success=True, node_id="!mock", command="set", output="OK")
        with patch("jenn_mesh.core.failover_manager.RemoteAdmin") as MockAdmin:
            MockAdmin.return_value.set_remote_config.return_value = mock_result
            exec_resp = await client.post("/api/v1/failover/!b/execute", json={"confirmed": True})
        event_id = exec_resp.json()["event_id"]

        resp = await client.post(f"/api/v1/failover/{event_id}/cancel", json={"confirmed": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"


# ── Status endpoint ──────────────────────────────────────────────────


class TestStatusEndpoint:
    """GET /api/v1/failover/{node_id}/status"""

    @pytest.mark.asyncio
    async def test_status_no_failover(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_relay_topology(db)
        resp = await client.get("/api/v1/failover/!b/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_id"] == "!b"
        assert data["has_active_failover"] is False

    @pytest.mark.asyncio
    async def test_status_with_active_failover(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_relay_topology(db)
        mock_result = RemoteAdminResult(success=True, node_id="!mock", command="set", output="OK")
        with patch("jenn_mesh.core.failover_manager.RemoteAdmin") as MockAdmin:
            MockAdmin.return_value.set_remote_config.return_value = mock_result
            await client.post("/api/v1/failover/!b/execute", json={"confirmed": True})

        resp = await client.get("/api/v1/failover/!b/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_active_failover"] is True


# ── Active list endpoint ─────────────────────────────────────────────


class TestActiveListEndpoint:
    """GET /api/v1/failover/active"""

    @pytest.mark.asyncio
    async def test_no_active_failovers(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/failover/active")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["events"] == []

    @pytest.mark.asyncio
    async def test_with_active_failover(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_relay_topology(db)
        mock_result = RemoteAdminResult(success=True, node_id="!mock", command="set", output="OK")
        with patch("jenn_mesh.core.failover_manager.RemoteAdmin") as MockAdmin:
            MockAdmin.return_value.set_remote_config.return_value = mock_result
            await client.post("/api/v1/failover/!b/execute", json={"confirmed": True})

        resp = await client.get("/api/v1/failover/active")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["events"][0]["failed_node_id"] == "!b"


# ── Check recoveries endpoint ────────────────────────────────────────


class TestCheckRecoveriesEndpoint:
    """POST /api/v1/failover/check-recoveries"""

    @pytest.mark.asyncio
    async def test_no_active_failovers(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/failover/check-recoveries")
        assert resp.status_code == 200
        data = resp.json()
        assert data["checked"] == 0

    @pytest.mark.asyncio
    async def test_manager_unavailable(self, client: AsyncClient) -> None:
        app = client._transport.app  # type: ignore[attr-defined]
        if hasattr(app.state, "failover_manager"):
            delattr(app.state, "failover_manager")
        resp = await client.post("/api/v1/failover/check-recoveries")
        assert resp.status_code == 503


# ── Health endpoint includes failover ────────────────────────────────


class TestHealthIncludesFailover:
    """GET /health includes failover component."""

    @pytest.mark.asyncio
    async def test_health_has_failover_component(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "failover" in data["components"]
        assert data["components"]["failover"]["status"] == "healthy"
        assert data["components"]["failover"]["active_failover_count"] == 0
