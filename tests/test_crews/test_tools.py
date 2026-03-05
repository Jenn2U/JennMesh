"""Tests for CrewAI tool wrappers."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jenn_mesh.crews.tools import (
    ALL_TOOLS,
    _services,
    analyze_fleet_tool,
    analyze_node_tool,
    query_fleet_tool,
    reason_lost_node_tool,
    recommend_provisioning_tool,
    set_services,
    summarize_alerts_tool,
    summarize_node_alerts_tool,
)


@pytest.fixture(autouse=True)
def _clear_services():
    """Ensure clean service registry for each test."""
    _services.clear()
    yield
    _services.clear()


# ── Service registry ─────────────────────────────────────────────────


def test_set_services_populates_registry():
    set_services({"anomaly_detector": "fake"})
    assert _services["anomaly_detector"] == "fake"


def test_all_tools_has_seven_entries():
    assert len(ALL_TOOLS) == 7
    expected = {
        "analyze_node",
        "analyze_fleet",
        "summarize_alerts",
        "summarize_node_alerts",
        "query_fleet",
        "reason_lost_node",
        "recommend_provisioning",
    }
    assert set(ALL_TOOLS.keys()) == expected


# ── Tool: unavailable service → error JSON ────────────────────────────


def test_analyze_node_unavailable():
    result = json.loads(analyze_node_tool("!abc"))
    assert "error" in result


def test_analyze_fleet_unavailable():
    result = json.loads(analyze_fleet_tool())
    assert "error" in result


def test_summarize_alerts_unavailable():
    result = json.loads(summarize_alerts_tool())
    assert "error" in result


def test_summarize_node_alerts_unavailable():
    result = json.loads(summarize_node_alerts_tool("!abc"))
    assert "error" in result


def test_query_fleet_unavailable():
    result = json.loads(query_fleet_tool("how many nodes?"))
    assert "error" in result


def test_reason_lost_node_unavailable():
    result = json.loads(reason_lost_node_tool("!abc"))
    assert "error" in result


def test_recommend_provisioning_unavailable():
    result = json.loads(recommend_provisioning_tool())
    assert "error" in result


# ── Tool: calls correct service method ────────────────────────────────


def test_analyze_node_calls_service():
    mock = MagicMock()
    mock.analyze_node = AsyncMock(return_value={"is_anomalous": True, "node_id": "!x"})
    set_services({"anomaly_detector": mock})

    result = json.loads(analyze_node_tool("!x"))
    mock.analyze_node.assert_awaited_once_with("!x")
    assert result["is_anomalous"] is True


def test_analyze_node_none_result():
    """When analyze_node returns None → not anomalous."""
    mock = MagicMock()
    mock.analyze_node = AsyncMock(return_value=None)
    set_services({"anomaly_detector": mock})

    result = json.loads(analyze_node_tool("!x"))
    assert result["is_anomalous"] is False


def test_analyze_fleet_calls_service():
    mock = MagicMock()
    mock.analyze_fleet = AsyncMock(return_value=[{"node_id": "!a"}])
    set_services({"anomaly_detector": mock})

    result = json.loads(analyze_fleet_tool())
    mock.analyze_fleet.assert_awaited_once()
    assert result["anomaly_count"] == 1


def test_summarize_alerts_calls_service():
    mock = MagicMock()
    mock.summarize_active = AsyncMock(return_value={"total": 5})
    set_services({"alert_summarizer": mock})

    result = json.loads(summarize_alerts_tool())
    mock.summarize_active.assert_awaited_once()
    assert result["total"] == 5


def test_query_fleet_calls_service():
    mock = MagicMock()
    response = SimpleNamespace(answer="3 nodes online", source="sql")
    mock.ask = AsyncMock(return_value=response)
    set_services({"fleet_query_engine": mock})

    result = json.loads(query_fleet_tool("how many nodes?"))
    mock.ask.assert_awaited_once_with("how many nodes?")
    assert result["answer"] == "3 nodes online"


def test_reason_lost_node_calls_service():
    mock = MagicMock()
    mock.reason = AsyncMock(return_value={"location": "last known: Austin"})
    set_services({"lost_node_reasoner": mock})

    result = json.loads(reason_lost_node_tool("!lost"))
    mock.reason.assert_awaited_once_with("!lost")
    assert "location" in result


def test_recommend_provisioning_calls_service():
    mock = MagicMock()
    mock.recommend = AsyncMock(return_value={"nodes": []})
    set_services({"provisioning_advisor": mock})

    result = json.loads(recommend_provisioning_tool("forest", 5, "solar"))
    mock.recommend.assert_awaited_once()
    call_ctx = mock.recommend.call_args[0][0]
    assert call_ctx["terrain"] == "forest"
    assert call_ctx["num_nodes"] == 5
