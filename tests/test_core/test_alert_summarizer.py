"""Tests for alert summarizer — Ollama-powered alert collapse."""

from __future__ import annotations

import pytest

from jenn_mesh.core.alert_summarizer import AlertSummarizer
from jenn_mesh.db import MeshDatabase
from jenn_mesh.models.fleet import ALERT_SEVERITY_MAP, AlertType

# ── Helpers ─────────────────────────────────────────────────────────


def _seed_alerts(db: MeshDatabase, count: int = 3) -> list[int]:
    """Seed N alerts into the DB. Returns alert IDs."""
    alert_ids = []
    types = [
        AlertType.LOW_BATTERY,
        AlertType.SIGNAL_DEGRADED,
        AlertType.ANOMALY_DETECTED,
    ]
    for i in range(count):
        atype = types[i % len(types)]
        severity = ALERT_SEVERITY_MAP[atype].value
        aid = db.create_alert(
            node_id="!aaa11111",
            alert_type=atype.value,
            severity=severity,
            message=f"Test alert {i}",
        )
        alert_ids.append(aid)
    return alert_ids


class MockOllamaClient:
    """Mock Ollama that returns a predetermined summary."""

    def __init__(self, summary_text: str = "AI summary of alerts."):
        self._summary = summary_text

    async def summarize_alerts(self, alerts: list[dict]) -> str | None:
        return self._summary


class FailingOllamaClient:
    """Mock Ollama that always raises."""

    async def summarize_alerts(self, alerts: list[dict]) -> str | None:
        raise ConnectionError("Ollama offline")


# ── Init ────────────────────────────────────────────────────────────


class TestSummarizerInit:
    def test_init_without_ollama(self, populated_db: MeshDatabase):
        s = AlertSummarizer(populated_db)
        assert s._ollama is None
        assert s.db is populated_db

    def test_init_with_ollama(self, populated_db: MeshDatabase):
        mock = MockOllamaClient()
        s = AlertSummarizer(populated_db, ollama=mock)
        assert s._ollama is mock


# ── Summarize Active ────────────────────────────────────────────────


class TestSummarizeActive:
    @pytest.mark.asyncio
    async def test_no_alerts(self, populated_db: MeshDatabase):
        """No alerts → normal operation message."""
        s = AlertSummarizer(populated_db)
        result = await s.summarize_active()
        assert result["alert_count"] == 0
        assert result["source"] == "none"
        assert "normally" in result["summary"].lower()

    @pytest.mark.asyncio
    async def test_rule_based_fallback(self, populated_db: MeshDatabase):
        """Without Ollama, uses rule-based summarization."""
        _seed_alerts(populated_db, 3)
        s = AlertSummarizer(populated_db)
        result = await s.summarize_active()
        assert result["alert_count"] == 3
        assert result["source"] == "rule-based"
        assert "3 active alert" in result["summary"]
        assert "breakdown" in result

    @pytest.mark.asyncio
    async def test_ollama_enrichment(self, populated_db: MeshDatabase):
        """With Ollama, uses AI summary."""
        _seed_alerts(populated_db, 2)
        mock = MockOllamaClient("Critical battery issues detected.")
        s = AlertSummarizer(populated_db, ollama=mock)
        result = await s.summarize_active()
        assert result["alert_count"] == 2
        assert result["source"] == "ollama"
        assert result["summary"] == "Critical battery issues detected."

    @pytest.mark.asyncio
    async def test_ollama_failure_degrades(self, populated_db: MeshDatabase):
        """When Ollama fails, falls back to rule-based."""
        _seed_alerts(populated_db, 2)
        s = AlertSummarizer(populated_db, ollama=FailingOllamaClient())
        result = await s.summarize_active()
        assert result["alert_count"] == 2
        assert result["source"] == "rule-based"

    @pytest.mark.asyncio
    async def test_breakdown_structure(self, populated_db: MeshDatabase):
        """Breakdown dict has expected keys."""
        _seed_alerts(populated_db, 5)
        s = AlertSummarizer(populated_db)
        result = await s.summarize_active()
        bd = result["breakdown"]
        assert "by_type" in bd
        assert "by_severity" in bd
        assert "by_node" in bd


