"""Tests for workbench and bulk push API endpoints."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from jenn_mesh.dashboard.app import create_app
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.workbench import (
    ApplyResult,
    BulkPushProgress,
    ConfigDiff,
    ConfigDiffEntry,
    ConfigSection,
    RadioConfig,
    SaveTemplateResult,
    WorkbenchStatus,
)

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def app(populated_db: MeshDatabase):
    """FastAPI app with workbench singletons injected."""
    return create_app(db=populated_db)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Mock helpers ─────────────────────────────────────────────────────


def _mock_status_connected() -> WorkbenchStatus:
    return WorkbenchStatus(
        connected=True,
        method="serial",
        address="/dev/ttyUSB0",
        node_id="!aabb1122",
        long_name="TestRadio",
        short_name="TST",
        hw_model="heltec_v3",
        firmware_version="2.5.6",
        uptime_seconds=3600,
    )


def _mock_radio_config() -> RadioConfig:
    return RadioConfig(
        sections=[
            ConfigSection(name="device", fields={"role": 4, "is_managed": False}),
            ConfigSection(name="lora", fields={"region": 1, "hop_limit": 3}),
        ],
        raw_yaml="device:\n  role: 4\nlora:\n  region: 1\n",
        config_hash="a" * 64,
    )


# ── Workbench endpoints ─────────────────────────────────────────────


class TestWorkbenchConnect:
    @pytest.mark.asyncio
    async def test_connect_serial(self, client: AsyncClient, app):
        wm = app.state.workbench
        with patch.object(wm, "connect", return_value=_mock_status_connected()):
            resp = await client.post(
                "/api/v1/workbench/connect",
                json={"method": "serial", "port": "/dev/ttyUSB0"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is True
        assert data["node_id"] == "!aabb1122"

    @pytest.mark.asyncio
    async def test_connect_failure(self, client: AsyncClient, app):
        wm = app.state.workbench
        with patch.object(
            wm,
            "connect",
            return_value=WorkbenchStatus(connected=False, error="No radio found"),
        ):
            resp = await client.post(
                "/api/v1/workbench/connect",
                json={"method": "serial"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is False
        assert "No radio found" in data["error"]


class TestWorkbenchDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect(self, client: AsyncClient, app):
        wm = app.state.workbench
        with patch.object(
            wm,
            "disconnect",
            return_value=WorkbenchStatus(connected=False),
        ):
            resp = await client.post("/api/v1/workbench/disconnect")
        assert resp.status_code == 200
        assert resp.json()["connected"] is False


class TestWorkbenchStatus:
    @pytest.mark.asyncio
    async def test_status_connected(self, client: AsyncClient, app):
        wm = app.state.workbench
        with patch.object(wm, "get_status", return_value=_mock_status_connected()):
            resp = await client.get("/api/v1/workbench/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is True
        assert data["hw_model"] == "heltec_v3"

    @pytest.mark.asyncio
    async def test_status_not_connected(self, client: AsyncClient, app):
        wm = app.state.workbench
        with patch.object(
            wm,
            "get_status",
            return_value=WorkbenchStatus(connected=False),
        ):
            resp = await client.get("/api/v1/workbench/status")
        assert resp.status_code == 200
        assert resp.json()["connected"] is False


class TestWorkbenchConfig:
    @pytest.mark.asyncio
    async def test_read_config(self, client: AsyncClient, app):
        wm = app.state.workbench
        with patch.object(wm, "read_config", return_value=_mock_radio_config()):
            resp = await client.get("/api/v1/workbench/config")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["sections"]) == 2
        assert data["config_hash"] == "a" * 64

    @pytest.mark.asyncio
    async def test_read_config_not_connected(self, client: AsyncClient, app):
        wm = app.state.workbench
        with patch.object(wm, "read_config", side_effect=RuntimeError("Not connected")):
            resp = await client.get("/api/v1/workbench/config")
        assert resp.status_code == 200
        assert "error" in resp.json()


class TestWorkbenchDiff:
    @pytest.mark.asyncio
    async def test_diff(self, client: AsyncClient, app):
        wm = app.state.workbench
        diff = ConfigDiff(
            changes=[
                ConfigDiffEntry(
                    section="device",
                    field="role",
                    current_value=4,
                    proposed_value=7,
                ),
            ],
            change_count=1,
        )
        with patch.object(wm, "compute_diff", return_value=diff):
            resp = await client.post(
                "/api/v1/workbench/diff",
                json={"sections": [{"name": "device", "fields": {"role": 7}}]},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["change_count"] == 1
        assert data["changes"][0]["field"] == "role"


class TestWorkbenchApply:
    @pytest.mark.asyncio
    async def test_apply(self, client: AsyncClient, app):
        wm = app.state.workbench
        result = ApplyResult(
            success=True,
            applied_sections=["device"],
            readback_matches=True,
            config_hash="b" * 64,
        )
        with patch.object(wm, "apply_config", return_value=result):
            resp = await client.post(
                "/api/v1/workbench/apply",
                json={"sections": [{"name": "device", "fields": {"role": 7}}]},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "device" in data["applied_sections"]


class TestWorkbenchSaveTemplate:
    @pytest.mark.asyncio
    async def test_save_template(self, client: AsyncClient, app):
        wm = app.state.workbench
        result = SaveTemplateResult(
            success=True,
            template_name="my-template",
            config_hash="c" * 64,
            yaml_path="/configs/my-template.yaml",
        )
        with patch.object(wm, "save_as_template", return_value=result):
            resp = await client.post(
                "/api/v1/workbench/save-template",
                json={
                    "template_name": "my-template",
                    "description": "Test template",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["template_name"] == "my-template"


# ── Bulk Push endpoints ──────────────────────────────────────────────


class TestBulkPush:
    @pytest.mark.asyncio
    async def test_push_dry_run(self, client: AsyncClient, app):
        bpm = app.state.bulk_push
        progress = BulkPushProgress(
            push_id="test123",
            template_name="relay-node",
            total=2,
            skipped=2,
            is_complete=True,
        )
        with patch.object(bpm, "start_push", return_value=progress):
            resp = await client.post(
                "/api/v1/config/push",
                json={
                    "template_name": "relay-node",
                    "device_ids": ["!aaa11111", "!bbb22222"],
                    "dry_run": True,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["push_id"] == "test123"
        assert data["is_complete"] is True
        assert data["skipped"] == 2

    @pytest.mark.asyncio
    async def test_push_invalid_template(self, client: AsyncClient, app):
        bpm = app.state.bulk_push
        with patch.object(
            bpm,
            "start_push",
            side_effect=ValueError("Template 'bad' not found"),
        ):
            resp = await client.post(
                "/api/v1/config/push",
                json={
                    "template_name": "bad",
                    "device_ids": ["!aaa11111"],
                },
            )
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_push_progress(self, client: AsyncClient, app):
        bpm = app.state.bulk_push
        progress = BulkPushProgress(
            push_id="prog123",
            template_name="relay-node",
            total=3,
            success=2,
            pushing=1,
        )
        with patch.object(bpm, "get_progress", return_value=progress):
            resp = await client.get("/api/v1/config/push/prog123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["push_id"] == "prog123"
        assert data["success"] == 2

    @pytest.mark.asyncio
    async def test_push_progress_not_found(self, client: AsyncClient, app):
        bpm = app.state.bulk_push
        with patch.object(bpm, "get_progress", return_value=None):
            resp = await client.get("/api/v1/config/push/nonexistent")
        assert resp.status_code == 200
        assert "error" in resp.json()
