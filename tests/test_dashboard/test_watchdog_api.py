"""Tests for watchdog API endpoints."""

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
    """Async test client with watchdog wired."""
    app = create_app(db=db)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── Status endpoint ──────────────────────────────────────────────────


class TestStatusEndpoint:
    """GET /api/v1/watchdog/status"""

    @pytest.mark.asyncio
    async def test_status_returns_200(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/watchdog/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert "checks" in data
        assert "total_cycles" in data

    @pytest.mark.asyncio
    async def test_status_lists_all_checks(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/watchdog/status")
        data = resp.json()
        expected_checks = {
            "offline_nodes",
            "stale_heartbeats",
            "low_battery",
            "health_scoring",
            "config_drift",
            "topology_spof",
            "failover_recovery",
            "baseline_deviation",
            "post_push_failures",
            "sync_health",
            "encryption_audit",
        }
        assert set(data["checks"].keys()) == expected_checks

    @pytest.mark.asyncio
    async def test_status_unavailable_when_no_watchdog(self, db: MeshDatabase) -> None:
        app = create_app(db=db)
        # Remove the watchdog
        if hasattr(app.state, "mesh_watchdog"):
            delattr(app.state, "mesh_watchdog")
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        resp = await client.get("/api/v1/watchdog/status")
        assert resp.status_code == 503


# ── History endpoint ──────────────────────────────────────────────────


class TestHistoryEndpoint:
    """GET /api/v1/watchdog/history"""

    @pytest.mark.asyncio
    async def test_empty_history(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/watchdog/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["runs"] == []

    @pytest.mark.asyncio
    async def test_history_with_runs(self, client: AsyncClient, db: MeshDatabase) -> None:
        # Seed a few runs
        run_id = db.create_watchdog_run("offline_nodes")
        db.complete_watchdog_run(run_id, result_summary='{"count": 2}')

        resp = await client.get("/api/v1/watchdog/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["runs"][0]["check_name"] == "offline_nodes"

    @pytest.mark.asyncio
    async def test_history_filter_by_check(self, client: AsyncClient, db: MeshDatabase) -> None:
        db.create_watchdog_run("offline_nodes")
        db.create_watchdog_run("low_battery")

        resp = await client.get("/api/v1/watchdog/history?check_name=offline_nodes")
        data = resp.json()
        assert data["count"] == 1
        assert data["runs"][0]["check_name"] == "offline_nodes"


# ── Trigger endpoint ─────────────────────────────────────────────────


class TestTriggerEndpoint:
    """POST /api/v1/watchdog/trigger/{check_name}"""

    @pytest.mark.asyncio
    async def test_trigger_valid_check(self, client: AsyncClient, db: MeshDatabase) -> None:
        # Seed minimal devices so checks don't blow up
        db.upsert_device("!a", long_name="Node-A", role="CLIENT")
        with db.connection() as conn:
            conn.execute(
                "UPDATE devices SET last_seen = datetime('now'),"
                " battery_level = 80, mesh_status = 'reachable'"
                " WHERE node_id = '!a'"
            )

        resp = await client.post("/api/v1/watchdog/trigger/offline_nodes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["check_name"] == "offline_nodes"
        assert "result" in data

    @pytest.mark.asyncio
    async def test_trigger_unknown_check(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/watchdog/trigger/nonexistent")
        assert resp.status_code == 404
        assert "nonexistent" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_trigger_unavailable_when_no_watchdog(self, db: MeshDatabase) -> None:
        app = create_app(db=db)
        if hasattr(app.state, "mesh_watchdog"):
            delattr(app.state, "mesh_watchdog")
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        resp = await client.post("/api/v1/watchdog/trigger/offline_nodes")
        assert resp.status_code == 503


# ── Health endpoint includes watchdog ─────────────────────────────────


class TestHealthIncludesWatchdog:
    """GET /health includes mesh_watchdog component."""

    @pytest.mark.asyncio
    async def test_health_has_watchdog_component(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "mesh_watchdog" in data["components"]
        assert data["components"]["mesh_watchdog"]["status"] == "healthy"
        assert data["components"]["mesh_watchdog"]["total_cycles"] == 0
        assert data["components"]["mesh_watchdog"]["enabled_checks"] == 11
