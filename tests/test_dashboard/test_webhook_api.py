"""Tests for webhook API endpoints."""

from __future__ import annotations

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


class TestWebhookCRUD:
    @pytest.mark.asyncio
    async def test_create_webhook(self, client):
        resp = await client.post(
            "/api/v1/webhooks",
            json={
                "name": "Test Hook",
                "url": "https://example.com/hook",
                "secret": "s3cret",
                "event_types": ["alert_created"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test Hook"
        assert data["url"] == "https://example.com/hook"

    @pytest.mark.asyncio
    async def test_list_webhooks(self, client):
        await client.post(
            "/api/v1/webhooks",
            json={"name": "A", "url": "https://a.com/hook"},
        )
        await client.post(
            "/api/v1/webhooks",
            json={"name": "B", "url": "https://b.com/hook"},
        )
        resp = await client.get("/api/v1/webhooks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2

    @pytest.mark.asyncio
    async def test_get_webhook(self, client):
        create_resp = await client.post(
            "/api/v1/webhooks",
            json={"name": "Get Me", "url": "https://x.com/hook"},
        )
        wh_id = create_resp.json()["id"]
        resp = await client.get(f"/api/v1/webhooks/{wh_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Get Me"

    @pytest.mark.asyncio
    async def test_get_webhook_not_found(self, client):
        resp = await client.get("/api/v1/webhooks/9999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_webhook(self, client):
        create_resp = await client.post(
            "/api/v1/webhooks",
            json={"name": "Old", "url": "https://x.com/hook"},
        )
        wh_id = create_resp.json()["id"]
        resp = await client.put(
            f"/api/v1/webhooks/{wh_id}",
            json={"name": "New"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "New"

    @pytest.mark.asyncio
    async def test_delete_webhook(self, client):
        create_resp = await client.post(
            "/api/v1/webhooks",
            json={"name": "Del", "url": "https://x.com/hook"},
        )
        wh_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/v1/webhooks/{wh_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_delete_webhook_not_found(self, client):
        resp = await client.delete("/api/v1/webhooks/9999")
        assert resp.status_code == 404


class TestWebhookDeliveries:
    @pytest.mark.asyncio
    async def test_list_deliveries(self, client, db):
        wh_id = db.create_webhook(name="H", url="https://x.com/hook")
        db.create_webhook_delivery(wh_id, "test", '{"event":"test"}')
        resp = await client.get(f"/api/v1/webhooks/{wh_id}/deliveries")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1


class TestWebhookTestFire:
    @pytest.mark.asyncio
    async def test_test_fire_not_found(self, client):
        resp = await client.post(
            "/api/v1/webhooks/9999/test",
        )
        assert resp.status_code == 404  # Route raises 404 for missing webhook
