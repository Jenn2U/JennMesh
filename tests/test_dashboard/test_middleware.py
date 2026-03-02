"""Tests for production middleware — security headers, rate limiting, CORS, request logging."""

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


# ── Security Headers ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_security_headers_on_api(client: AsyncClient):
    """API responses should include standard security headers."""
    resp = await client.get("/api/v1/fleet")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("x-xss-protection") == "1; mode=block"
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


@pytest.mark.asyncio
async def test_security_headers_on_health(client: AsyncClient):
    """Health endpoint also gets security headers."""
    resp = await client.get("/health")
    assert resp.headers.get("x-content-type-options") == "nosniff"


@pytest.mark.asyncio
async def test_no_cache_still_present(client: AsyncClient):
    """API routes should still include Cache-Control: no-store."""
    resp = await client.get("/api/v1/fleet")
    assert "no-store" in resp.headers.get("cache-control", "")


# ── Rate Limiting ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limit_not_triggered_under_threshold(client: AsyncClient):
    """Normal traffic should not be rate-limited."""
    for _ in range(10):
        resp = await client.get("/api/v1/fleet")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_skips_health(populated_db: MeshDatabase):
    """Health endpoint should never be rate-limited (monitoring probes)."""
    app = create_app(db=populated_db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Make many health requests
        for _ in range(150):
            resp = await client.get("/health")
            assert resp.status_code == 200


# ── CORS ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cors_allows_localhost(client: AsyncClient):
    """CORS should allow localhost:8002 origin."""
    resp = await client.options(
        "/api/v1/fleet",
        headers={
            "Origin": "http://localhost:8002",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:8002"


@pytest.mark.asyncio
async def test_cors_allows_lan_origin(client: AsyncClient):
    """CORS should allow private LAN origins (10.x.x.x)."""
    resp = await client.options(
        "/api/v1/fleet",
        headers={
            "Origin": "http://10.10.50.15:8002",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "10.10.50.15" in resp.headers.get("access-control-allow-origin", "")


@pytest.mark.asyncio
async def test_cors_allows_mesh_domain(client: AsyncClient):
    """CORS should allow mesh.jenn2u.ai."""
    resp = await client.options(
        "/api/v1/fleet",
        headers={
            "Origin": "https://mesh.jenn2u.ai",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.headers.get("access-control-allow-origin") == "https://mesh.jenn2u.ai"


@pytest.mark.asyncio
async def test_cors_blocks_unknown_origin(client: AsyncClient):
    """CORS should block unknown external origins."""
    resp = await client.options(
        "/api/v1/fleet",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "evil.example.com" not in resp.headers.get("access-control-allow-origin", "")
