"""Tests for encryption audit API endpoints."""

from __future__ import annotations

import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

from jenn_mesh.dashboard.app import create_app
from jenn_mesh.db import MeshDatabase


@pytest.fixture
def db() -> MeshDatabase:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = MeshDatabase(db_path=tmp.name)
    db.upsert_device("!enc1", long_name="Node1", role="ROUTER")
    db.upsert_device("!enc2", long_name="Node2", role="CLIENT")
    return db


@pytest.fixture
def client(db: MeshDatabase) -> AsyncClient:
    app = create_app(db=db)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _add_channel(db, index, name, psk):
    with db.connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO channels (channel_index, name, psk) VALUES (?, ?, ?)",
            (index, name, psk),
        )


class TestEncryptionAuditEndpoint:
    @pytest.mark.asyncio
    async def test_fleet_audit(self, client, db):
        _add_channel(db, 0, "Primary", "0x" + "ab" * 32)
        resp = await client.get("/api/v1/encryption/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert "fleet_score" in data
        assert "devices" in data
        assert data["total_devices"] == 2

    @pytest.mark.asyncio
    async def test_fleet_audit_no_channels(self, client):
        resp = await client.get("/api/v1/encryption/audit")
        assert resp.status_code == 200
        data = resp.json()
        # All devices unknown → score 100 (no weak)
        assert data["unknown_count"] == 2

    @pytest.mark.asyncio
    async def test_device_audit(self, client, db):
        _add_channel(db, 0, "Primary", "AQ==")
        resp = await client.get("/api/v1/encryption/audit/!enc1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_id"] == "!enc1"
        assert data["encryption_status"] == "unencrypted"

    @pytest.mark.asyncio
    async def test_encryption_score(self, client, db):
        _add_channel(db, 0, "Primary", "0x" + "ab" * 32)
        resp = await client.get("/api/v1/encryption/score")
        assert resp.status_code == 200
        data = resp.json()
        assert "fleet_score" in data
        assert data["fleet_score"] == 100.0
