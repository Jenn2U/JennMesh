"""Tests for sync relay API endpoints (MESH-027)."""

from __future__ import annotations

import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

from jenn_mesh.dashboard.app import create_app
from jenn_mesh.db import MeshDatabase

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def db() -> MeshDatabase:
    """Fresh test database."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return MeshDatabase(db_path=tmp.name)


@pytest.fixture
def client(db: MeshDatabase) -> AsyncClient:
    """Async test client with sync relay wired."""
    app = create_app(db=db)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── Status endpoint ──────────────────────────────────────────────────


class TestStatusEndpoint:
    """GET /api/v1/sync-relay/status"""

    @pytest.mark.asyncio
    async def test_status_returns_200(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/sync-relay/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "active_sessions" in data
        assert "pending_queue_entries" in data

    @pytest.mark.asyncio
    async def test_status_unavailable_when_no_manager(self, db: MeshDatabase) -> None:
        app = create_app(db=db)
        if hasattr(app.state, "sync_relay_manager"):
            delattr(app.state, "sync_relay_manager")
        transport = ASGITransport(app=app)
        c = AsyncClient(transport=transport, base_url="http://test")
        resp = await c.get("/api/v1/sync-relay/status")
        assert resp.status_code == 503


# ── Sessions endpoint ─────────────────────────────────────────────────


class TestSessionsEndpoint:
    """GET /api/v1/sync-relay/sessions"""

    @pytest.mark.asyncio
    async def test_empty_sessions(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/sync-relay/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["sessions"] == []

    @pytest.mark.asyncio
    async def test_sessions_with_data(self, client: AsyncClient, db: MeshDatabase) -> None:
        # Seed a sync log entry
        log_id = db.create_sync_log(
            node_id="!abc123",
            direction="to_edge",
            session_id="sess01",
        )
        db.update_sync_log(log_id, status="completed", items_synced=5)

        resp = await client.get("/api/v1/sync-relay/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1


# ── Session detail endpoint ───────────────────────────────────────────


class TestSessionDetailEndpoint:
    """GET /api/v1/sync-relay/session/{session_id}"""

    @pytest.mark.asyncio
    async def test_session_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/sync-relay/session/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_session_with_fragments(self, client: AsyncClient, db: MeshDatabase) -> None:
        # Seed fragments
        db.create_sync_fragment(
            session_id="fragtest",
            seq=0,
            total=2,
            direction="outbound",
            payload_b64="dGVzdA==",
            crc16="ab12",
        )
        db.create_sync_fragment(
            session_id="fragtest",
            seq=1,
            total=2,
            direction="outbound",
            payload_b64="dGVzdDI=",
            crc16="cd34",
        )

        resp = await client.get("/api/v1/sync-relay/session/fragtest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "fragtest"
        assert data["total_fragments"] == 2
        assert len(data["fragments"]) == 2


# ── Log endpoint ──────────────────────────────────────────────────────


class TestLogEndpoint:
    """GET /api/v1/sync-relay/log"""

    @pytest.mark.asyncio
    async def test_empty_log(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/sync-relay/log")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["entries"] == []

    @pytest.mark.asyncio
    async def test_log_with_entries(self, client: AsyncClient, db: MeshDatabase) -> None:
        log_id = db.create_sync_log(
            node_id="!abc123",
            direction="to_edge",
            session_id="logsess",
        )
        db.update_sync_log(log_id, status="completed", items_synced=3)

        resp = await client.get("/api/v1/sync-relay/log")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1

    @pytest.mark.asyncio
    async def test_log_filter_by_node(self, client: AsyncClient, db: MeshDatabase) -> None:
        db.create_sync_log(node_id="!aaa", direction="to_edge", session_id="s1")
        db.create_sync_log(node_id="!bbb", direction="from_edge", session_id="s2")

        resp = await client.get("/api/v1/sync-relay/log?node_id=!aaa")
        data = resp.json()
        for entry in data["entries"]:
            assert entry["node_id"] == "!aaa"


# ── Trigger endpoint ──────────────────────────────────────────────────


class TestTriggerEndpoint:
    """POST /api/v1/sync-relay/trigger/{node_id}"""

    @pytest.mark.asyncio
    async def test_trigger_requires_confirmation(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/sync-relay/trigger/!abc123",
            json={"confirmed": False},
        )
        assert resp.status_code == 400
        assert "confirmation" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_trigger_unknown_node(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/sync-relay/trigger/!nonexistent",
            json={"confirmed": True},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_trigger_unavailable_when_no_manager(self, db: MeshDatabase) -> None:
        app = create_app(db=db)
        if hasattr(app.state, "sync_relay_manager"):
            delattr(app.state, "sync_relay_manager")
        transport = ASGITransport(app=app)
        c = AsyncClient(transport=transport, base_url="http://test")
        resp = await c.post(
            "/api/v1/sync-relay/trigger/!abc",
            json={"confirmed": True},
        )
        assert resp.status_code == 503


# ── Health endpoint includes sync_relay ──────────────────────────────


class TestHealthIncludesSyncRelay:
    """GET /health includes sync_relay component."""

    @pytest.mark.asyncio
    async def test_health_has_sync_relay_component(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "sync_relay" in data["components"]
        # Manager wired in create_app(db=...) → status is healthy
        assert data["components"]["sync_relay"]["status"] == "healthy"
