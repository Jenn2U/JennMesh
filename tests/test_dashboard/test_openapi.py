"""Tests for OpenAPI spec, versioning, and documentation endpoints."""

from __future__ import annotations

import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

from jenn_mesh.dashboard.app import OPENAPI_TAGS, create_app
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


class TestOpenAPISpec:
    @pytest.mark.asyncio
    async def test_openapi_json_available(self, client):
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        spec = resp.json()
        assert "openapi" in spec
        assert "paths" in spec
        assert "info" in spec

    @pytest.mark.asyncio
    async def test_openapi_info_metadata(self, client):
        resp = await client.get("/openapi.json")
        spec = resp.json()
        info = spec["info"]
        assert "JennMesh" in info["title"]
        assert "version" in info

    @pytest.mark.asyncio
    async def test_openapi_has_tags(self, client):
        resp = await client.get("/openapi.json")
        spec = resp.json()
        tag_names = {t["name"] for t in spec.get("tags", [])}
        # Verify key tag groups are present
        expected = {"health", "fleet", "topology", "webhooks", "notifications", "bulk-ops"}
        assert expected.issubset(tag_names)

    @pytest.mark.asyncio
    async def test_openapi_tags_count(self, client):
        """Verify all 15 tag groups are configured."""
        resp = await client.get("/openapi.json")
        spec = resp.json()
        assert len(spec.get("tags", [])) >= 15

    def test_openapi_tags_constant(self):
        """Verify the OPENAPI_TAGS constant has expected structure."""
        assert len(OPENAPI_TAGS) == 20
        tag_names = [t["name"] for t in OPENAPI_TAGS]
        assert "health" in tag_names
        assert "webhooks" in tag_names
        assert "bulk-ops" in tag_names
        assert "team-comms" in tag_names
        assert "tak" in tag_names
        assert "asset-tracking" in tag_names
        assert "edge-associations" in tag_names
        assert "fleet-query" in tag_names
        for tag in OPENAPI_TAGS:
            assert "description" in tag

    @pytest.mark.asyncio
    async def test_api_v1_prefix_on_routes(self, client):
        resp = await client.get("/openapi.json")
        spec = resp.json()
        paths = list(spec["paths"].keys())
        api_paths = [p for p in paths if p.startswith("/api/v1/")]
        assert len(api_paths) > 20, f"Expected 20+ /api/v1/ routes, got {len(api_paths)}"


class TestDocsEndpoints:
    @pytest.mark.asyncio
    async def test_swagger_ui(self, client):
        resp = await client.get("/docs")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_redoc(self, client):
        resp = await client.get("/redoc")
        assert resp.status_code == 200
