"""Tests for partition detection API endpoints."""

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
    db = MeshDatabase(db_path=tmp.name)
    # Need at least some devices for topology checks
    db.upsert_device("!p001", long_name="R1", role="ROUTER")
    db.upsert_device("!p002", long_name="R2", role="ROUTER")
    return db


@pytest.fixture
def client(db: MeshDatabase) -> AsyncClient:
    app = create_app(db=db)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestPartitionStatus:
    @pytest.mark.asyncio
    async def test_partition_status(self, client):
        resp = await client.get("/api/v1/partitions/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "is_partitioned" in data or "component_count" in data


class TestPartitionEvents:
    @pytest.mark.asyncio
    async def test_list_events_empty(self, client):
        resp = await client.get("/api/v1/partitions/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["events"] == []

    @pytest.mark.asyncio
    async def test_list_events_with_data(self, client, db):
        db.create_partition_event(
            event_type="partition_detected",
            component_count=3,
            components_json=json.dumps([["!p001"], ["!p002"], ["!p003"]]),
        )
        resp = await client.get("/api/v1/partitions/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["events"][0]["component_count"] == 3
        # JSON should be parsed
        assert "components" in data["events"][0]

    @pytest.mark.asyncio
    async def test_list_events_with_type_filter(self, client, db):
        db.create_partition_event(
            event_type="partition_detected", component_count=2
        )
        db.create_partition_event(
            event_type="partition_resolved", component_count=1
        )
        resp = await client.get(
            "/api/v1/partitions/events?event_type=partition_detected"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["events"][0]["event_type"] == "partition_detected"

    @pytest.mark.asyncio
    async def test_get_event_by_id(self, client, db):
        ev_id = db.create_partition_event(
            event_type="partition_detected",
            component_count=2,
            components_json=json.dumps([["!p001"], ["!p002"]]),
        )
        resp = await client.get(f"/api/v1/partitions/events/{ev_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["event_type"] == "partition_detected"
        assert "components" in data

    @pytest.mark.asyncio
    async def test_get_event_not_found(self, client):
        resp = await client.get("/api/v1/partitions/events/9999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_event_with_relay_recommendation(self, client, db):
        ev_id = db.create_partition_event(
            event_type="partition_detected",
            component_count=2,
            components_json=json.dumps([["!p001"], ["!p002"]]),
            relay_recommendation="Place relay near (37.7749, -122.4194)",
        )
        resp = await client.get(f"/api/v1/partitions/events/{ev_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "relay" in data["relay_recommendation"].lower()
