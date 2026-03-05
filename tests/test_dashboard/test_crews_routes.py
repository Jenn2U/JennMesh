"""Tests for CrewAI crew execution API routes."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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


# ── Status endpoint ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crews_status_disabled(client: AsyncClient):
    """Status returns enabled=false when CREWAI_ENABLED is off."""
    resp = await client.get("/api/v1/crews/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["crews"] == []


@pytest.mark.asyncio
async def test_crews_status_enabled(client: AsyncClient):
    """Status returns crew list when enabled."""
    with patch("jenn_mesh.dashboard.routes.crews.CREWAI_ENABLED", True):
        resp = await client.get("/api/v1/crews/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert len(data["crews"]) == 4


# ── Disabled state → 503 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fleet_health_disabled(client: AsyncClient):
    resp = await client.post("/api/v1/crews/fleet-health")
    assert resp.status_code == 503
    assert "not enabled" in resp.json()["error"]


@pytest.mark.asyncio
async def test_incident_disabled(client: AsyncClient):
    resp = await client.post("/api/v1/crews/incident/!abc11111")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_provisioning_disabled(client: AsyncClient):
    resp = await client.post(
        "/api/v1/crews/provisioning",
        json={"terrain": "urban", "num_nodes": 3, "power_source": "battery"},
    )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_query_disabled(client: AsyncClient):
    resp = await client.post(
        "/api/v1/crews/query",
        json={"question": "how many nodes?"},
    )
    assert resp.status_code == 503


# ── Crew unavailable → 503 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_fleet_health_crew_unavailable(client: AsyncClient):
    """Enabled but get_crew returns None → 503."""
    with patch("jenn_mesh.dashboard.routes.crews.CREWAI_ENABLED", True):
        with patch("jenn_mesh.crews.get_crew", return_value=None):
            resp = await client.post("/api/v1/crews/fleet-health")
            assert resp.status_code == 503
            assert "unavailable" in resp.json()["error"]


# ── Successful crew execution ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_fleet_health_success(client: AsyncClient):
    """Crew kickoff returns result on success."""
    mock_crew = MagicMock()
    mock_crew.kickoff.return_value = "Fleet looks healthy"

    with patch("jenn_mesh.dashboard.routes.crews.CREWAI_ENABLED", True):
        with patch("jenn_mesh.crews.get_crew", return_value=mock_crew):
            resp = await client.post("/api/v1/crews/fleet-health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["crew"] == "fleet_health"
            assert data["status"] == "completed"
            assert "Fleet looks healthy" in data["result"]


@pytest.mark.asyncio
async def test_query_success(client: AsyncClient):
    mock_crew = MagicMock()
    mock_crew.kickoff.return_value = "3 nodes are online"

    with patch("jenn_mesh.dashboard.routes.crews.CREWAI_ENABLED", True):
        with patch("jenn_mesh.crews.get_crew", return_value=mock_crew):
            resp = await client.post(
                "/api/v1/crews/query",
                json={"question": "how many nodes?"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["question"] == "how many nodes?"
            assert data["status"] == "completed"


# ── Crew execution failure → 500 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_fleet_health_execution_error(client: AsyncClient):
    """Crew kickoff raises → 500 with error detail."""
    mock_crew = MagicMock()
    mock_crew.kickoff.side_effect = RuntimeError("LLM timeout")

    with patch("jenn_mesh.dashboard.routes.crews.CREWAI_ENABLED", True):
        with patch("jenn_mesh.crews.get_crew", return_value=mock_crew):
            resp = await client.post("/api/v1/crews/fleet-health")
            assert resp.status_code == 500
            assert "RuntimeError" in resp.json()["error"]
            assert "LLM timeout" in resp.json()["error"]


# ── Input validation ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_missing_question(client: AsyncClient):
    """POST /crews/query without question → 422."""
    with patch("jenn_mesh.dashboard.routes.crews.CREWAI_ENABLED", True):
        resp = await client.post("/api/v1/crews/query", json={})
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_provisioning_invalid_num_nodes(client: AsyncClient):
    """num_nodes > 100 → 422."""
    with patch("jenn_mesh.dashboard.routes.crews.CREWAI_ENABLED", True):
        resp = await client.post(
            "/api/v1/crews/provisioning",
            json={"terrain": "urban", "num_nodes": 999, "power_source": "battery"},
        )
        assert resp.status_code == 422
