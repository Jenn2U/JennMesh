"""Tests for coverage mapping API routes."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from jenn_mesh.dashboard.app import create_app
from jenn_mesh.db import MeshDatabase


@pytest.fixture
def app(populated_db: MeshDatabase):
    return create_app(db=populated_db)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── Helpers ─────────────────────────────────────────────────────────


def _seed_coverage(db: MeshDatabase, count: int = 10):
    """Seed coverage samples around Austin, TX."""
    base_lat = 30.2672
    base_lon = -97.7431
    for i in range(count):
        db.add_coverage_sample(
            from_node="!aaa11111",
            to_node="!bbb22222",
            latitude=base_lat + (i * 0.001),
            longitude=base_lon + (i * 0.001),
            rssi=-80.0 - i,
            snr=10.0 - (i * 0.5),
        )


# ── Heatmap Endpoint ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_heatmap_empty(client: AsyncClient):
    """Heatmap with no data returns zero cells."""
    resp = await client.get("/api/v1/coverage/heatmap")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_samples"] == 0
    assert data["cell_count"] == 0
    assert data["cells"] == []


@pytest.mark.asyncio
async def test_heatmap_with_data(populated_db: MeshDatabase):
    """Heatmap with seeded data returns cells."""
    _seed_coverage(populated_db, 5)
    app = create_app(db=populated_db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(
            "/api/v1/coverage/heatmap",
            params={
                "min_lat": 30.0,
                "max_lat": 31.0,
                "min_lon": -98.0,
                "max_lon": -97.0,
            },
        )
        data = resp.json()
        assert data["total_samples"] == 5
        assert data["cell_count"] > 0
        cell = data["cells"][0]
        assert "lat" in cell
        assert "lon" in cell
        assert "avg_rssi" in cell
        assert "sample_count" in cell


@pytest.mark.asyncio
async def test_heatmap_bounds(client: AsyncClient):
    """Heatmap response includes bounds."""
    resp = await client.get(
        "/api/v1/coverage/heatmap",
        params={"min_lat": 30.0, "max_lat": 31.0, "min_lon": -98.0, "max_lon": -97.0},
    )
    data = resp.json()
    assert "bounds" in data
    assert data["bounds"]["min_lat"] == 30.0
    assert data["bounds"]["max_lon"] == -97.0


# ── Dead Zones Endpoint ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dead_zones_empty(client: AsyncClient):
    """No data → no dead zones."""
    resp = await client.get("/api/v1/coverage/dead-zones")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["dead_zones"] == []


@pytest.mark.asyncio
async def test_dead_zones_with_poor_signal(populated_db: MeshDatabase):
    """Very poor RSSI samples should appear as dead zones."""
    for i in range(5):
        populated_db.add_coverage_sample(
            "!aaa11111",
            "!bbb22222",
            31.0 + (i * 0.0001),
            -98.0 + (i * 0.0001),
            rssi=-115.0 - i,
        )
    app = create_app(db=populated_db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/coverage/dead-zones", params={"min_rssi": -110})
        data = resp.json()
        assert data["count"] > 0


# ── Stats Endpoint ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stats_empty(client: AsyncClient):
    """No data → zeroed stats."""
    resp = await client.get("/api/v1/coverage/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_samples"] == 0


@pytest.mark.asyncio
async def test_stats_with_data(populated_db: MeshDatabase):
    """Stats with seeded data."""
    _seed_coverage(populated_db, 5)
    app = create_app(db=populated_db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/coverage/stats")
        data = resp.json()
        assert data["total_samples"] == 5
        assert data["avg_rssi"] is not None
        assert data["min_rssi"] <= data["avg_rssi"] <= data["max_rssi"]


# ── Export Endpoint ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_geojson_empty(client: AsyncClient):
    """Export with no data returns empty FeatureCollection."""
    resp = await client.get("/api/v1/coverage/export")
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 0


@pytest.mark.asyncio
async def test_export_geojson_with_data(populated_db: MeshDatabase):
    """Export returns valid GeoJSON features."""
    _seed_coverage(populated_db, 3)
    app = create_app(db=populated_db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(
            "/api/v1/coverage/export",
            params={
                "min_lat": 30.0,
                "max_lat": 31.0,
                "min_lon": -98.0,
                "max_lon": -97.0,
            },
        )
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) > 0
        feature = data["features"][0]
        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] == "Point"