# ── Summarize for Node ──────────────────────────────────────────────


class TestSummarizeForNode:
    @pytest.mark.asyncio
    async def test_node_no_alerts(self, populated_db: MeshDatabase):
        s = AlertSummarizer(populated_db)
        result = await s.summarize_for_node("!aaa11111")
        assert result["alert_count"] == 0
        assert result["node_id"] == "!aaa11111"

    @pytest.mark.asyncio
    async def test_node_rule_based(self, populated_db: MeshDatabase):
        _seed_alerts(populated_db, 2)
        s = AlertSummarizer(populated_db)
        result = await s.summarize_for_node("!aaa11111")
        assert result["alert_count"] == 2
        assert result["source"] == "rule-based"
        assert "!aaa11111" in result["summary"]

    @pytest.mark.asyncio
    async def test_node_with_ollama(self, populated_db: MeshDatabase):
        _seed_alerts(populated_db, 2)
        mock = MockOllamaClient("Node !aaa11111 has battery issues.")
        s = AlertSummarizer(populated_db, ollama=mock)
        result = await s.summarize_for_node("!aaa11111")
        assert result["source"] == "ollama"
        assert result["summary"] == "Node !aaa11111 has battery issues."

    @pytest.mark.asyncio
    async def test_node_ollama_failure(self, populated_db: MeshDatabase):
        _seed_alerts(populated_db, 2)
        s = AlertSummarizer(populated_db, ollama=FailingOllamaClient())
        result = await s.summarize_for_node("!aaa11111")
        assert result["source"] == "rule-based"

    @pytest.mark.asyncio
    async def test_nonexistent_node(self, populated_db: MeshDatabase):
        s = AlertSummarizer(populated_db)
        result = await s.summarize_for_node("!nonexistent")
        assert result["alert_count"] == 0


# ── Status ──────────────────────────────────────────────────────────


class TestSummarizerStatus:
    def test_status_without_ollama(self, populated_db: MeshDatabase):
        s = AlertSummarizer(populated_db)
        status = s.get_status()
        assert status["enabled"] is True
        assert status["ollama_available"] is False
        assert isinstance(status["active_alert_count"], int)

    def test_status_with_ollama(self, populated_db: MeshDatabase):
        s = AlertSummarizer(populated_db, ollama=MockOllamaClient())
        status = s.get_status()
        assert status["ollama_available"] is True


# ── Rule-Based Summary ──────────────────────────────────────────────


class TestRuleBasedSummary:
    def test_single_alert(self, populated_db: MeshDatabase):
        _seed_alerts(populated_db, 1)
        s = AlertSummarizer(populated_db)
        breakdown = s._build_breakdown(populated_db.get_active_alerts())
        summary = s._rule_based_summary(populated_db.get_active_alerts(), breakdown)
        assert "1 active alert" in summary
        assert "1 node" in summary

    def test_multiple_types(self, populated_db: MeshDatabase):
        _seed_alerts(populated_db, 6)
        s = AlertSummarizer(populated_db)
        alerts = populated_db.get_active_alerts()
        breakdown = s._build_breakdown(alerts)
        summary = s._rule_based_summary(alerts, breakdown)
        assert "Top alert types:" in summary

    def test_breakdown_counts(self):
        alerts = [
            {"alert_type": "battery_low", "severity": "warning", "node_id": "!a"},
            {"alert_type": "battery_low", "severity": "warning", "node_id": "!b"},
            {"alert_type": "signal_weak", "severity": "info", "node_id": "!a"},
        ]
        bd = AlertSummarizer._build_breakdown(alerts)
        assert bd["by_type"]["battery_low"] == 2
        assert bd["by_type"]["signal_weak"] == 1
        assert bd["by_severity"]["warning"] == 2
        assert bd["by_severity"]["info"] == 1
        assert bd["by_node"]["!a"] == 2
        assert bd["by_node"]["!b"] == 1
