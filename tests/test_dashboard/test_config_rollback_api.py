"""Tests for config rollback API endpoints."""

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
    """Async test client with config rollback manager wired."""
    app = create_app(db=db)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _seed_device(db: MeshDatabase, node_id: str = "!abc") -> None:
    """Seed a device so snapshot operations have a valid target."""
    db.upsert_device(node_id, long_name="Test-Node")
    with db.connection() as conn:
        conn.execute(
            "UPDATE devices SET last_seen = datetime('now'),"
            " mesh_status = 'online' WHERE node_id = ?",
            (node_id,),
        )


def _create_snapshot(
    db: MeshDatabase,
    node_id: str = "!abc",
    push_source: str = "bulk_push",
    yaml_before: str = "owner: me\n",
    status: str = "monitoring",
) -> int:
    """Create a config snapshot and return its ID."""
    snap_id = db.create_config_snapshot(node_id, push_source, yaml_before=yaml_before)
    if status != "active":
        db.update_config_snapshot(snap_id, status=status)
    return snap_id


# ── List snapshots endpoint ──────────────────────────────────────────


class TestListSnapshots:
    """GET /api/v1/config-rollback/snapshots"""

    @pytest.mark.asyncio
    async def test_empty_list(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/config-rollback/snapshots")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["snapshots"] == []

    @pytest.mark.asyncio
    async def test_with_snapshots(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_device(db)
        _create_snapshot(db, status="monitoring")
        _create_snapshot(db, status="confirmed")
        resp = await client.get("/api/v1/config-rollback/snapshots")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2

    @pytest.mark.asyncio
    async def test_filter_by_node_id(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_device(db, "!abc")
        _seed_device(db, "!xyz")
        _create_snapshot(db, node_id="!abc")
        _create_snapshot(db, node_id="!xyz")
        resp = await client.get("/api/v1/config-rollback/snapshots?node_id=!abc")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["snapshots"][0]["node_id"] == "!abc"

    @pytest.mark.asyncio
    async def test_manager_unavailable(self, client: AsyncClient) -> None:
        app = client._transport.app  # type: ignore[attr-defined]
        if hasattr(app.state, "config_rollback_manager"):
            delattr(app.state, "config_rollback_manager")
        resp = await client.get("/api/v1/config-rollback/snapshots")
        assert resp.status_code == 503


# ── Get snapshot endpoint ────────────────────────────────────────────


class TestGetSnapshot:
    """GET /api/v1/config-rollback/snapshot/{id}"""

    @pytest.mark.asyncio
    async def test_get_existing_snapshot(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_device(db)
        snap_id = _create_snapshot(db)
        resp = await client.get(f"/api/v1/config-rollback/snapshot/{snap_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_id"] == "!abc"
        assert data["yaml_before"] == "owner: me\n"

    @pytest.mark.asyncio
    async def test_snapshot_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/config-rollback/snapshot/9999")
        assert resp.status_code == 404


# ── Manual rollback endpoint ─────────────────────────────────────────


class TestManualRollback:
    """POST /api/v1/config-rollback/snapshot/{id}/rollback"""

    @pytest.mark.asyncio
    async def test_requires_confirmation(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_device(db)
        snap_id = _create_snapshot(db)
        resp = await client.post(
            f"/api/v1/config-rollback/snapshot/{snap_id}/rollback",
            json={"confirmed": False},
        )
        assert resp.status_code == 400
        assert "confirmed" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_rollback_success(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_device(db)
        snap_id = _create_snapshot(db)
        mock_result = RemoteAdminResult(success=True, node_id="!abc", command="apply", output="OK")
        # Patch the already-instantiated _admin on the manager (created eagerly in __init__)
        app = client._transport.app  # type: ignore[attr-defined]
        manager = app.state.config_rollback_manager
        with patch.object(manager._admin, "apply_remote_config", return_value=mock_result):
            resp = await client.post(
                f"/api/v1/config-rollback/snapshot/{snap_id}/rollback",
                json={"confirmed": True},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["node_id"] == "!abc"

    @pytest.mark.asyncio
    async def test_rollback_no_yaml_before(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_device(db)
        snap_id = db.create_config_snapshot("!abc", "bulk_push", yaml_before=None)
        db.update_config_snapshot(snap_id, status="monitoring")
        resp = await client.post(
            f"/api/v1/config-rollback/snapshot/{snap_id}/rollback",
            json={"confirmed": True},
        )
        # manual_rollback returns {"error": ...}, route converts to 422
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_rollback_snapshot_not_found(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/config-rollback/snapshot/9999/rollback",
            json={"confirmed": True},
        )
        assert resp.status_code == 422


# ── Status endpoint ──────────────────────────────────────────────────


class TestRollbackStatus:
    """GET /api/v1/config-rollback/status"""

    @pytest.mark.asyncio
    async def test_empty_status(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/config-rollback/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["monitoring_count"] == 0
        assert data["recent_snapshot_count"] == 0
        assert "monitoring_minutes" in data

    @pytest.mark.asyncio
    async def test_status_with_monitoring(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_device(db)
        _create_snapshot(db, status="monitoring")
        _create_snapshot(db, status="confirmed")
        resp = await client.get("/api/v1/config-rollback/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["monitoring_count"] == 1
        assert data["recent_snapshot_count"] == 2
        assert data["status_breakdown"]["monitoring"] == 1
        assert data["status_breakdown"]["confirmed"] == 1


# ── Health endpoint includes config_rollback ─────────────────────────


class TestHealthIncludesRollback:
    """GET /health includes config_rollback component."""

    @pytest.mark.asyncio
    async def test_health_has_config_rollback_component(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "config_rollback" in data["components"]
        comp = data["components"]["config_rollback"]
        assert comp["status"] == "healthy"
        assert comp["monitoring_count"] == 0
