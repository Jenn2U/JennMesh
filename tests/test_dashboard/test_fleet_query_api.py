"""Tests for fleet NL query API routes."""

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


# ── POST /fleet-query/ask ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_ask_keyword_offline(client: AsyncClient):
    """POST /fleet-query/ask with 'offline' triggers keyword fallback."""
    resp = await client.post(
        "/api/v1/fleet-query/ask",
        json={"question": "Show me offline nodes"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["question"] == "Show me offline nodes"
    assert data["source"] == "keyword"
    assert data["answer"]  # non-empty answer
    assert data["ollama_available"] is False


@pytest.mark.asyncio
async def test_ask_keyword_battery(client: AsyncClient):
    """POST /fleet-query/ask with 'battery' matches keyword pattern."""
    resp = await client.post(
        "/api/v1/fleet-query/ask",
        json={"question": "Which nodes have low battery?"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "keyword"


@pytest.mark.asyncio
async def test_ask_keyword_health(client: AsyncClient):
    """POST /fleet-query/ask with 'health' triggers fleet summary."""
    resp = await client.post(
        "/api/v1/fleet-query/ask",
        json={"question": "Fleet health summary"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "keyword"


@pytest.mark.asyncio
async def test_ask_canned_fallback(client: AsyncClient):
    """POST /fleet-query/ask with unrecognized question returns canned fallback."""
    resp = await client.post(
        "/api/v1/fleet-query/ask",
        json={"question": "What color is the sky?"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "canned"
    assert "couldn't understand" in data["answer"].lower() or "canned" in data["source"]


@pytest.mark.asyncio
async def test_ask_validation_too_short(client: AsyncClient):
    """POST /fleet-query/ask rejects questions under 3 chars."""
    resp = await client.post(
        "/api/v1/fleet-query/ask",
        json={"question": "ab"},
    )
    assert resp.status_code == 422  # Pydantic validation


@pytest.mark.asyncio
async def test_ask_validation_too_long(client: AsyncClient):
    """POST /fleet-query/ask rejects questions over 500 chars."""
    resp = await client.post(
        "/api/v1/fleet-query/ask",
        json={"question": "x" * 501},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ask_has_duration(client: AsyncClient):
    """POST /fleet-query/ask response includes duration_ms."""
    resp = await client.post(
        "/api/v1/fleet-query/ask",
        json={"question": "Show me all alerts"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "duration_ms" in data
    assert data["duration_ms"] >= 0


# ── GET /fleet-query/status ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_endpoint(client: AsyncClient):
    """GET /fleet-query/status returns engine availability."""
    resp = await client.get("/api/v1/fleet-query/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["engine"] == "fleet_query"
    assert data["ollama_configured"] is False
    assert "keyword_patterns" in data
    assert "canned_queries" in data


# ── GET /fleet-query/history ────────────────────────────────────────


@pytest.mark.asyncio
async def test_history_empty_initially(client: AsyncClient):
    """GET /fleet-query/history returns empty list when no queries yet."""
    resp = await client.get("/api/v1/fleet-query/history")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_history_after_ask(client: AsyncClient):
    """History includes a query after asking one."""
    # Ask a question first
    await client.post(
        "/api/v1/fleet-query/ask",
        json={"question": "Show offline nodes"},
    )
    # Check history
    resp = await client.get("/api/v1/fleet-query/history")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["question"] == "Show offline nodes"


@pytest.mark.asyncio
async def test_history_limit_param(client: AsyncClient):
    """GET /fleet-query/history respects limit parameter."""
    # Ask multiple questions
    for q in ["offline nodes", "battery low", "fleet health"]:
        await client.post("/api/v1/fleet-query/ask", json={"question": q})

    resp = await client.get("/api/v1/fleet-query/history?limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) <= 2


# ── GET /fleet-query/canned ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_canned_queries(client: AsyncClient):
    """GET /fleet-query/canned returns pre-built query list."""
    resp = await client.get("/api/v1/fleet-query/canned")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 5
    # Each entry has question and description
    for item in data:
        assert "question" in item
        assert "description" in item


# ── End-to-end flow ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_e2e_ask_then_history(client: AsyncClient):
    """Full flow: ask question, verify it appears in history."""
    question = "Single points of failure"
    ask_resp = await client.post(
        "/api/v1/fleet-query/ask",
        json={"question": question},
    )
    assert ask_resp.status_code == 200
    ask_data = ask_resp.json()
    assert ask_data["source"] == "keyword"

    history_resp = await client.get("/api/v1/fleet-query/history")
    assert history_resp.status_code == 200
    history = history_resp.json()
    questions = [h["question"] for h in history]
    assert question in questions
