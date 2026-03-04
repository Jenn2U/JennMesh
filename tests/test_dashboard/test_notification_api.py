"""Tests for notification channel and rule API endpoints."""

from __future__ import annotations

import json
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

from jenn_mesh.dashboard.app import create_app
from jenn_mesh.db import MeshDatabase


@pytest.fixture
def db() -> MeshDatabase:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return MeshDatabase(db_path=tmp.name)


@pytest.fixture
def client(db: MeshDatabase) -> AsyncClient:
    app = create_app(db=db)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── Channel CRUD ──────────────────────────────────────────────────────


class TestChannelCRUD:
    @pytest.mark.asyncio
    async def test_create_channel(self, client):
        resp = await client.post(
            "/api/v1/notifications/channels",
            json={
                "name": "Ops Slack",
                "channel_type": "slack",
                "config_json": json.dumps({"webhook_url": "https://hooks.slack.com/x"}),
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Ops Slack"
        assert data["channel_type"] == "slack"

    @pytest.mark.asyncio
    async def test_list_channels(self, client):
        await client.post(
            "/api/v1/notifications/channels",
            json={"name": "A", "channel_type": "slack"},
        )
        await client.post(
            "/api/v1/notifications/channels",
            json={"name": "B", "channel_type": "email"},
        )
        resp = await client.get("/api/v1/notifications/channels")
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    @pytest.mark.asyncio
    async def test_get_channel(self, client):
        create = await client.post(
            "/api/v1/notifications/channels",
            json={"name": "Get", "channel_type": "teams"},
        )
        ch_id = create.json()["id"]
        resp = await client.get(f"/api/v1/notifications/channels/{ch_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Get"

    @pytest.mark.asyncio
    async def test_get_channel_not_found(self, client):
        resp = await client.get("/api/v1/notifications/channels/9999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_channel(self, client):
        create = await client.post(
            "/api/v1/notifications/channels",
            json={"name": "Old", "channel_type": "slack"},
        )
        ch_id = create.json()["id"]
        resp = await client.put(
            f"/api/v1/notifications/channels/{ch_id}",
            json={"name": "New"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "New"

    @pytest.mark.asyncio
    async def test_update_channel_no_fields(self, client):
        create = await client.post(
            "/api/v1/notifications/channels",
            json={"name": "X", "channel_type": "slack"},
        )
        ch_id = create.json()["id"]
        resp = await client.put(
            f"/api/v1/notifications/channels/{ch_id}",
            json={},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_delete_channel(self, client):
        create = await client.post(
            "/api/v1/notifications/channels",
            json={"name": "Del", "channel_type": "slack"},
        )
        ch_id = create.json()["id"]
        resp = await client.delete(f"/api/v1/notifications/channels/{ch_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_delete_channel_not_found(self, client):
        resp = await client.delete("/api/v1/notifications/channels/9999")
        assert resp.status_code == 404


# ── Rule CRUD ─────────────────────────────────────────────────────────


class TestRuleCRUD:
    @pytest.mark.asyncio
    async def test_create_rule(self, client):
        resp = await client.post(
            "/api/v1/notifications/rules",
            json={
                "name": "Critical Alerts",
                "alert_types": ["low_battery"],
                "severities": ["critical"],
                "channel_ids": [1],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Critical Alerts"

    @pytest.mark.asyncio
    async def test_list_rules(self, client):
        await client.post(
            "/api/v1/notifications/rules",
            json={"name": "R1", "alert_types": []},
        )
        await client.post(
            "/api/v1/notifications/rules",
            json={"name": "R2", "alert_types": ["test"]},
        )
        resp = await client.get("/api/v1/notifications/rules")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        # Verify JSON fields were parsed
        for rule in data["rules"]:
            assert isinstance(rule["alert_types"], list)

    @pytest.mark.asyncio
    async def test_update_rule(self, client):
        create = await client.post(
            "/api/v1/notifications/rules",
            json={"name": "Old", "alert_types": []},
        )
        rule_id = create.json()["id"]
        resp = await client.put(
            f"/api/v1/notifications/rules/{rule_id}",
            json={"name": "Updated", "severities": ["warning"]},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"

    @pytest.mark.asyncio
    async def test_delete_rule(self, client):
        create = await client.post(
            "/api/v1/notifications/rules",
            json={"name": "Del", "alert_types": []},
        )
        rule_id = create.json()["id"]
        resp = await client.delete(f"/api/v1/notifications/rules/{rule_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_delete_rule_not_found(self, client):
        resp = await client.delete("/api/v1/notifications/rules/9999")
        assert resp.status_code == 404


# ── Test fire ─────────────────────────────────────────────────────────


class TestNotificationTestFire:
    @pytest.mark.asyncio
    async def test_test_fire_channel_not_found(self, client):
        resp = await client.post(
            "/api/v1/notifications/test",
            json={"channel_id": 9999},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_test_fire_unknown_type(self, client, db):
        ch_id = db.create_notification_channel(name="Unknown", channel_type="unknown")
        resp = await client.post(
            "/api/v1/notifications/test",
            json={"channel_id": ch_id},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
