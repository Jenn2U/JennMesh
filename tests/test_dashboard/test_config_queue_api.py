"""Tests for config queue API endpoints."""

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


def _seed_entry(db: MeshDatabase, node_id: str = "!aaa11111", role: str = "relay") -> int:
    """Create a config queue entry and return its ID."""
    return db.create_config_queue_entry(
        target_node_id=node_id,
        template_role=role,
        config_hash="abc123",
        yaml_content="owner:\n  long_name: Test\n",
    )


# ── List entries ──────────────────────────────────────────────────────


class TestListConfigQueueEntries:
    """Tests for GET /api/v1/config-queue/entries."""

    @pytest.mark.asyncio
    async def test_list_empty(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/config-queue/entries")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["entries"] == []

    @pytest.mark.asyncio
    async def test_list_with_entries(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_entry(db, "!aaa11111")
        _seed_entry(db, "!bbb22222")
        resp = await client.get("/api/v1/config-queue/entries")
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    @pytest.mark.asyncio
    async def test_list_filter_by_node(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_entry(db, "!aaa11111")
        _seed_entry(db, "!bbb22222")
        resp = await client.get("/api/v1/config-queue/entries?target_node_id=!aaa11111")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["entries"][0]["target_node_id"] == "!aaa11111"

    @pytest.mark.asyncio
    async def test_list_filter_by_status(self, client: AsyncClient, db: MeshDatabase) -> None:
        id1 = _seed_entry(db, "!aaa11111")
        _seed_entry(db, "!bbb22222")
        db.update_config_queue_status(id1, "delivered")
        resp = await client.get("/api/v1/config-queue/entries?status=delivered")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_list_with_limit(self, client: AsyncClient, db: MeshDatabase) -> None:
        for i in range(5):
            _seed_entry(db, f"!node{i:04d}")
        resp = await client.get("/api/v1/config-queue/entries?limit=3")
        assert resp.status_code == 200
        assert resp.json()["count"] == 3


# ── Get entry ─────────────────────────────────────────────────────────


class TestGetConfigQueueEntry:
    """Tests for GET /api/v1/config-queue/entry/{entry_id}."""

    @pytest.mark.asyncio
    async def test_get_entry(self, client: AsyncClient, db: MeshDatabase) -> None:
        entry_id = _seed_entry(db)
        resp = await client.get(f"/api/v1/config-queue/entry/{entry_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == entry_id
        assert data["target_node_id"] == "!aaa11111"
        assert data["template_role"] == "relay"
        assert data["status"] == "pending"

    @pytest.mark.asyncio
    async def test_get_entry_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/config-queue/entry/9999")
        assert resp.status_code == 404


# ── Retry entry ───────────────────────────────────────────────────────


class TestRetryConfigQueueEntry:
    """Tests for POST /api/v1/config-queue/entry/{entry_id}/retry."""

    @pytest.mark.asyncio
    async def test_retry_failed_entry(self, client: AsyncClient, db: MeshDatabase) -> None:
        entry_id = _seed_entry(db)
        db.update_config_queue_status(entry_id, "failed_permanent", retry_count=10)
        resp = await client.post(
            f"/api/v1/config-queue/entry/{entry_id}/retry",
            json={"confirmed": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        assert data["retry_count"] == 10  # preserved

    @pytest.mark.asyncio
    async def test_retry_requires_confirmation(self, client: AsyncClient, db: MeshDatabase) -> None:
        entry_id = _seed_entry(db)
        db.update_config_queue_status(entry_id, "failed_permanent")
        resp = await client.post(
            f"/api/v1/config-queue/entry/{entry_id}/retry",
            json={"confirmed": False},
        )
        assert resp.status_code == 400
        assert "confirmation" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_retry_missing_confirmed(self, client: AsyncClient, db: MeshDatabase) -> None:
        entry_id = _seed_entry(db)
        db.update_config_queue_status(entry_id, "failed_permanent")
        resp = await client.post(
            f"/api/v1/config-queue/entry/{entry_id}/retry",
            json={},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_retry_pending_entry_fails(self, client: AsyncClient, db: MeshDatabase) -> None:
        """Cannot retry an entry that's still pending."""
        entry_id = _seed_entry(db)
        resp = await client.post(
            f"/api/v1/config-queue/entry/{entry_id}/retry",
            json={"confirmed": True},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_retry_not_found(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/config-queue/entry/9999/retry",
            json={"confirmed": True},
        )
        assert resp.status_code == 404


# ── Cancel entry ──────────────────────────────────────────────────────


class TestCancelConfigQueueEntry:
    """Tests for POST /api/v1/config-queue/entry/{entry_id}/cancel."""

    @pytest.mark.asyncio
    async def test_cancel_pending(self, client: AsyncClient, db: MeshDatabase) -> None:
        entry_id = _seed_entry(db)
        resp = await client.post(
            f"/api/v1/config-queue/entry/{entry_id}/cancel",
            json={"confirmed": True},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_requires_confirmation(
        self, client: AsyncClient, db: MeshDatabase
    ) -> None:
        entry_id = _seed_entry(db)
        resp = await client.post(
            f"/api/v1/config-queue/entry/{entry_id}/cancel",
            json={"confirmed": False},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_cancel_delivered_fails(self, client: AsyncClient, db: MeshDatabase) -> None:
        entry_id = _seed_entry(db)
        db.update_config_queue_status(entry_id, "delivered")
        resp = await client.post(
            f"/api/v1/config-queue/entry/{entry_id}/cancel",
            json={"confirmed": True},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_not_found(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/config-queue/entry/9999/cancel",
            json={"confirmed": True},
        )
        assert resp.status_code == 404


# ── Device queue status ───────────────────────────────────────────────


class TestDeviceQueueStatus:
    """Tests for GET /api/v1/config-queue/status/{node_id}."""

    @pytest.mark.asyncio
    async def test_status_empty(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/config-queue/status/!unknown")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_id"] == "!unknown"
        assert data["total_entries"] == 0
        assert data["pending_count"] == 0

    @pytest.mark.asyncio
    async def test_status_with_entries(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_entry(db, "!aaa11111", "relay")
        _seed_entry(db, "!aaa11111", "client")
        resp = await client.get("/api/v1/config-queue/status/!aaa11111")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_id"] == "!aaa11111"
        assert data["total_entries"] == 2
        assert data["pending_count"] == 2


# ── Health endpoint integration ───────────────────────────────────────


class TestHealthConfigQueueComponent:
    """Test that /health includes the config_queue component."""

    @pytest.mark.asyncio
    async def test_health_includes_config_queue(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        cq = data["components"]["config_queue"]
        assert cq["status"] == "healthy"
        assert "pending_count" in cq
        assert "failed_permanent_count" in cq
        assert "total_delivered" in cq

    @pytest.mark.asyncio
    async def test_health_config_queue_counts(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_entry(db, "!aaa11111")
        id2 = _seed_entry(db, "!bbb22222")
        db.update_config_queue_status(id2, "delivered")
        resp = await client.get("/health")
        data = resp.json()
        cq = data["components"]["config_queue"]
        assert cq["pending_count"] >= 1
        assert cq["total_delivered"] >= 1
