"""Tests for TAK gateway API endpoints."""

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


# ── Status ──────────────────────────────────────────────────────────


class TestTakStatusAPI:
    @pytest.mark.asyncio
    async def test_initial_status(self, client):
        resp = await client.get("/api/v1/tak/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["gateway"]["connection_status"] == "disconnected"
        assert data["gateway"]["events_sent"] == 0

    @pytest.mark.asyncio
    async def test_status_after_translation(self, client):
        await client.post(
            "/api/v1/tak/translate",
            json={"node_id": "!abc", "latitude": 30.0, "longitude": -97.0},
        )
        resp = await client.get("/api/v1/tak/status")
        data = resp.json()
        assert data["gateway"]["events_sent"] >= 1


# ── Configuration ───────────────────────────────────────────────────


class TestTakConfigAPI:
    @pytest.mark.asyncio
    async def test_no_config(self, client):
        resp = await client.get("/api/v1/tak/config")
        assert resp.status_code == 200
        assert resp.json()["config"] is None

    @pytest.mark.asyncio
    async def test_set_config(self, client):
        resp = await client.post(
            "/api/v1/tak/config",
            json={"host": "tak.example.com", "port": 8087},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["config"]["host"] == "tak.example.com"
        assert data["config"]["port"] == 8087
        assert data["config"]["callsign_prefix"] == "JENN-"

    @pytest.mark.asyncio
    async def test_config_persists(self, client):
        await client.post(
            "/api/v1/tak/config",
            json={"host": "tak.local", "port": 9999, "use_tls": True},
        )
        resp = await client.get("/api/v1/tak/config")
        cfg = resp.json()["config"]
        assert cfg["host"] == "tak.local"
        assert cfg["port"] == 9999
        assert cfg["use_tls"] is True


# ── Translate position ──────────────────────────────────────────────


class TestTranslateAPI:
    @pytest.mark.asyncio
    async def test_basic_translation(self, client):
        resp = await client.post(
            "/api/v1/tak/translate",
            json={
                "node_id": "!2a3b4c5d",
                "latitude": 32.123,
                "longitude": -96.789,
                "altitude": 150.0,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["event"]["uid"].startswith("JENN-MESH-")
        assert data["event"]["latitude"] == 32.123
        assert "xml" in data
        assert "<event" in data["xml"]

    @pytest.mark.asyncio
    async def test_translation_with_battery(self, client):
        resp = await client.post(
            "/api/v1/tak/translate",
            json={
                "node_id": "!abc",
                "latitude": 30.0,
                "longitude": -97.0,
                "battery": 80,
            },
        )
        data = resp.json()
        assert "battery" in data["xml"].lower() or "80" in data["xml"]

    @pytest.mark.asyncio
    async def test_translation_with_custom_type(self, client):
        resp = await client.post(
            "/api/v1/tak/translate",
            json={
                "node_id": "!abc",
                "latitude": 30.0,
                "longitude": -97.0,
                "cot_type": "a-f-G-U-C-I",
            },
        )
        assert resp.json()["event"]["cot_type"] == "a-f-G-U-C-I"


# ── Events ──────────────────────────────────────────────────────────


class TestTakEventsAPI:
    @pytest.mark.asyncio
    async def test_empty_events(self, client):
        resp = await client.get("/api/v1/tak/events")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    @pytest.mark.asyncio
    async def test_events_after_translation(self, client):
        await client.post(
            "/api/v1/tak/translate",
            json={"node_id": "!abc", "latitude": 30.0, "longitude": -97.0},
        )
        resp = await client.get("/api/v1/tak/events")
        assert resp.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_events_filter_by_direction(self, client):
        await client.post(
            "/api/v1/tak/translate",
            json={"node_id": "!abc", "latitude": 30.0, "longitude": -97.0},
        )
        out = await client.get("/api/v1/tak/events?direction=outbound")
        assert out.json()["count"] == 1
        inb = await client.get("/api/v1/tak/events?direction=inbound")
        assert inb.json()["count"] == 0


# ── Parse CoT XML ───────────────────────────────────────────────────


class TestParseAPI:
    @pytest.mark.asyncio
    async def test_parse_valid_xml(self, client):
        xml = (
            '<event version="2.0" uid="test-1" type="a-f-G" '
            'time="2025-01-01T00:00:00Z" start="2025-01-01T00:00:00Z" '
            'stale="2025-01-01T00:10:00Z" how="m-g">'
            '<point lat="30.0" lon="-97.0" hae="100.0" ce="50" le="50"/>'
            '<detail><contact callsign="TEST-1"/></detail>'
            "</event>"
        )
        resp = await client.post(
            "/api/v1/tak/parse",
            content=xml.encode("utf-8"),
            headers={"content-type": "application/xml"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["event"]["uid"] == "test-1"
        assert data["event"]["callsign"] == "TEST-1"
        assert data["event"]["latitude"] == 30.0

    @pytest.mark.asyncio
    async def test_parse_empty_body(self, client):
        resp = await client.post(
            "/api/v1/tak/parse",
            content=b"",
            headers={"content-type": "application/xml"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_parse_invalid_xml(self, client):
        resp = await client.post(
            "/api/v1/tak/parse",
            content=b"not xml at all",
            headers={"content-type": "application/xml"},
        )
        assert resp.status_code == 400
