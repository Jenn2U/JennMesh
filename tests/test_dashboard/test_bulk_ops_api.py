"""Tests for bulk fleet operations API endpoints."""

from __future__ import annotations

import json
import tempfile
import time

import pytest
from httpx import ASGITransport, AsyncClient

from jenn_mesh.dashboard.app import create_app
from jenn_mesh.db import MeshDatabase


@pytest.fixture
def db() -> MeshDatabase:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = MeshDatabase(db_path=tmp.name)
    db.upsert_device("!bulk1", long_name="R1", role="ROUTER")
    db.upsert_device("!bulk2", long_name="R2", role="ROUTER")
    db.upsert_device("!bulk3", long_name="C1", role="CLIENT")
    return db


@pytest.fixture
def client(db: MeshDatabase) -> AsyncClient:
    app = create_app(db=db)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestBulkOpsPreview:
    @pytest.mark.asyncio
    async def test_preview_all_devices(self, client):
        resp = await client.post(
            "/api/v1/bulk-ops/preview",
            json={
                "operation_type": "reboot",
                "target_filter": {"all_devices": True},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "preview"
        assert data["target_count"] == 3
        assert data["operation_type"] == "reboot"

    @pytest.mark.asyncio
    async def test_preview_by_role(self, client):
        resp = await client.post(
            "/api/v1/bulk-ops/preview",
            json={
                "operation_type": "config_push",
                "target_filter": {"role": "ROUTER"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["target_count"] == 2

    @pytest.mark.asyncio
    async def test_preview_factory_reset_warning(self, client):
        resp = await client.post(
            "/api/v1/bulk-ops/preview",
            json={
                "operation_type": "factory_reset",
                "target_filter": {"all_devices": True},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert any("factory reset" in w.lower() for w in data.get("warnings", []))

    @pytest.mark.asyncio
    async def test_preview_no_targets(self, client):
        resp = await client.post(
            "/api/v1/bulk-ops/preview",
            json={
                "operation_type": "reboot",
                "target_filter": {"role": "NONEXISTENT"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["target_count"] == 0
        assert any("no devices" in w.lower() for w in data.get("warnings", []))


class TestBulkOpsExecute:
    @pytest.mark.asyncio
    async def test_execute_rejects_dry_run(self, client):
        resp = await client.post(
            "/api/v1/bulk-ops/execute",
            json={
                "operation_type": "reboot",
                "target_filter": {"all_devices": True},
                "dry_run": True,
                "confirmed": True,
            },
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_execute_rejects_unconfirmed(self, client):
        resp = await client.post(
            "/api/v1/bulk-ops/execute",
            json={
                "operation_type": "reboot",
                "target_filter": {"all_devices": True},
                "dry_run": False,
                "confirmed": False,
            },
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_execute_reboot(self, client):
        resp = await client.post(
            "/api/v1/bulk-ops/execute",
            json={
                "operation_type": "reboot",
                "target_filter": {"all_devices": True},
                "dry_run": False,
                "confirmed": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["target_count"] == 3

    @pytest.mark.asyncio
    async def test_execute_no_targets_fails(self, client):
        resp = await client.post(
            "/api/v1/bulk-ops/execute",
            json={
                "operation_type": "reboot",
                "target_filter": {"role": "NOPE"},
                "dry_run": False,
                "confirmed": True,
            },
        )
        assert resp.status_code == 400


class TestBulkOpsProgress:
    @pytest.mark.asyncio
    async def test_get_progress(self, client, db):
        op_id = db.create_bulk_operation(
            operation_type="reboot",
            target_node_ids='["!bulk1", "!bulk2"]',
            total_targets=2,
            status="running",
        )
        resp = await client.get(f"/api/v1/bulk-ops/{op_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["operation_type"] == "reboot"
        assert data["total_targets"] == 2

    @pytest.mark.asyncio
    async def test_get_progress_not_found(self, client):
        resp = await client.get("/api/v1/bulk-ops/9999")
        assert resp.status_code == 404


class TestBulkOpsCancel:
    @pytest.mark.asyncio
    async def test_cancel_running(self, client, db):
        op_id = db.create_bulk_operation(
            operation_type="reboot",
            target_node_ids='["!bulk1"]',
            total_targets=1,
            status="running",
        )
        resp = await client.post(f"/api/v1/bulk-ops/{op_id}/cancel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_already_completed(self, client, db):
        op_id = db.create_bulk_operation(
            operation_type="reboot",
            target_node_ids='["!bulk1"]',
            total_targets=1,
            status="completed",
        )
        resp = await client.post(f"/api/v1/bulk-ops/{op_id}/cancel")
        assert resp.status_code == 400


class TestBulkOpsList:
    @pytest.mark.asyncio
    async def test_list_operations(self, client, db):
        db.create_bulk_operation(operation_type="reboot", total_targets=2, status="completed")
        db.create_bulk_operation(operation_type="psk_rotation", total_targets=5, status="running")
        resp = await client.get("/api/v1/bulk-ops")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2

    @pytest.mark.asyncio
    async def test_list_with_status_filter(self, client, db):
        db.create_bulk_operation(operation_type="reboot", total_targets=2, status="completed")
        db.create_bulk_operation(operation_type="psk_rotation", total_targets=5, status="running")
        resp = await client.get("/api/v1/bulk-ops?status=running")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["operations"][0]["operation_type"] == "psk_rotation"
