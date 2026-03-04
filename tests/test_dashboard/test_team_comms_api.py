"""Tests for team communication API endpoints."""

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


# ── Send message ────────────────────────────────────────────────────


class TestSendMessageAPI:
    @pytest.mark.asyncio
    async def test_send_broadcast(self, client):
        resp = await client.post(
            "/api/v1/team-comms/send",
            json={
                "channel": "broadcast",
                "sender": "op1",
                "message": "Rally at CP2",
                "confirmed": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["message_id"] is not None
        assert data["wire_format"] == "[TEAM:BROADCAST] Rally at CP2"

    @pytest.mark.asyncio
    async def test_send_direct(self, client):
        resp = await client.post(
            "/api/v1/team-comms/send",
            json={
                "channel": "direct",
                "sender": "op1",
                "message": "Report in",
                "recipient": "!abc123",
                "confirmed": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "@!abc123" in data["wire_format"]

    @pytest.mark.asyncio
    async def test_send_requires_confirmation(self, client):
        resp = await client.post(
            "/api/v1/team-comms/send",
            json={"message": "Hello", "confirmed": False},
        )
        assert resp.status_code == 400
        assert "confirmation" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_send_empty_message(self, client):
        resp = await client.post(
            "/api/v1/team-comms/send",
            json={"message": "", "confirmed": True},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_send_invalid_channel(self, client):
        resp = await client.post(
            "/api/v1/team-comms/send",
            json={"channel": "invalid", "message": "Hi", "confirmed": True},
        )
        assert resp.status_code == 400


# ── List / get messages ─────────────────────────────────────────────


class TestListMessagesAPI:
    @pytest.mark.asyncio
    async def test_list_empty(self, client):
        resp = await client.get("/api/v1/team-comms/messages")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    @pytest.mark.asyncio
    async def test_list_after_send(self, client):
        await client.post(
            "/api/v1/team-comms/send",
            json={"message": "A", "confirmed": True},
        )
        await client.post(
            "/api/v1/team-comms/send",
            json={"channel": "team", "message": "B", "confirmed": True},
        )
        resp = await client.get("/api/v1/team-comms/messages")
        assert resp.json()["count"] == 2

    @pytest.mark.asyncio
    async def test_list_by_channel(self, client):
        await client.post(
            "/api/v1/team-comms/send",
            json={"channel": "broadcast", "message": "A", "confirmed": True},
        )
        await client.post(
            "/api/v1/team-comms/send",
            json={"channel": "team", "message": "B", "confirmed": True},
        )
        resp = await client.get("/api/v1/team-comms/messages?channel=team")
        assert resp.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_get_message(self, client):
        send = await client.post(
            "/api/v1/team-comms/send",
            json={"message": "Hello", "confirmed": True},
        )
        msg_id = send.json()["message_id"]
        resp = await client.get(f"/api/v1/team-comms/messages/{msg_id}")
        assert resp.status_code == 200
        assert resp.json()["message"]["message"] == "Hello"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, client):
        resp = await client.get("/api/v1/team-comms/messages/9999")
        assert resp.status_code == 404


# ── Delivery lifecycle ──────────────────────────────────────────────


class TestDeliveryAPI:
    @pytest.mark.asyncio
    async def test_mark_sent(self, client):
        send = await client.post(
            "/api/v1/team-comms/send",
            json={"message": "Test", "confirmed": True},
        )
        msg_id = send.json()["message_id"]
        resp = await client.post(
            f"/api/v1/team-comms/messages/{msg_id}/mark-sent"
        )
        assert resp.status_code == 200
        assert resp.json()["delivery_status"] == "sent"

    @pytest.mark.asyncio
    async def test_mark_delivered(self, client):
        send = await client.post(
            "/api/v1/team-comms/send",
            json={"message": "Test", "confirmed": True},
        )
        msg_id = send.json()["message_id"]
        resp = await client.post(
            f"/api/v1/team-comms/messages/{msg_id}/mark-delivered"
        )
        assert resp.status_code == 200
        assert resp.json()["delivery_status"] == "delivered"

    @pytest.mark.asyncio
    async def test_mark_nonexistent(self, client):
        resp = await client.post(
            "/api/v1/team-comms/messages/9999/mark-sent"
        )
        assert resp.status_code == 404
