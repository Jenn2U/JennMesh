"""Tests for crew creation and the public crews API."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# ── Helpers: fake crewai module for import-time safety ────────────────


def _make_fake_crewai() -> ModuleType:
    """Create a fake crewai module so tests run without crewai installed."""
    mod = ModuleType("crewai")
    mod.Agent = MagicMock
    mod.Task = MagicMock
    mod.Crew = MagicMock(return_value=MagicMock())
    mod.Process = MagicMock()
    mod.Process.sequential = "sequential"
    return mod


@pytest.fixture(autouse=True)
def _inject_fake_crewai():
    """Inject fake crewai into sys.modules for all tests in this file."""
    fake = _make_fake_crewai()
    with patch.dict(sys.modules, {"crewai": fake}):
        yield fake


# ── Crew creation via public API ──────────────────────────────────────


@pytest.fixture
def _enable_crewai():
    with patch("jenn_mesh.crews.CREWAI_ENABLED", True):
        with patch("jenn_mesh.crews.config.CREWAI_ENABLED", True):
            yield


@pytest.fixture
def mock_tools():
    return {
        "analyze_node": MagicMock(),
        "analyze_fleet": MagicMock(),
        "summarize_alerts": MagicMock(),
        "summarize_node_alerts": MagicMock(),
        "query_fleet": MagicMock(),
        "reason_lost_node": MagicMock(),
        "recommend_provisioning": MagicMock(),
    }


def test_get_crew_returns_none_when_disabled():
    """get_crew() returns None when CREWAI_ENABLED=False."""
    with patch("jenn_mesh.crews.CREWAI_ENABLED", False):
        from jenn_mesh.crews import get_crew

        result = get_crew("fleet_health")
        assert result is None


def test_get_crew_unknown_name(_enable_crewai, mock_tools):
    """get_crew() returns None for unknown crew name."""
    with patch("jenn_mesh.crews.tools.ALL_TOOLS", mock_tools):
        from jenn_mesh.crews import get_crew

        result = get_crew("nonexistent")
        assert result is None


def test_available_crews_returns_four():
    """available_crews() lists all 4 crew types."""
    from jenn_mesh.crews import available_crews

    crews = available_crews()
    assert len(crews) == 4
    names = {c["name"] for c in crews}
    assert names == {"fleet_health", "incident_response", "provisioning", "fleet_query"}


# ── Individual crew creation ──────────────────────────────────────────


def test_create_fleet_health_crew(mock_tools):
    from jenn_mesh.crews.fleet_health import create_fleet_health_crew

    crew = create_fleet_health_crew(mock_tools)
    assert crew is not None


def test_create_incident_response_crew(mock_tools):
    from jenn_mesh.crews.incident_response import create_incident_response_crew

    crew = create_incident_response_crew(mock_tools, "!abc11111")
    assert crew is not None


def test_create_provisioning_crew(mock_tools):
    from jenn_mesh.crews.provisioning import create_provisioning_crew

    ctx = {"terrain": "urban", "num_nodes": 3, "power_source": "battery"}
    crew = create_provisioning_crew(mock_tools, ctx)
    assert crew is not None


def test_create_fleet_query_crew(mock_tools):
    from jenn_mesh.crews.fleet_query import create_fleet_query_crew

    crew = create_fleet_query_crew(mock_tools, "how many nodes?")
    assert crew is not None


def test_incident_response_requires_node_id(_enable_crewai, mock_tools):
    """get_crew('incident_response') without node_id → None."""
    with patch("jenn_mesh.crews.tools.ALL_TOOLS", mock_tools):
        from jenn_mesh.crews import get_crew

        result = get_crew("incident_response")
        assert result is None


def test_fleet_query_requires_question(_enable_crewai, mock_tools):
    """get_crew('fleet_query') without question → None."""
    with patch("jenn_mesh.crews.tools.ALL_TOOLS", mock_tools):
        from jenn_mesh.crews import get_crew

        result = get_crew("fleet_query")
        assert result is None


# ── Graceful degradation when crewai not installed ─────────────────────


def test_crew_creation_returns_none_without_crewai():
    """Crew creators return None when crewai is not importable."""
    with patch.dict(sys.modules, {"crewai": None}):
        from jenn_mesh.crews.fleet_health import create_fleet_health_crew  # noqa: F401

        # Force re-import to pick up the None crewai module
        import importlib

        import jenn_mesh.crews.fleet_health as fh_mod

        importlib.reload(fh_mod)
        result = fh_mod.create_fleet_health_crew({})
        assert result is None
