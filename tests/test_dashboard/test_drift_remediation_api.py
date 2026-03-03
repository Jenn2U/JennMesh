"""Tests for drift remediation API endpoints."""

from __future__ import annotations

import tempfile
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from jenn_mesh.agent.remote_admin import RemoteAdminResult
from jenn_mesh.dashboard.app import create_app
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.device import ConfigHash


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


def _seed_drifted_device(
    db: MeshDatabase,
    node_id: str = "!aaa11111",
    template_role: str = "relay-node",
) -> str:
    """Seed a device with drift (config_hash ≠ template_hash). Returns template_hash."""
    yaml_content = f"owner:\n  long_name: {node_id}\nradio:\n  role: ROUTER\n"
    template_hash = ConfigHash.compute(yaml_content)
    db.upsert_device(node_id, long_name=f"Drifted-{node_id}")
    db.save_config_template(template_role, yaml_content, template_hash)
    with db.connection() as conn:
        conn.execute(
            """UPDATE devices SET template_role = ?, config_hash = ?, template_hash = ?
               WHERE node_id = ?""",
            (template_role, "drifted-hash-000", template_hash, node_id),
        )
    return template_hash


# ── Preview endpoint ──────────────────────────────────────────────────


class TestPreviewEndpoint:
    """Tests for GET /api/v1/config/drift/{node_id}/preview."""

    @pytest.mark.asyncio
    async def test_preview_drifted_device(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_drifted_device(db)
        resp = await client.get("/api/v1/config/drift/!aaa11111/preview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_id"] == "!aaa11111"
        assert data["drifted"] is True
        assert data["template_role"] == "relay-node"
        assert data["template_yaml"] is not None
        assert data["device_hash"] == "drifted-hash-000"

    @pytest.mark.asyncio
    async def test_preview_device_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/config/drift/!unknown/preview")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_preview_manager_unavailable(self, client: AsyncClient) -> None:
        """503 when manager not wired."""
        # Clear the manager from app state
        app = client._transport.app  # type: ignore[attr-defined]
        if hasattr(app.state, "drift_remediation_manager"):
            delattr(app.state, "drift_remediation_manager")
        resp = await client.get("/api/v1/config/drift/!aaa11111/preview")
        assert resp.status_code == 503


# ── Remediate single device endpoint ──────────────────────────────────


class TestRemediateDeviceEndpoint:
    """Tests for POST /api/v1/config/drift/{node_id}/remediate."""

    @pytest.mark.asyncio
    async def test_requires_confirmation(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_drifted_device(db)
        resp = await client.post(
            "/api/v1/config/drift/!aaa11111/remediate",
            json={"confirmed": False},
        )
        assert resp.status_code == 400
        assert "confirmation" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_missing_confirmed_field(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_drifted_device(db)
        resp = await client.post(
            "/api/v1/config/drift/!aaa11111/remediate",
            json={},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @patch("jenn_mesh.core.drift_remediation.RemoteAdmin")
    async def test_successful_remediation(
        self, mock_admin_cls: MagicMock, client: AsyncClient, db: MeshDatabase
    ) -> None:
        _seed_drifted_device(db)
        mock_admin = MagicMock()
        mock_admin.apply_remote_config.return_value = RemoteAdminResult(
            success=True, node_id="!aaa11111", command="configure", output="OK"
        )
        mock_admin_cls.return_value = mock_admin

        resp = await client.post(
            "/api/v1/config/drift/!aaa11111/remediate",
            json={"confirmed": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "delivered"
        assert data["template_role"] == "relay-node"

    @pytest.mark.asyncio
    async def test_device_not_found(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/config/drift/!unknown/remediate",
            json={"confirmed": True},
        )
        assert resp.status_code == 404


# ── Remediate all endpoint ────────────────────────────────────────────


class TestRemediateAllEndpoint:
    """Tests for POST /api/v1/config/drift/remediate-all."""

    @pytest.mark.asyncio
    async def test_requires_confirmation(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/config/drift/remediate-all",
            json={"confirmed": False},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_empty_fleet(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/config/drift/remediate-all",
            json={"confirmed": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["delivered"] == 0

    @pytest.mark.asyncio
    async def test_manager_unavailable(self, client: AsyncClient) -> None:
        app = client._transport.app  # type: ignore[attr-defined]
        if hasattr(app.state, "drift_remediation_manager"):
            delattr(app.state, "drift_remediation_manager")
        resp = await client.post(
            "/api/v1/config/drift/remediate-all",
            json={"confirmed": True},
        )
        assert resp.status_code == 503


# ── Status endpoint ───────────────────────────────────────────────────


class TestStatusEndpoint:
    """Tests for GET /api/v1/config/drift/{node_id}/status."""

    @pytest.mark.asyncio
    async def test_status_with_data(self, client: AsyncClient, db: MeshDatabase) -> None:
        _seed_drifted_device(db)
        resp = await client.get("/api/v1/config/drift/!aaa11111/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_id"] == "!aaa11111"
        assert data["drifted"] is True
        assert data["template_role"] == "relay-node"
        assert "pending_queue_entries" in data
        assert "active_alerts" in data
        assert "recent_remediation_log" in data

    @pytest.mark.asyncio
    async def test_status_not_found(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/config/drift/!unknown/status")
        assert resp.status_code == 404


# ── Health endpoint integration ───────────────────────────────────────


class TestHealthDriftComponent:
    """Test that /health includes the drift_remediation component."""

    @pytest.mark.asyncio
    async def test_health_includes_drift_remediation(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        dr = data["components"]["drift_remediation"]
        assert dr["status"] == "healthy"
        assert "drifted_device_count" in dr
